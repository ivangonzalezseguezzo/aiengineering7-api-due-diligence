"""API REST de producción del sistema de due diligence (FastAPI, totalmente asíncrona).

    uvicorn app.main:app --port 8000          (documentación interactiva en http://localhost:8000/docs)

| Método | Ruta                      | Qué hace                                                            |
|--------|---------------------------|---------------------------------------------------------------------|
| GET    | /health                   | API, Redis y observabilidad                                          |
| POST   | /tasks                    | registra el job en `pending`, lo encola y devuelve el `job_id` (202) |
| GET    | /tasks                    | últimos jobs con su estado                                           |
| GET    | /tasks/{id}               | estado, recomendación, pedido de aprobación, uso, costo y eventos    |
| GET    | /tasks/{id}/informe       | informe en Markdown (preliminar en la pausa, final al cerrar)        |
| GET    | /tasks/{id}/eventos       | progreso nodo a nodo como Server-Sent Events                         |
| POST   | /tasks/{id}/approve       | decisión humana: aprobar / rechazar / otra_vuelta (header X-API-Key) |

La API nunca corre el grafo: solo lee y escribe Redis con el cliente asíncrono (`redis.asyncio`),
así que ningún endpoint bloquea el event loop. El trabajo pesado lo hace el worker (app/worker.py).
"""

import asyncio
import json
import logging
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Security, status
from fastapi.responses import PlainTextResponse, StreamingResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.config import AppSettings, app_settings_from_env
from app.hitl import DecisionInvalida, PedidoAprobacion, validar_pedido
from app.jobs import TERMINALES, Estado, JobStore, TransicionInvalida
from app.observability import configurar_observabilidad
from due_diligence.config import DDSettings, dd_settings_from_env
from due_diligence.corpus import Corpus
from due_diligence.logging_setup import configurar_logging

INTERVALO_EVENTOS_S = 1.0
# Esquema de seguridad declarado: /docs muestra el botón "Authorize" para cargar la clave y aprobar desde ahí.
CLAVE_APROBACION = APIKeyHeader(name="X-API-Key", auto_error=False, description="Clave del comité (API_KEY_APROBACION)")


class CrearTarea(BaseModel):
    startup: str = Field(description="id de la startup (GET /health lista las disponibles)", examples=["vozactiva"])


def _links(job_id: str) -> dict[str, str]:
    return {
        "estado": f"/tasks/{job_id}",
        "informe": f"/tasks/{job_id}/informe",
        "eventos": f"/tasks/{job_id}/eventos",
        "aprobar": f"/tasks/{job_id}/approve",
    }


def crear_app(
    store: JobStore | None = None,
    ajustes: AppSettings | None = None,
    dd: DDSettings | None = None,
) -> FastAPI:
    """Fábrica de la app. Los tests inyectan un `JobStore` sobre un Redis falso; en producción se arma del .env."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        load_dotenv()
        configurar_logging()
        app.state.ajustes = ajustes or app_settings_from_env()
        app.state.dd = dd or dd_settings_from_env()
        app.state.startups = Corpus.desde_directorio(app.state.dd.data_dir).startups
        app.state.observabilidad = configurar_observabilidad()
        redis = None
        if store is None:
            # Un pool de conexiones compartido por todos los requests (no un cliente por request).
            redis = Redis.from_url(app.state.ajustes.redis_url, decode_responses=True, max_connections=20, socket_keepalive=True)
            app.state.store = JobStore(redis, app.state.ajustes.prefijo)
            # Precalentar: abrir conexiones (handshake + AUTH) antes del primer request. Con Redis en la
            # nube, sin esto las primeras peticiones concurrentes pagan la apertura (~1 s en la carga).
            try:
                await asyncio.gather(*(redis.ping() for _ in range(5)))
            except RedisError as exc:
                logging.getLogger(__name__).warning("Redis no respondió al arrancar la API: %s (se reintenta en cada request)", exc)
        else:
            app.state.store = store
        yield
        if redis is not None:
            await redis.aclose()

    app = FastAPI(
        title="Due diligence de startups de IA — API de producción",
        version="7.0.0",
        description="Encola análisis multi-agente, expone su estado y pide aprobación humana antes de emitir la recomendación.",
        lifespan=lifespan,
    )

    def store_de(request: Request) -> JobStore:
        return request.app.state.store

    async def job_o_404(job_id: str, store: JobStore) -> dict[str, Any]:
        job = await store.obtener(job_id)
        if job is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"No existe el job '{job_id}'.")
        return job

    async def exigir_clave(request: Request, x_api_key: str | None = Security(CLAVE_APROBACION)) -> None:
        esperada = request.app.state.ajustes.api_key_aprobacion
        if esperada is None or not esperada.get_secret_value():
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Falta API_KEY_APROBACION en el .env: sin clave no se puede aprobar.")
        if not x_api_key or not secrets.compare_digest(x_api_key, esperada.get_secret_value()):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Header X-API-Key ausente o inválido.")

    @app.get("/health", tags=["sistema"])
    async def health(request: Request) -> Any:
        try:
            redis_ok = await store_de(request).ping()
        except RedisError as exc:
            redis_ok, detalle = False, str(exc)
        cuerpo = {
            "estado": "ok" if redis_ok else "degradado",
            "redis": "ok" if redis_ok else f"sin conexión: {detalle}",
            "observabilidad": request.app.state.observabilidad,
            "startups": request.app.state.startups,
        }
        if not redis_ok:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, cuerpo)
        return cuerpo

    @app.post("/tasks", status_code=status.HTTP_202_ACCEPTED, tags=["tareas"])
    async def crear_tarea(pedido: CrearTarea, request: Request) -> dict[str, Any]:
        startups = request.app.state.startups
        if pedido.startup not in startups:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, f"No existe la startup '{pedido.startup}'. Opciones: {', '.join(startups)}."
            )
        job = await store_de(request).crear(pedido.startup)
        return {"job_id": job["job_id"], "estado": job["estado"], "startup": pedido.startup, "links": _links(job["job_id"])}

    @app.get("/tasks", tags=["tareas"])
    async def listar_tareas(request: Request, limite: int = Query(default=20, ge=1, le=100)) -> list[dict[str, Any]]:
        return await store_de(request).listar(limite)

    @app.get("/tasks/{job_id}", tags=["tareas"])
    async def estado_tarea(job_id: str, request: Request) -> dict[str, Any]:
        store = store_de(request)
        job = await job_o_404(job_id, store)
        return {
            **job,
            "ejecuciones_detalle": await store.ejecuciones(job_id),
            "eventos": [e["texto"] for e in await store.eventos(job_id)],
            "links": _links(job_id),
        }

    @app.get("/tasks/{job_id}/informe", tags=["tareas"], response_class=PlainTextResponse)
    async def informe_tarea(job_id: str, request: Request) -> PlainTextResponse:
        store = store_de(request)
        job = await job_o_404(job_id, store)
        texto = await store.informe(job_id)
        if texto is None:
            raise HTTPException(status.HTTP_409_CONFLICT, f"El informe todavía no está listo (estado: {job['estado']}).")
        return PlainTextResponse(texto, media_type="text/markdown; charset=utf-8")

    @app.get("/tasks/{job_id}/eventos", tags=["tareas"])
    async def eventos_tarea(job_id: str, request: Request) -> StreamingResponse:
        """Server-Sent Events: progreso nodo a nodo y cambios de estado. Termina en la pausa o al cerrar."""
        store = store_de(request)
        await job_o_404(job_id, store)

        async def flujo() -> AsyncIterator[str]:
            leidos, ultimo_estado = 0, None
            while not await request.is_disconnected():
                for evento in await store.eventos(job_id, leidos):
                    leidos += 1
                    yield f"event: progreso\ndata: {json.dumps(evento, ensure_ascii=False)}\n\n"
                job = await store.obtener(job_id)
                if job["estado"] != ultimo_estado:
                    ultimo_estado = job["estado"]
                    yield f"event: estado\ndata: {json.dumps({'estado': ultimo_estado}, ensure_ascii=False)}\n\n"
                if ultimo_estado in TERMINALES or ultimo_estado == Estado.AWAITING_APPROVAL:
                    yield "event: fin\ndata: {}\n\n"
                    return
                await asyncio.sleep(INTERVALO_EVENTOS_S)

        return StreamingResponse(flujo(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @app.post("/tasks/{job_id}/approve", status_code=status.HTTP_202_ACCEPTED, tags=["aprobación humana"], dependencies=[Depends(exigir_clave)])
    async def aprobar_tarea(job_id: str, pedido: PedidoAprobacion, request: Request) -> dict[str, Any]:
        store = store_de(request)
        job = await job_o_404(job_id, store)
        if job["estado"] != Estado.AWAITING_APPROVAL:
            raise HTTPException(status.HTTP_409_CONFLICT, f"El job está en '{job['estado']}': solo se decide en awaiting_approval.")
        try:
            validar_pedido(pedido, (job.get("aprobacion") or {}).get("rondas_pedidas", 0), request.app.state.dd)
        except DecisionInvalida as exc:
            codigo = status.HTTP_409_CONFLICT if pedido.decision == "otra_vuelta" and "vueltas" in str(exc) else status.HTTP_422_UNPROCESSABLE_CONTENT
            raise HTTPException(codigo, str(exc)) from exc
        try:
            await store.registrar_decision(job_id, pedido.model_dump())
        except TransicionInvalida as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, f"Otra decisión llegó antes: el job ya está en '{exc.actual}'.") from exc
        return {
            "job_id": job_id,
            "estado": Estado.PENDING.value,
            "decision": pedido.decision,
            "mensaje": "Decisión registrada: el worker retoma el análisis desde el checkpoint.",
            "links": _links(job_id),
        }

    return app


app = crear_app()
