"""PE7: el worker con el grafo real (LLM guionado) y la cola sobre un Redis falso."""

import asyncio
import contextlib
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from pydantic import Field

from app.config import AppSettings
from app.graph import crear_grafo
from app.jobs import Estado
from app.worker import Worker
from due_diligence.config import DDSettings
from due_diligence.graph import Modelos
from due_diligence.state import serializador

from .conftest import ScriptedChatModel, guion_feliz, modelos_guionados


class ModeloQueSeCuelga(ScriptedChatModel):
    """La primera llamada no vuelve nunca (como un proceso que se corta a mitad de camino)."""

    colgado: bool = False
    llego: Any = Field(default_factory=asyncio.Event)

    async def _agenerate(self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any) -> Any:
        if not self.colgado:
            self.colgado = True
            self.llego.set()
            await asyncio.Event().wait()
        return self._generate(messages)


def armar_worker(
    corpus, store, guion, checkpointer=None, modelos: Modelos | None = None, settings: DDSettings | None = None
) -> Worker:
    grafo, corpus, settings = crear_grafo(
        checkpointer or InMemorySaver(serde=serializador()),
        settings=settings or DDSettings(hitl_modo="siempre"),
        modelos=modelos or modelos_guionados(guion),
        corpus=corpus,
    )
    return Worker(store, grafo, corpus, settings, AppSettings())


async def procesar_siguiente(worker: Worker) -> None:
    tomado = await worker.store.tomar(1)
    assert tomado is not None
    await worker._procesar_y_confirmar(*tomado)


async def test_ciclo_completo_pausa_aprobacion_y_cierre(corpus, store):
    worker = armar_worker(corpus, store, guion_feliz())
    job_id = (await store.crear("vozactiva"))["job_id"]

    await procesar_siguiente(worker)
    job = await store.obtener(job_id)
    assert job["estado"] == Estado.AWAITING_APPROVAL
    assert job["aprobacion"]["recomendacion"] == "NO AVANZAR" and job["recomendacion"] == "NO AVANZAR"
    assert job["llamadas_llm"] == 7 and job["ejecuciones"] == 1
    assert "Recomendación preliminar: NO AVANZAR" in await store.informe(job_id)
    assert await store.redis.llen(store.procesando) == 0  # el mensaje se confirmó
    textos = [e["texto"] for e in await store.eventos(job_id)]
    assert textos[0].startswith("▶ comienza") and textos[-1].startswith("⏸ pausa")

    await store.registrar_decision(job_id, {"decision": "aprobar", "comentario": "", "aprobador": "Ana"})
    await procesar_siguiente(worker)
    job = await store.obtener(job_id)
    assert job["estado"] == Estado.DONE and job["decision_final"] == "APROBADA" and job["aprobacion"] is None
    assert job["ejecuciones"] == 2 and job["llamadas_llm"] == 7  # la reanudación no llamó al LLM
    assert "Ana eligió **aprobar**" in await store.informe(job_id)
    ejecuciones = await store.ejecuciones(job_id)
    assert [e["tipo"] for e in ejecuciones] == ["iniciar", "reanudar"]
    assert ejecuciones[0]["uso_llm"]["por_modelo"] and ejecuciones[0]["costo"]["sin_precio"] == ["desconocido"]


async def test_una_excepcion_del_agente_termina_en_failed(corpus, store):
    guion = guion_feliz()
    guion["investigador"] = [RuntimeError("el proveedor devolvió basura")]
    worker = armar_worker(corpus, store, guion)
    job_id = (await store.crear("vozactiva"))["job_id"]

    await procesar_siguiente(worker)
    job = await store.obtener(job_id)
    assert job["estado"] == Estado.FAILED and "RuntimeError: el proveedor devolvió basura" in job["error"]
    assert (await store.eventos(job_id))[-1]["texto"].startswith("✘")
    assert await store.redis.llen(store.procesando) == 0  # no queda nada colgado: el cliente ve FAILED


async def test_si_el_worker_se_corta_otro_retoma_desde_el_ultimo_checkpoint(corpus, store):
    checkpointer = InMemorySaver(serde=serializador())  # el mismo almacén de checkpoints (en producción, Redis)
    guion = guion_feliz()
    analista = ModeloQueSeCuelga(respuestas=guion.pop("analista"))
    modelos = Modelos(analista=analista, **{rol: ScriptedChatModel(respuestas=r) for rol, r in guion.items()})
    worker = armar_worker(corpus, store, guion, checkpointer, modelos)
    job_id = (await store.crear("vozactiva"))["job_id"]

    tarea = asyncio.create_task(worker._procesar_y_confirmar(*await store.tomar(1)))
    await analista.llego.wait()
    tarea.cancel()  # Ctrl+C o reinicio del contenedor en pleno análisis
    with contextlib.suppress(asyncio.CancelledError):
        await tarea
    assert (await store.obtener(job_id))["estado"] == Estado.RUNNING
    assert await store.redis.llen(store.procesando) == 1  # el mensaje no se confirmó

    # Un worker nuevo (mismo Redis, mismos checkpoints) recupera el mensaje y sigue desde el analista.
    nuevo = Worker(store, worker.grafo, worker.corpus, worker.settings, worker.app_settings)
    assert await store.recuperar_pendientes() == 1
    await procesar_siguiente(nuevo)
    job = await store.obtener(job_id)
    assert job["estado"] == Estado.AWAITING_APPROVAL
    textos = [e["texto"] for e in await store.eventos(job_id)]
    assert any(t.startswith("↻ se retoma desde el último checkpoint (próximo nodo: analista)") for t in textos)
    assert sum(t.startswith("investigador:") for t in textos) == 1  # el investigador no se repitió


async def test_mensaje_duplicado_de_un_job_terminado_se_descarta(corpus, store):
    worker = armar_worker(corpus, store, guion_feliz())
    job_id = (await store.crear("vozactiva"))["job_id"]
    await store.transicion(job_id, {Estado.PENDING}, Estado.FAILED, error="x")
    await procesar_siguiente(worker)
    assert (await store.obtener(job_id))["estado"] == Estado.FAILED
    assert await store.ejecuciones(job_id) == []


async def test_reanudar_sin_checkpoint_falla_en_vez_de_empezar_de_cero(corpus, store):
    worker = armar_worker(corpus, store, guion_feliz())
    job_id = (await store.crear("vozactiva"))["job_id"]
    await store.redis.delete(store.cola)
    await store.transicion(job_id, {Estado.PENDING}, Estado.RUNNING)
    await store.transicion(job_id, {Estado.RUNNING}, Estado.AWAITING_APPROVAL, aprobacion={"rondas_pedidas": 0})
    await store.registrar_decision(job_id, {"decision": "aprobar", "comentario": "", "aprobador": "Ana"})
    await procesar_siguiente(worker)
    job = await store.obtener(job_id)
    assert job["estado"] == Estado.FAILED and "checkpoint del hilo no existe" in job["error"]
    assert worker.grafo.checkpointer.storage == {} or job["llamadas_llm"] == 0  # no se llamó al LLM


async def test_un_descarte_claro_se_emite_sin_pausa(corpus, store):
    """Modo por defecto ("criticidad"): VozActiva da NO AVANZAR con score 41, lejos del umbral 50."""
    worker = armar_worker(corpus, store, guion_feliz(), settings=DDSettings())
    job_id = (await store.crear("vozactiva"))["job_id"]
    await procesar_siguiente(worker)
    job = await store.obtener(job_id)
    assert job["estado"] == Estado.DONE and job["decision_final"] == "EMITIDA SIN PAUSA"
    assert job["criticidad"]["critica"] is False and "descarte claro" in job["criticidad"]["motivos"][0]
    assert "## Emisión (sin pausa humana)" in await store.informe(job_id)
    ejecucion = (await store.ejecuciones(job_id))[0]
    assert {"investigador", "analista", "critico", "triage", "cierre"} <= set(ejecucion["duracion_por_nodo_s"])


async def test_un_corte_de_redis_al_leer_la_cola_no_mata_al_worker(corpus, store, monkeypatch):
    from redis.exceptions import TimeoutError as RedisTimeoutError

    worker = armar_worker(corpus, store, guion_feliz())
    worker.app_settings = AppSettings(worker_espera_cola_s=1)
    detener = asyncio.Event()
    llamadas = []

    async def tomar_que_falla(espera_s):
        llamadas.append(espera_s)
        if len(llamadas) == 1:
            raise RedisTimeoutError("Timeout reading from redis.cloud")
        detener.set()
        return None

    monkeypatch.setattr(store, "tomar", tomar_que_falla)
    await asyncio.wait_for(worker.correr(detener), timeout=10)
    assert len(llamadas) == 2  # después del timeout volvió a leer la cola
