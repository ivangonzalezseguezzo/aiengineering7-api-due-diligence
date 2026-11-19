"""Worker: ejecuta los análisis en segundo plano, lejos del event loop de la API.

    python -m app.worker

Ciclo de un job:  pending ─► running ─┬─(caso crítico)─► awaiting_approval ─(approve)─► pending ─► running ─► done | rejected
                                       ├─(no crítico: emisión automática)──────────────────────────────► done
                                       └─(cualquier excepción)─────────────────────────────────────────► failed

- Escucha la cola de Redis (`JobStore.tomar`, BLMOVE) y corre hasta `WORKER_CONCURRENCIA` jobs a la
  vez en el mismo proceso asyncio. Un solo proceso a propósito: el rate limiter del LLM es por
  proceso y el plan gratuito de Gemini limita las requests por minuto; con varios workers cada
  uno tendría su propio balde y juntos se pasarían del cupo.
- Cada ejecución usa `astream(stream_mode="updates")`: cada nodo que termina se publica como
  evento del job (GET /tasks/{id}/eventos) y la pausa human-in-the-loop llega como `__interrupt__`.
- Toda excepción termina en `failed` con un mensaje legible (sin esto, el cliente haría polling
  para siempre). Si el worker se corta (Ctrl+C, reinicio del contenedor), el mensaje queda en
  `procesando`: el próximo worker lo recupera y el grafo sigue desde el último checkpoint de Redis.
"""

import asyncio
import logging
import sys
import time
from typing import Any

from dotenv import load_dotenv
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from pydantic import ValidationError
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import ResponseError
from redis.exceptions import TimeoutError as RedisTimeoutError
from redisvl.exceptions import RedisSearchError

from app.config import AppSettings, app_settings_from_env
from app.graph import checkpointer_redis, config_hilo, crear_grafo
from app.jobs import TERMINALES, Estado, JobStore, TransicionInvalida, ahora
from app.observability import configurar_observabilidad, ejecucion_trazada, metadata_de_traza, vaciar_trazas
from due_diligence.config import DDSettings, describir_error_config
from due_diligence.corpus import Corpus
from due_diligence.costos import costo_usd
from due_diligence.llm import describir_error_del_proveedor, es_error_del_proveedor
from due_diligence.logging_setup import configurar_logging
from due_diligence.state import estado_inicial
from due_diligence.tracing import ContadorLLM, describir

logger = logging.getLogger("app.worker")

PEDIDO_POR_DEFECTO = (
    "Hacé la due diligence de {nombre} para el comité de inversión: verificá las métricas del data room, "
    "calculá el costo real de los LLM y los unit economics, y marcá los riesgos que la startup no muestra."
)


def describir_excepcion(exc: BaseException) -> str:
    if es_error_del_proveedor(exc):
        return describir_error_del_proveedor(exc)
    return f"{type(exc).__name__}: {str(exc)[:300]}"


class Worker:
    def __init__(self, store: JobStore, grafo: CompiledStateGraph, corpus: Corpus, settings: DDSettings, app_settings: AppSettings) -> None:
        self.store = store
        self.grafo = grafo
        self.corpus = corpus
        self.settings = settings
        self.app_settings = app_settings

    # --- bucle principal -----------------------------------------------------------------------

    async def correr(self, detener: asyncio.Event | None = None) -> None:
        detener = detener or asyncio.Event()
        if recuperados := await self.store.recuperar_pendientes():
            logger.warning("Se recuperaron %d mensajes que un worker anterior no terminó", recuperados)
        cupo = asyncio.Semaphore(self.app_settings.worker_concurrencia)
        en_curso: set[asyncio.Task] = set()
        logger.info("Worker listo: hasta %d jobs simultáneos", self.app_settings.worker_concurrencia)
        try:
            while not detener.is_set():
                await cupo.acquire()
                try:
                    tomado = await self.store.tomar(self.app_settings.worker_espera_cola_s)
                except (RedisConnectionError, RedisTimeoutError) as exc:
                    # Un corte de red con Redis no mata al worker: los mensajes siguen en la cola.
                    cupo.release()
                    logger.warning("Redis no respondió al leer la cola (%s): se reintenta en %d s", exc, self.app_settings.worker_espera_cola_s)
                    await asyncio.sleep(self.app_settings.worker_espera_cola_s)
                    continue
                if tomado is None:
                    cupo.release()
                    continue
                tarea = asyncio.create_task(self._procesar_y_confirmar(*tomado))
                en_curso.add(tarea)
                tarea.add_done_callback(lambda t: (en_curso.discard(t), cupo.release()))
        finally:
            if en_curso:
                await asyncio.gather(*en_curso, return_exceptions=True)

    async def _procesar_y_confirmar(self, crudo: str, mensaje: dict[str, Any]) -> None:
        await self.procesar(mensaje)
        # Solo se confirma si `procesar` terminó (bien o en failed). Si el proceso muere antes,
        # el mensaje sigue en `procesando` y se recupera al reiniciar.
        await self.store.confirmar(crudo)

    # --- un job --------------------------------------------------------------------------------

    async def procesar(self, mensaje: dict[str, Any]) -> None:
        job_id, tipo = mensaje["job_id"], mensaje.get("tipo", "iniciar")
        job = await self.store.obtener(job_id)
        if job is None:
            logger.error("Mensaje de un job inexistente (%s): se descarta", job_id)
            return
        if job["estado"] in TERMINALES or job["estado"] == Estado.AWAITING_APPROVAL:
            logger.warning("Job %s ya está en '%s': mensaje duplicado, se descarta", job_id, job["estado"])
            return
        startup = job["startup"]
        try:
            await self.store.transicion(job_id, {Estado.PENDING, Estado.RUNNING}, Estado.RUNNING, error=None)
        except TransicionInvalida as exc:
            logger.warning("No se toma el job %s: %s", job_id, exc)
            return
        logger.info("Job %s (%s) tomado de la cola: %s -> running", job_id[:8], startup, tipo)

        contador = ContadorLLM()
        config = {
            **config_hilo(job_id, self.settings),
            "callbacks": [contador],
            "run_name": f"grafo:{startup}",
            "tags": ["pe7", f"startup:{startup}", tipo],
            "metadata": metadata_de_traza(job_id, startup, tipo),
        }
        inicio, comienzo = time.perf_counter(), ahora()
        duraciones: dict[str, float] = {}

        async def registrar(error: str | None) -> None:
            # Se registra ANTES de cambiar el estado del job: quien ve "done", "awaiting_approval" o
            # "failed" encuentra el detalle de esta ejecución (uso, costo, latencia) ya guardado.
            uso = contador.resumen()
            await self.store.registrar_ejecucion(
                job_id,
                {
                    "tipo": tipo,
                    "inicio": comienzo,
                    "fin": ahora(),
                    "latencia_s": round(time.perf_counter() - inicio, 2),
                    "duracion_por_nodo_s": duraciones,
                    "uso_llm": uso,
                    "costo": costo_usd(uso["por_modelo"]),
                    "error": error,
                },
            )

        try:
            entrada = await self._entrada(job_id, startup, tipo, mensaje, config)
            duraciones = await self._ejecutar(
                job_id,
                entrada,
                config,
                langsmith_extra={"name": f"due_diligence:{startup}:{tipo}", "metadata": metadata_de_traza(job_id, startup, tipo)},
            )
        except Exception as exc:  # toda falla del análisis termina en FAILED, nunca en un polling eterno
            error = describir_excepcion(exc)
            logger.exception("Job %s falló", job_id)
            await registrar(error)
            await self.store.agregar_evento(job_id, f"✘ {error}")
            try:
                await self.store.transicion(job_id, {Estado.PENDING, Estado.RUNNING}, Estado.FAILED, error=error, terminado_en=ahora())
            except TransicionInvalida:
                logger.error("No se pudo marcar como failed el job %s", job_id)
            return
        except BaseException:
            # Corte del proceso (Ctrl+C, kill): se deja registro de lo consumido; el job sigue en
            # running y el mensaje en `procesando` para que otro worker lo retome desde el checkpoint.
            await registrar("ejecución interrumpida (el worker se detuvo)")
            raise
        await registrar(None)
        try:
            await self._cerrar_ejecucion(job_id, config)
        except Exception as exc:
            error = describir_excepcion(exc)
            logger.exception("Job %s falló al cerrar la ejecución", job_id)
            await self.store.agregar_evento(job_id, f"✘ {error}")
            try:
                await self.store.transicion(job_id, {Estado.PENDING, Estado.RUNNING}, Estado.FAILED, error=error, terminado_en=ahora())
            except TransicionInvalida:
                logger.error("No se pudo marcar como failed el job %s", job_id)

    async def _entrada(self, job_id: str, startup: str, tipo: str, mensaje: dict[str, Any], config: dict) -> Any:
        snapshot = await self.grafo.aget_state(config)
        if snapshot.interrupts:
            if tipo != "reanudar":
                raise RuntimeError("el hilo está en pausa de aprobación pero el mensaje no trae una decisión")
            await self.store.agregar_evento(job_id, f"▶ se reanuda con la decisión humana: {mensaje['decision']['decision']}")
            logger.info("Job %s: se reanuda el hilo desde el checkpoint de Redis con la decisión '%s'", job_id[:8], mensaje["decision"]["decision"])
            return Command(resume=mensaje["decision"])
        if snapshot.next:
            # El hilo quedó a mitad de camino (el worker anterior se cortó): sigue desde el último checkpoint.
            await self.store.agregar_evento(job_id, f"↻ se retoma desde el último checkpoint (próximo nodo: {', '.join(snapshot.next)})")
            logger.warning("Job %s: el hilo quedó a mitad de camino; se retoma desde el último checkpoint (próximo: %s)", job_id[:8], ", ".join(snapshot.next))
            return None
        if tipo == "reanudar":
            # Una decisión humana sin hilo en pausa: el checkpoint se perdió (p. ej., se vació Redis).
            # Empezar de cero en silencio ocultaría el problema y gastaría LLM: se informa como failed.
            raise RuntimeError("no hay un análisis en pausa para reanudar: el checkpoint del hilo no existe")
        nombre = self.corpus.startups[startup]
        await self.store.agregar_evento(job_id, f"▶ comienza la due diligence de {nombre}")
        return estado_inicial(startup, PEDIDO_POR_DEFECTO.format(nombre=nombre))

    @ejecucion_trazada
    async def _ejecutar(self, job_id: str, entrada: Any, config: dict) -> dict[str, float]:
        """Raíz de la traza en LangSmith: una ejecución = una traza con todo el grafo adentro.

        Devuelve los segundos por nodo (el grafo es secuencial: cada actualización llega cuando
        termina un nodo, así que la diferencia con la anterior es lo que tardó ese nodo).
        """
        duraciones: dict[str, float] = {}
        anterior = time.perf_counter()
        async for actualizacion in self.grafo.astream(entrada, config, stream_mode="updates"):
            ahora_s = time.perf_counter()
            for nodo, cambios in actualizacion.items():
                if nodo == "__interrupt__":
                    await self.store.agregar_evento(job_id, "⏸ pausa: la recomendación espera la aprobación del comité")
                    continue
                duraciones[nodo] = round(duraciones.get(nodo, 0) + ahora_s - anterior, 2)
                for linea in describir(nodo, cambios or {}):
                    await self.store.agregar_evento(job_id, linea.strip())
            anterior = ahora_s
        return duraciones

    async def _cerrar_ejecucion(self, job_id: str, config: dict) -> None:
        snapshot = await self.grafo.aget_state(config)
        valores = snapshot.values
        if snapshot.interrupts:
            resumen = snapshot.interrupts[0].value
            if valores.get("informe"):
                await self.store.guardar_informe(job_id, valores["informe"])
            await self.store.transicion(
                job_id,
                {Estado.RUNNING},
                Estado.AWAITING_APPROVAL,
                aprobacion=resumen,
                criticidad=valores["criticidad"].model_dump() if valores.get("criticidad") else None,
                recomendacion=valores.get("recomendacion"),
                rondas_pedidas=resumen.get("rondas_pedidas", 0),
            )
            logger.info(
                "Job %s: %s, caso crítico (%s) -> awaiting_approval; checkpoint en Redis",
                job_id[:8], valores.get("recomendacion"), "; ".join(resumen.get("motivos_pausa", [])),
            )
            return
        if valores.get("decision_final") is None:
            raise RuntimeError(f"el grafo terminó sin decisión final (próximo nodo: {snapshot.next or 'ninguno'})")
        await self.store.guardar_informe(job_id, valores["informe_final"])
        final = Estado.REJECTED if valores["decision_final"] == "RECHAZADA" else Estado.DONE
        await self.store.transicion(
            job_id,
            {Estado.RUNNING},
            final,
            recomendacion=valores.get("recomendacion"),
            decision_final=valores["decision_final"],
            criticidad=valores["criticidad"].model_dump() if valores.get("criticidad") else None,
            aprobacion=None,
            terminado_en=ahora(),
        )
        if valores["decision_final"] == "EMITIDA SIN PAUSA":
            cierre = "emitida sin pausa: " + valores["criticidad"].motivos[0]
        else:
            cierre = f"{valores['decision_final'].lower()} por el comité"
        logger.info("Job %s: %s -> %s (%s)", job_id[:8], valores["recomendacion"], final.value, cierre)
        await self.store.agregar_evento(job_id, f"■ {valores['recomendacion']} — {cierre}")


async def main() -> int:
    load_dotenv()
    configurar_logging(logging.INFO)
    try:
        app_settings = app_settings_from_env()
        from due_diligence.config import dd_settings_from_env

        settings = dd_settings_from_env()
    except ValidationError as exc:
        print(describir_error_config(exc), file=sys.stderr)
        return 2
    configurar_observabilidad()
    # redis-py corta las lecturas a los 5 s por defecto: la espera bloqueante de la cola (BLMOVE)
    # necesita un límite mayor, o con la latencia de un Redis en la nube la lectura vence antes.
    redis = Redis.from_url(
        app_settings.redis_url,
        decode_responses=True,
        socket_timeout=app_settings.worker_espera_cola_s + 10,
        socket_keepalive=True,
        health_check_interval=30,
    )
    try:
        await redis.ping()
        async with checkpointer_redis(app_settings.redis_url) as saver:
            try:
                grafo, corpus, settings = crear_grafo(saver, settings=settings)
            except ValidationError as exc:
                print(describir_error_config(exc), file=sys.stderr)
                return 2
            worker = Worker(JobStore(redis, app_settings.prefijo), grafo, corpus, settings, app_settings)
            await worker.correr()
    except (RedisConnectionError, OSError) as exc:
        print(f"No se pudo conectar a Redis ({app_settings.redis_url}): {exc}. Revisá REDIS_URL o levantá Redis.", file=sys.stderr)
        return 2
    except (RedisSearchError, ResponseError) as exc:
        if "unknown command" not in str(exc).lower():
            raise
        print(
            "Este Redis no tiene Redis Query Engine (FT.*) ni JSON, que usa el checkpointer de LangGraph: "
            "usá Redis 8 (docker compose levanta redis:8) o una base de Redis Cloud. "
            f"Detalle: {str(exc)[:160]}",
            file=sys.stderr,
        )
        return 2
    finally:
        await redis.aclose()
        vaciar_trazas()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("Worker detenido. Los jobs en curso se retoman desde su checkpoint al volver a iniciarlo.")
