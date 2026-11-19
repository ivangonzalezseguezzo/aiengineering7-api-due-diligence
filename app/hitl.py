"""Human-in-the-loop selectivo: los casos críticos esperan la decisión del comité; el resto se emite solo.

    ... validador ──► informe ──► triage ──(no crítico)──────────────────────────► cierre ──► END
                                    │                                               ▲
                                    └──(crítico)──► aprobacion ──(aprobar / rechazar)┘
                                                       │  ▲
                                                       │  └── interrupt(): el grafo se detiene acá
                                                       └──(otra_vuelta)──► supervisor

Qué es crítico (`evaluar_criticidad`, reglas de negocio determinísticas): "HITL no es revisar todo"
(Módulo 7). El sistema automatiza el triage y el descarte de bajo riesgo, y reserva la supervisión
humana para las decisiones que comprometen capital, las zonas de ambigüedad y los casos en los que
el comité ya intervino:
- PAUSA: recomendación positiva (AVANZAR A COMITÉ / PROFUNDIZAR: compromete capital o avanza de
  etapa); score a ± `DD_HITL_MARGEN_AMBIGUEDAD` puntos de un umbral de banda (confianza media);
  validación aprobada con salvedades; o el comité ya pidió otra vuelta (decide el cierre).
- EMISIÓN AUTOMÁTICA: un NO AVANZAR claro (lejos del umbral y sin salvedades: las red flags lo
  refuerzan, no lo contradicen) o una abstención (SIN RECOMENDACIÓN: no hay decisión de capital
  que aprobar; el informe ya deriva a un analista y pide los datos faltantes).
`DD_HITL_MODO=siempre` vuelve a pausar todo (útil para una demo).

El informe ya está armado cuando se decide: el comité decide sobre el informe completo.

Cómo funciona:
- `aprobacion` llama a `interrupt(resumen)`: LangGraph guarda el checkpoint (en Redis) y la
  ejecución termina. El job queda en `awaiting_approval`, sin consumir recursos, el tiempo que
  haga falta: sobrevive a reinicios de la API y del worker porque el estado vive en Redis.
- `POST /tasks/{id}/approve` valida el pedido (`validar_pedido`) y encola la reanudación; el
  worker retoma el hilo con `Command(resume=pedido)` y el nodo recibe la decisión.
- aprobar / rechazar -> `cierre` agrega la decisión al informe. otra_vuelta -> el supervisor
  delega el pedido del comité (máximo `DD_MAX_RONDAS_HUMANAS` vueltas; el comentario es
  obligatorio para rechazar o pedir otra vuelta).

Al reanudar, LangGraph vuelve a ejecutar el nodo desde el principio: por eso el nodo no tiene
efectos antes de `interrupt` (solo arma el resumen, que es determinístico).
"""

from datetime import datetime
from typing import Any, Literal

from langchain_core.messages import AIMessage
from langgraph.types import interrupt
from pydantic import BaseModel, Field

from due_diligence.config import DDSettings
from due_diligence.finanzas import BANDAS
from due_diligence.state import AccionHumana, Aporte, Criticidad, DecisionHumana, EstadoDD, RevisionHumana

ACCIONES: tuple[str, ...] = ("aprobar", "rechazar", "otra_vuelta")
LARGO_MINIMO_COMENTARIO = 5
RECOMENDACIONES_POSITIVAS = ("AVANZAR A COMITÉ", "PROFUNDIZAR")
UMBRALES_DE_BANDA = tuple(minimo for minimo, _ in BANDAS if minimo > 0)  # 70 y 50


def _score(state: EstadoDD) -> float | None:
    analisis = state.get("analisis")
    return (analisis.resultado("calcular_scoring") if analisis else {}).get("score")


def evaluar_criticidad(state: EstadoDD, settings: DDSettings) -> Criticidad:
    """Triage determinístico: ¿esta recomendación necesita al comité? (reglas en el docstring del módulo)."""
    recomendacion = state.get("recomendacion")
    validacion, revision = state.get("validacion"), state.get("revision")
    score = _score(state)
    motivos: list[str] = []
    nivel = "bajo"
    if revision is not None and revision.decisiones:
        motivos.append("el comité ya intervino en este análisis (pidió otra vuelta): decide el cierre")
        nivel = "alto"
    if recomendacion in RECOMENDACIONES_POSITIVAS:
        motivos.append(f"recomendación positiva ({recomendacion}): compromete capital o avanza de etapa")
        nivel = "alto"
    if score is not None and recomendacion != "SIN RECOMENDACIÓN":
        for umbral in UMBRALES_DE_BANDA:
            if abs(score - umbral) <= settings.hitl_margen_ambiguedad:
                motivos.append(f"zona de ambigüedad: score {score:g} a {abs(score - umbral):g} puntos del umbral {umbral}")
                nivel = "alto" if nivel == "alto" else "medio"
    if validacion is not None and validacion.advertencias and recomendacion != "SIN RECOMENDACIÓN":
        nombres = ", ".join(c.nombre for c in validacion.advertencias)
        motivos.append(f"la validación pasó con salvedades ({nombres})")
        nivel = "alto" if nivel == "alto" else "medio"
    if settings.hitl_modo == "siempre" and not motivos:
        motivos.append("DD_HITL_MODO=siempre: toda recomendación espera al comité")
        nivel = "medio"
    if motivos:
        return Criticidad(critica=True, nivel_riesgo=nivel, motivos=motivos)

    if recomendacion == "SIN RECOMENDACIÓN":
        razon = "abstención: no hay una decisión de capital que aprobar; el informe deriva a un analista y pide los datos faltantes"
    else:
        razon = (
            f"descarte claro: {recomendacion} con score {score:g}, a más de {settings.hitl_margen_ambiguedad:g} puntos "
            "de cualquier umbral y con la validación sin salvedades"
        )
    return Criticidad(critica=False, nivel_riesgo="bajo", motivos=[razon])


def crear_nodo_triage(settings: DDSettings):
    async def triage(state: EstadoDD) -> dict[str, Any]:
        criticidad = evaluar_criticidad(state, settings)
        accion = "crítica: espera al comité" if criticidad.critica else "no crítica: emisión automática"
        detalle = f"riesgo {criticidad.nivel_riesgo}; " + "; ".join(criticidad.motivos)
        return {
            "criticidad": criticidad,
            "aportes": [Aporte(paso=state["pasos"], agente="triage", accion=accion, detalle=detalle)],
            "messages": [AIMessage(content=f"Triage — {accion}: {detalle}", name="triage")],
        }

    return triage


def ruta_triage(state: EstadoDD) -> Literal["aprobacion", "cierre"]:
    return "aprobacion" if state["criticidad"].critica else "cierre"


class PedidoAprobacion(BaseModel):
    """Cuerpo de POST /tasks/{id}/approve."""

    decision: AccionHumana = Field(description="aprobar | rechazar | otra_vuelta")
    comentario: str = Field(default="", max_length=2000, description="Obligatorio para rechazar u otra_vuelta")
    aprobador: str = Field(default="comité", min_length=1, max_length=80)


class DecisionInvalida(ValueError):
    """El pedido de aprobación no es válido en el estado actual (la API responde 409 o 422)."""


def validar_pedido(pedido: PedidoAprobacion, rondas_pedidas: int, settings: DDSettings) -> None:
    if pedido.decision in ("rechazar", "otra_vuelta") and len(pedido.comentario.strip()) < LARGO_MINIMO_COMENTARIO:
        raise DecisionInvalida(
            f"Para '{pedido.decision}' el comentario es obligatorio (mínimo {LARGO_MINIMO_COMENTARIO} caracteres): "
            "decí qué hay que revisar o por qué se rechaza."
        )
    if pedido.decision == "otra_vuelta" and rondas_pedidas >= settings.max_rondas_humanas:
        raise DecisionInvalida(
            f"Ya se pidieron {rondas_pedidas} vueltas (máximo {settings.max_rondas_humanas}): aprobá o rechazá."
        )


def acciones_disponibles(rondas_pedidas: int, settings: DDSettings) -> list[str]:
    return [a for a in ACCIONES if a != "otra_vuelta" or rondas_pedidas < settings.max_rondas_humanas]


def resumen_para_aprobar(state: EstadoDD, settings: DDSettings) -> dict[str, Any]:
    """Lo que ve el comité en GET /tasks/{id} mientras el job espera (el informe completo está aparte)."""
    revision = state.get("revision")
    rondas = revision.rondas_pedidas if revision else 0
    analisis, critica = state.get("analisis"), state.get("critica")
    scoring = analisis.resultado("calcular_scoring") if analisis else {}
    motivo = next((a.detalle for a in reversed(state.get("aportes", [])) if a.agente == "informe"), "")
    criticidad = state.get("criticidad") or evaluar_criticidad(state, settings)
    return {
        "pregunta": "¿Se emite esta recomendación de inversión?",
        "startup": state["startup"],
        "recomendacion": state.get("recomendacion"),
        "motivo": motivo,
        "nivel_riesgo": criticidad.nivel_riesgo,
        "motivos_pausa": criticidad.motivos,
        "score": scoring.get("score"),
        "banda": scoring.get("banda"),
        "red_flags_altas": [r.titulo for r in critica.red_flags if r.severidad == "alta"] if critica else [],
        "pausa": (len(revision.decisiones) if revision else 0) + 1,
        "rondas_pedidas": rondas,
        "rondas_restantes": max(0, settings.max_rondas_humanas - rondas),
        "acciones": acciones_disponibles(rondas, settings),
        "comentario_obligatorio_para": ["rechazar", "otra_vuelta"],
    }


def crear_nodo_aprobacion(settings: DDSettings):
    async def aprobacion(state: EstadoDD) -> dict[str, Any]:
        resumen = resumen_para_aprobar(state, settings)
        respuesta = interrupt(resumen)  # pausa: el checkpoint queda en Redis hasta la decisión humana
        pedido = PedidoAprobacion.model_validate(respuesta)
        validar_pedido(pedido, resumen["rondas_pedidas"], settings)  # la API ya validó: defensa en profundidad
        decision = DecisionHumana(
            accion=pedido.decision,
            comentario=pedido.comentario.strip(),
            aprobador=pedido.aprobador.strip(),
            ronda=resumen["pausa"],
            fecha=datetime.now().isoformat(timespec="seconds"),
        )
        previa = state.get("revision") or RevisionHumana()
        detalle = f"{decision.aprobador}: {decision.accion}" + (f" — {decision.comentario}" if decision.comentario else "")
        return {
            "revision": RevisionHumana(decisiones=[*previa.decisiones, decision]),
            "aportes": [Aporte(paso=state["pasos"], agente="aprobacion", accion=f"humano: {decision.accion}", detalle=detalle)],
            "messages": [AIMessage(content=f"Decisión humana: {detalle}", name="aprobacion")],
        }

    return aprobacion


def ruta_aprobacion(state: EstadoDD) -> Literal["supervisor", "cierre"]:
    return "supervisor" if state["revision"].ultima.accion == "otra_vuelta" else "cierre"


def seccion_decision(revision: RevisionHumana, decision_final: str) -> str:
    lineas = ["## Decisión del comité (human-in-the-loop)", f"**{decision_final}**."]
    for d in revision.decisiones:
        comentario = f": {d.comentario}" if d.comentario else ""
        lineas.append(f"- Pausa {d.ronda} ({d.fecha}) — {d.aprobador} eligió **{d.accion}**{comentario}")
    return "\n".join(lineas)


def seccion_emision_automatica(criticidad: Criticidad) -> str:
    return "\n".join(
        [
            "## Emisión (sin pausa humana)",
            "**EMITIDA SIN PAUSA**: el triage no la consideró crítica.",
            *(f"- {motivo}" for motivo in criticidad.motivos),
            "- El comité puede revisar el informe después (human-on-the-loop): no compromete capital.",
        ]
    )


def crear_nodo_cierre():
    async def cierre(state: EstadoDD) -> dict[str, Any]:
        revision, criticidad = state.get("revision"), state.get("criticidad")
        if criticidad is not None and not criticidad.critica:
            decision_final = "EMITIDA SIN PAUSA"
            seccion = seccion_emision_automatica(criticidad)
            detalle = f"recomendación {state.get('recomendacion')} emitida sin pausa (no crítica)"
        else:
            decision_final = "APROBADA" if revision.ultima.accion == "aprobar" else "RECHAZADA"
            seccion = seccion_decision(revision, decision_final)
            detalle = f"recomendación {state.get('recomendacion')} {decision_final.lower()} por el comité"
        informe_final = (state.get("informe") or "").rstrip() + "\n\n" + seccion + "\n"
        return {
            "decision_final": decision_final,
            "informe_final": informe_final,
            "aportes": [Aporte(paso=state["pasos"], agente="cierre", accion="decisión final", detalle=detalle)],
            "messages": [AIMessage(content=detalle, name="cierre")],
        }

    return cierre
