"""Grafo del orquestador: topología jerárquica con un supervisor que rutea y una compuerta de validación.

    START ──► supervisor ──(Literal)──► investigador ──┐
                 ▲  │                ├► analista ─────┤
                 │  │                └► critico ──────┤
                 │  │                                 │
                 │  └──(finalizar)──► validador       │
                 │                       │            │
                 ├──────(rechazada)──────┘            │
                 └────────────────────────────────────┘
                              validador ──(aprobada o sin pasos)──► informe ──► END

- `supervisor` decide con salida estructurada; `ruta_supervisor` mapea esa decisión al nodo
  (`Literal` en el retorno: LangGraph dibuja las aristas a partir de esa anotación).
- Cada especialista vuelve SIEMPRE al supervisor: no se hablan entre ellos.
- `validador` aplica la rúbrica antes del END; si rechaza y quedan pasos, vuelve al supervisor.
- `informe` sintetiza sin LLM a partir de las parcelas validadas.

Aislamiento: cada especialista lee solo su `input_schema` (state.py) y `con_parcela` verifica
que cada nodo escriba únicamente las claves de su parcela (`PARCELAS`).
"""

import functools
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from due_diligence.agents.analyst_agent import crear_nodo_analista
from due_diligence.agents.critic_agent import crear_nodo_critico
from due_diligence.agents.research_agent import crear_nodo_investigador
from due_diligence.agents.supervisor import crear_nodo_supervisor
from due_diligence.busqueda_web import BuscadorWeb
from due_diligence.config import DDSettings
from due_diligence.corpus import Corpus
from due_diligence.informe import crear_nodo_informe
from due_diligence.state import PARCELAS, EntradaAnalista, EntradaCritico, EntradaInvestigador, EstadoDD
from due_diligence.validation import crear_nodo_validador, crear_ruta_validador


class EscrituraFueraDeParcela(RuntimeError):
    """Un nodo intentó escribir una clave del estado que no le pertenece."""


@dataclass(frozen=True)
class Modelos:
    """Un chat model por rol. Pueden ser el mismo objeto (y comparten el rate limiter de llm.py)."""

    supervisor: BaseChatModel
    investigador: BaseChatModel
    analista: BaseChatModel
    critico: BaseChatModel

    @classmethod
    def mismo(cls, modelo: BaseChatModel, supervisor: BaseChatModel | None = None) -> "Modelos":
        return cls(supervisor=supervisor or modelo, investigador=modelo, analista=modelo, critico=modelo)


def con_parcela(nombre: str, nodo: Callable[[Any], Awaitable[dict]]) -> Callable[[Any], Awaitable[dict]]:
    """Envuelve un nodo y rechaza cualquier escritura fuera de su parcela (aislamiento de escritura)."""
    permitidas = PARCELAS[nombre]

    @functools.wraps(nodo)
    async def nodo_aislado(state: Any) -> dict:
        cambios = await nodo(state)
        if invadidas := set(cambios) - permitidas:
            raise EscrituraFueraDeParcela(f"el nodo '{nombre}' intentó escribir {sorted(invadidas)}, fuera de su parcela")
        return cambios

    return nodo_aislado


def ruta_supervisor(state: EstadoDD) -> Literal["investigador", "analista", "critico", "validador"]:
    """Arista condicional del supervisor: su decisión ya es uno de los nombres de nodo (o 'finalizar')."""
    destino = state["siguiente"]
    return "validador" if destino == "finalizar" else destino


def construir_workflow(
    modelos: Modelos,
    corpus: Corpus,
    settings: DDSettings,
    *,
    buscador_web: BuscadorWeb | None = None,
    reintentos_llm: int = 0,
) -> StateGraph:
    """El grafo de la PE6 SIN compilar y sin la arista final `informe -> END`.

    La PE7 (app/graph.py) le agrega la pausa human-in-the-loop después del informe; `build_graph`
    lo cierra como en la PE6.
    """
    comunes = {"reintentos_llm": reintentos_llm}
    nodos = {
        "supervisor": crear_nodo_supervisor(modelos.supervisor, corpus, settings, **comunes),
        "investigador": crear_nodo_investigador(modelos.investigador, corpus, settings, buscador_web=buscador_web, **comunes),
        "analista": crear_nodo_analista(modelos.analista, corpus, settings, **comunes),
        "critico": crear_nodo_critico(modelos.critico, corpus, settings, **comunes),
        "validador": crear_nodo_validador(settings),
        "informe": crear_nodo_informe(corpus),
    }
    lecturas = {"investigador": EntradaInvestigador, "analista": EntradaAnalista, "critico": EntradaCritico}

    workflow = StateGraph(EstadoDD)
    for nombre, nodo in nodos.items():
        workflow.add_node(nombre, con_parcela(nombre, nodo), input_schema=lecturas.get(nombre))

    workflow.add_edge(START, "supervisor")
    workflow.add_conditional_edges("supervisor", ruta_supervisor)
    for especialista in lecturas:
        workflow.add_edge(especialista, "supervisor")
    workflow.add_conditional_edges("validador", crear_ruta_validador(settings))
    return workflow


def build_graph(
    modelos: Modelos,
    corpus: Corpus,
    settings: DDSettings,
    *,
    buscador_web: BuscadorWeb | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
    reintentos_llm: int = 0,
) -> CompiledStateGraph:
    """Grafo de la PE6: termina en el informe, sin aprobación humana."""
    workflow = construir_workflow(modelos, corpus, settings, buscador_web=buscador_web, reintentos_llm=reintentos_llm)
    workflow.add_edge("informe", END)
    return workflow.compile(checkpointer=checkpointer, name="due-diligence-ia")
