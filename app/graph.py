"""El orquestador multi-agente de la PE6 en producción: checkpointer de Redis + pausa human-in-the-loop.

    START ─► supervisor ⇄ investigador / analista / crítico ─► validador ─► informe ─► triage ─┬─────────────► cierre ─► END
                 ▲                                                                     (crítico) └► aprobacion ─┘
                 └──────────────────────────────── otra_vuelta ─────────────────────────────────────┘ (interrupt)

- El grafo de la PE6 se reutiliza (`due_diligence.graph.construir_workflow`) y se le agregan los
  nodos `triage`, `aprobacion` y `cierre` (app/hitl.py), con el mismo aislamiento por parcelas.
- `AsyncRedisSaver` (langgraph-checkpoint-redis) guarda un checkpoint por super-paso en Redis.
  El `thread_id` es el `job_id`: el mismo hilo sigue después de la aprobación, después de un
  reinicio del worker o de la API, y un worker que se cae a mitad de camino retoma desde el último
  checkpoint (ver worker.py).
- Requiere Redis 8 o Redis Stack: el saver crea índices con Redis Query Engine y guarda JSON.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END
from langgraph.graph.state import CompiledStateGraph

from app.hitl import crear_nodo_aprobacion, crear_nodo_cierre, crear_nodo_triage, ruta_aprobacion, ruta_triage
from due_diligence.busqueda_web import buscador_desde_entorno
from due_diligence.config import DDSettings, dd_settings_from_env
from due_diligence.corpus import Corpus
from due_diligence.graph import Modelos, con_parcela, construir_workflow
from due_diligence.llm import get_chat_model, get_chat_model_con_respaldo, settings_from_env
from due_diligence.state import serializador_redis


def crear_grafo(
    checkpointer: BaseCheckpointSaver,
    *,
    settings: DDSettings | None = None,
    modelos: Modelos | None = None,
    corpus: Corpus | None = None,
) -> tuple[CompiledStateGraph, Corpus, DDSettings]:
    """Grafo compilado con checkpointer y aprobación humana.

    Sin `modelos`, los arma desde el .env (lanza ValidationError si falta la API key): los
    especialistas con el modelo de respaldo; el supervisor con su modelo propio si se configuró.
    """
    settings = settings or dd_settings_from_env()
    corpus = corpus or Corpus.desde_directorio(settings.data_dir)
    reintentos = 0
    if modelos is None:
        llm = settings_from_env()
        especialistas = get_chat_model_con_respaldo()
        supervisor = get_chat_model(model=settings.modelo_supervisor) if settings.modelo_supervisor else None
        modelos, reintentos = Modelos.mismo(especialistas, supervisor), llm.max_retries

    workflow = construir_workflow(modelos, corpus, settings, buscador_web=buscador_desde_entorno(), reintentos_llm=reintentos)
    workflow.add_node("triage", con_parcela("triage", crear_nodo_triage(settings)))
    workflow.add_node("aprobacion", con_parcela("aprobacion", crear_nodo_aprobacion(settings)))
    workflow.add_node("cierre", con_parcela("cierre", crear_nodo_cierre()))
    workflow.add_edge("informe", "triage")
    workflow.add_conditional_edges("triage", ruta_triage)
    workflow.add_conditional_edges("aprobacion", ruta_aprobacion)
    workflow.add_edge("cierre", END)
    return workflow.compile(checkpointer=checkpointer, name="due-diligence-produccion"), corpus, settings


@asynccontextmanager
async def checkpointer_redis(redis_url: str) -> AsyncIterator[BaseCheckpointSaver]:
    """`AsyncRedisSaver` listo para usar (crea los índices la primera vez) con el serializador del estado."""
    from langgraph.checkpoint.redis.aio import AsyncRedisSaver

    async with AsyncRedisSaver.from_conn_string(redis_url) as saver:
        # Sin la lista permitida, los contratos Pydantic del estado vuelven como dict (ver state.serializador_redis).
        saver.serde = serializador_redis()
        yield saver


def config_hilo(job_id: str, settings: DDSettings) -> RunnableConfig:
    """thread_id = job_id; recursion_limit es el techo duro de super-pasos de cada ejecución."""
    return {"configurable": {"thread_id": job_id}, "recursion_limit": settings.recursion_limit}
