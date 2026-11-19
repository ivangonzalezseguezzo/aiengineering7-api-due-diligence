"""Estado de los jobs y cola de trabajo en Redis (la API escribe, el worker consume).

Claves (prefijo configurable, por defecto "dd"):
- `dd:job:{id}`              hash con el estado del job (estado, fechas, error, recomendación, uso, costo)
- `dd:jobs`                  sorted set por fecha de creación (GET /tasks)
- `dd:cola`                  lista FIFO de mensajes {"job_id", "tipo": iniciar | reanudar, "decision"?}
- `dd:procesando`            mensajes tomados por un worker y todavía no terminados (cola confiable)
- `dd:job:{id}:eventos`      progreso nodo a nodo (lo lee GET /tasks/{id}/eventos)
- `dd:job:{id}:informe`      informe en Markdown (preliminar en la pausa; final al cerrar)
- `dd:job:{id}:ejecuciones`  uso, costo y latencia de cada ejecución (iniciar y cada reanudación)

Los checkpoints de LangGraph los guarda `AsyncRedisSaver` en el mismo Redis, con sus propias claves.

Garantías:
- `crear` registra el job en `pending` y lo encola en la MISMA transacción (MULTI): nunca hay un
  mensaje en la cola de un job que todavía no existe (el "404 fantasma").
- `transicion` cambia de estado con WATCH/MULTI: si dos aprobaciones llegan juntas, solo una pasa.
- Cola confiable: `tomar` mueve el mensaje a `procesando` con BLMOVE y `confirmar` lo borra al
  terminar. Si el worker se cae a mitad de camino, el mensaje sigue en `procesando` y el próximo
  worker lo devuelve a la cola al arrancar (`recuperar_pendientes`).
"""

import json
import time
import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import WatchError


class Estado(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    DONE = "done"
    REJECTED = "rejected"
    FAILED = "failed"


TERMINALES = frozenset({Estado.DONE, Estado.REJECTED, Estado.FAILED})
CAMPOS_JSON = frozenset({"aprobacion", "decision", "criticidad"})
CAMPOS_ENTEROS = frozenset({"llamadas_llm", "tokens_entrada", "tokens_salida", "ejecuciones", "rondas_pedidas"})
CAMPOS_DECIMALES = frozenset({"costo_usd", "latencia_total_s"})


class JobNoEncontrado(KeyError):
    pass


class TransicionInvalida(Exception):
    def __init__(self, job_id: str, actual: str, hacia: str) -> None:
        super().__init__(f"el job {job_id} está en '{actual}' y no puede pasar a '{hacia}'")
        self.actual = actual


def ahora() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _a_redis(valor: Any) -> str:
    if valor is None:
        return ""
    if isinstance(valor, (dict, list)):
        return json.dumps(valor, ensure_ascii=False)
    return str(valor)


def _desde_redis(campo: str, valor: str) -> Any:
    if valor == "":
        return None
    if campo in CAMPOS_JSON:
        return json.loads(valor)
    if campo in CAMPOS_ENTEROS:
        return int(valor)
    if campo in CAMPOS_DECIMALES:
        return float(valor)
    return valor


class JobStore:
    def __init__(self, redis: Redis, prefijo: str = "dd") -> None:
        self.redis = redis
        self.prefijo = prefijo
        self.indice = f"{prefijo}:jobs"
        self.cola = f"{prefijo}:cola"
        self.procesando = f"{prefijo}:procesando"

    def _job(self, job_id: str) -> str:
        return f"{self.prefijo}:job:{job_id}"

    # --- API ---------------------------------------------------------------------------------

    async def ping(self) -> bool:
        return bool(await self.redis.ping())

    async def crear(self, startup: str) -> dict[str, Any]:
        job_id = uuid.uuid4().hex
        momento = ahora()
        datos = {
            "job_id": job_id,
            "startup": startup,
            "estado": Estado.PENDING.value,
            "creado_en": momento,
            "actualizado_en": momento,
            "rondas_pedidas": 0,
            "llamadas_llm": 0,
            "tokens_entrada": 0,
            "tokens_salida": 0,
            "costo_usd": 0,
            "latencia_total_s": 0,
            "ejecuciones": 0,
        }
        mensaje = json.dumps({"job_id": job_id, "tipo": "iniciar"})
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.hset(self._job(job_id), mapping={k: _a_redis(v) for k, v in datos.items()})
            pipe.zadd(self.indice, {job_id: time.time()})
            pipe.rpush(self.cola, mensaje)  # después del estado y en la misma transacción
            await pipe.execute()
        return datos

    async def obtener(self, job_id: str) -> dict[str, Any] | None:
        crudo = await self.redis.hgetall(self._job(job_id))
        return {campo: _desde_redis(campo, valor) for campo, valor in crudo.items()} or None

    async def listar(self, limite: int = 20) -> list[dict[str, Any]]:
        ids = await self.redis.zrevrange(self.indice, 0, limite - 1)
        async with self.redis.pipeline(transaction=False) as pipe:
            for job_id in ids:
                pipe.hgetall(self._job(job_id))
            crudos = await pipe.execute()
        return [{c: _desde_redis(c, v) for c, v in crudo.items()} for crudo in crudos if crudo]

    async def transicion(
        self,
        job_id: str,
        desde: set[Estado] | frozenset[Estado],
        hacia: Estado,
        *,
        encolar: dict[str, Any] | None = None,
        **campos: Any,
    ) -> None:
        """Cambia de estado solo si el actual está en `desde` (atómico con WATCH/MULTI)."""
        clave = self._job(job_id)
        valores = {k: _a_redis(v) for k, v in campos.items()} | {"estado": hacia.value, "actualizado_en": ahora()}
        async with self.redis.pipeline(transaction=True) as pipe:
            while True:
                try:
                    await pipe.watch(clave)
                    actual = await pipe.hget(clave, "estado")
                    if actual is None:
                        raise JobNoEncontrado(job_id)
                    if actual not in {e.value for e in desde}:
                        raise TransicionInvalida(job_id, actual, hacia.value)
                    pipe.multi()
                    pipe.hset(clave, mapping=valores)
                    if encolar is not None:
                        pipe.rpush(self.cola, json.dumps(encolar, ensure_ascii=False))
                    await pipe.execute()
                    return
                except WatchError:
                    continue  # otro proceso cambió el job en el medio: se vuelve a leer

    async def registrar_decision(self, job_id: str, decision: dict[str, Any]) -> None:
        """awaiting_approval -> pending y encola la reanudación (lo usa POST /tasks/{id}/approve)."""
        await self.transicion(
            job_id,
            {Estado.AWAITING_APPROVAL},
            Estado.PENDING,
            encolar={"job_id": job_id, "tipo": "reanudar", "decision": decision},
            decision=decision,
        )

    async def eventos(self, job_id: str, desde: int = 0) -> list[dict[str, Any]]:
        return [json.loads(e) for e in await self.redis.lrange(f"{self._job(job_id)}:eventos", desde, -1)]

    async def informe(self, job_id: str) -> str | None:
        return await self.redis.get(f"{self._job(job_id)}:informe")

    async def ejecuciones(self, job_id: str) -> list[dict[str, Any]]:
        return [json.loads(e) for e in await self.redis.lrange(f"{self._job(job_id)}:ejecuciones", 0, -1)]

    # --- Worker ------------------------------------------------------------------------------

    async def tomar(self, espera_s: int) -> tuple[str, dict[str, Any]] | None:
        """Próximo mensaje de la cola (bloquea hasta `espera_s` segundos); queda en `procesando`."""
        crudo = await self.redis.blmove(self.cola, self.procesando, espera_s, "LEFT", "RIGHT")
        return (crudo, json.loads(crudo)) if crudo else None

    async def confirmar(self, crudo: str) -> None:
        await self.redis.lrem(self.procesando, 1, crudo)

    async def recuperar_pendientes(self) -> int:
        """Devuelve a la cola los mensajes que un worker anterior tomó y no terminó."""
        recuperados = 0
        while await self.redis.lmove(self.procesando, self.cola, "RIGHT", "LEFT"):
            recuperados += 1
        return recuperados

    async def agregar_evento(self, job_id: str, texto: str) -> None:
        await self.redis.rpush(f"{self._job(job_id)}:eventos", json.dumps({"ts": ahora(), "texto": texto}, ensure_ascii=False))

    async def guardar_informe(self, job_id: str, texto: str) -> None:
        await self.redis.set(f"{self._job(job_id)}:informe", texto)

    async def registrar_ejecucion(self, job_id: str, ejecucion: dict[str, Any]) -> None:
        """Suma el uso de una ejecución a los totales del job y la guarda en el historial."""
        clave = self._job(job_id)
        uso = ejecucion["uso_llm"]["total"]
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.rpush(f"{clave}:ejecuciones", json.dumps(ejecucion, ensure_ascii=False))
            pipe.hincrby(clave, "ejecuciones", 1)
            pipe.hincrby(clave, "llamadas_llm", uso["llamadas"])
            pipe.hincrby(clave, "tokens_entrada", uso["tokens_entrada"])
            pipe.hincrby(clave, "tokens_salida", uso["tokens_salida"])
            pipe.hincrbyfloat(clave, "costo_usd", ejecucion["costo"]["total_usd"])
            pipe.hincrbyfloat(clave, "latencia_total_s", ejecucion["latencia_s"])
            await pipe.execute()
