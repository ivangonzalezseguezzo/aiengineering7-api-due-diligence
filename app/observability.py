"""Observabilidad con LangSmith: trazas de cada ejecución, con costo y latencia.

Cómo se instrumenta:
- LangChain y LangGraph envían solos cada nodo, cada llamada al LLM (con tokens) y cada
  herramienta a LangSmith cuando están `LANGSMITH_TRACING=true` y `LANGSMITH_API_KEY`.
- `@traceable` (abajo, `ejecucion_trazada`) decora la ejecución de un job en el worker: es la
  raíz de la traza, así cada ejecución queda como UNA traza con todo el grafo adentro y LangSmith
  suma el costo y la latencia de la ejecución completa.
- `metadata_de_traza` agrega `job_id`, `thread_id` (LangSmith agrupa en "Threads" las ejecuciones
  del mismo análisis: la primera hasta la pausa y la que sigue después de la aprobación),
  `startup` y `tipo` (iniciar / reanudar) para filtrar en el dashboard.

Sin clave, el sistema funciona igual y solo avisa en el log que no hay trazas.
"""

import logging
import os
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from langsmith import traceable

logger = logging.getLogger(__name__)

PROYECTO_POR_DEFECTO = "due-diligence-pe7-produccion"
T = TypeVar("T")


def _activado(valor: str | None) -> bool:
    return (valor or "").strip().lower() in {"1", "true", "yes", "si", "sí"}


def configurar_observabilidad() -> dict[str, Any]:
    """Valida la configuración de LangSmith y la deja lista. Devuelve el estado para /health y el log."""
    pedido = _activado(os.getenv("LANGSMITH_TRACING")) or _activado(os.getenv("LANGCHAIN_TRACING_V2"))
    clave = os.getenv("LANGSMITH_API_KEY") or os.getenv("LANGCHAIN_API_KEY")
    if not pedido:
        logger.warning("Observabilidad desactivada: definí LANGSMITH_TRACING=true y LANGSMITH_API_KEY en el .env")
        return {"plataforma": "langsmith", "activa": False, "motivo": "LANGSMITH_TRACING no está en true"}
    if not clave:
        os.environ["LANGSMITH_TRACING"] = "false"
        logger.warning("LANGSMITH_TRACING=true pero falta LANGSMITH_API_KEY: las trazas quedan desactivadas")
        return {"plataforma": "langsmith", "activa": False, "motivo": "falta LANGSMITH_API_KEY"}
    proyecto = os.environ.setdefault("LANGSMITH_PROJECT", PROYECTO_POR_DEFECTO)
    logger.info("Trazas activas en LangSmith, proyecto '%s'", proyecto)
    return {"plataforma": "langsmith", "activa": True, "proyecto": proyecto}


def metadata_de_traza(job_id: str, startup: str, tipo: str) -> dict[str, Any]:
    return {"job_id": job_id, "thread_id": job_id, "startup": startup, "tipo": tipo}


def ejecucion_trazada(funcion: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
    """Decorador de la ejecución de un job: raíz de la traza en LangSmith (no hace nada sin tracing)."""
    # Solo el job_id como entrada de la raíz: el estado inicial y la config (con callbacks) no aportan
    # al leer la traza; el detalle de cada nodo y de cada llamada al LLM está en las trazas hijas.
    return traceable(
        run_type="chain",
        name="ejecucion_due_diligence",
        process_inputs=lambda entradas: {"job_id": entradas.get("job_id")},
    )(funcion)


def vaciar_trazas() -> None:
    """Espera a que se envíen las trazas pendientes (al apagar el worker)."""
    try:
        from langchain_core.tracers.langchain import wait_for_all_tracers

        wait_for_all_tracers()
    except Exception as exc:  # nunca bloquear el apagado por la observabilidad
        logger.warning("No se pudieron vaciar las trazas pendientes: %s", exc)
