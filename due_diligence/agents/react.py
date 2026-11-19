"""Agente ReAct de cada especialista: el patrón de la pre-entrega 5, ahora reutilizable por rol.

    START ──► agente ──tools_condition──► herramientas ──┐
                ▲   └──(sin tool_calls)──► END            │
                └─────────────────────────────────────────┘

Es lo que hace `create_react_agent`, que LangGraph 1.0 marcó como deprecado en favor de
`langchain.agents.create_agent`. Se arma a mano (como en la PE5) para controlar lo que el
orquestador necesita: la política de reintentos (`llm.es_reintentable`), un freno blando de
rondas de herramientas por invocación y el system prompt propio de cada rol.

Cada especialista se invoca con una lista de mensajes NUEVA (instrucción del supervisor + el
contexto mínimo que necesita) y se compila con `checkpointer=False`: su conversación interna con
las herramientas no se mezcla con el estado compartido (evita la contaminación de contexto).
"""

import logging
from collections.abc import Sequence

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from due_diligence.llm import es_reintentable

logger = logging.getLogger(__name__)

INSTRUCCION_LIMITE = (
    "Alcanzaste el máximo de rondas de herramientas para esta tarea. No llames más herramientas: "
    "respondé con un resumen de lo que hiciste y aclará qué quedó pendiente."
)
RESPUESTA_LIMITE = "Alcancé el máximo de rondas de herramientas sin cerrar la tarea; lo registrado hasta acá queda en el estado."


class EstadoReAct(MessagesState):
    iteraciones: int


def crear_agente_react(
    model: BaseChatModel,
    tools: Sequence[BaseTool],
    system_prompt: str,
    *,
    nombre: str,
    max_iteraciones: int = 4,
    reintentos_llm: int = 0,
) -> CompiledStateGraph:
    # Sin tool_choice forzado: el modelo decide (los Claude 5.5 rechazan forzarlo).
    modelo: Runnable = model.bind_tools(tools)
    if reintentos_llm:
        # Solo errores transitorios; nunca la cuota diaria ni errores permanentes (ver llm.es_reintentable).
        modelo = modelo.with_retry(
            retry_if_exception_type=es_reintentable, stop_after_attempt=reintentos_llm + 1, wait_exponential_jitter=True
        )

    async def agente(state: EstadoReAct) -> dict:
        iteracion = state.get("iteraciones", 0) + 1
        prompt = [SystemMessage(system_prompt), *state["messages"]]
        if iteracion > max_iteraciones:
            respuesta = await modelo.ainvoke([*prompt, SystemMessage(INSTRUCCION_LIMITE)])
            if respuesta.tool_calls:
                logger.warning("%s insistió en usar herramientas tras %d rondas: se corta", nombre, max_iteraciones)
                respuesta = AIMessage(content=RESPUESTA_LIMITE)
        else:
            respuesta = await modelo.ainvoke(prompt)
        if respuesta.tool_calls:
            logger.info("%s, ronda %d: %s", nombre, iteracion, ", ".join(c["name"] for c in respuesta.tool_calls))
        return {"messages": [respuesta], "iteraciones": iteracion}

    workflow = StateGraph(EstadoReAct)
    workflow.add_node("agente", agente)
    # handle_tool_errors=True: argumentos inválidos vuelven como ToolMessage de error y el agente se corrige.
    workflow.add_node("herramientas", ToolNode(tools, handle_tool_errors=True))
    workflow.add_edge(START, "agente")
    workflow.add_conditional_edges("agente", tools_condition, {"tools": "herramientas", END: END})
    workflow.add_edge("herramientas", "agente")
    return workflow.compile(checkpointer=False, name=nombre)


async def ejecutar_especialista(agente: CompiledStateGraph, mensaje: str, *, nombre: str, max_iteraciones: int) -> list[BaseMessage]:
    """Corre el ciclo ReAct con un historial nuevo y devuelve los mensajes generados."""
    config = {"recursion_limit": 2 * max_iteraciones + 3, "tags": [f"agente:{nombre}"], "run_name": nombre}
    resultado = await agente.ainvoke({"messages": [HumanMessage(mensaje)], "iteraciones": 0}, config)
    return resultado["messages"]


def texto_final(mensajes: Sequence[BaseMessage]) -> str:
    """Último mensaje del agente sin tool_calls: su resumen para el supervisor."""
    for mensaje in reversed(mensajes):
        if isinstance(mensaje, AIMessage) and not mensaje.tool_calls and mensaje.text.strip():
            return mensaje.text.strip()
    return ""
