"""PE7: pausa human-in-the-loop antes de emitir la recomendación (interrupt + Command(resume=...))."""

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.graph import crear_grafo
from app.hitl import DecisionInvalida, PedidoAprobacion, evaluar_criticidad, validar_pedido
from due_diligence.config import DDSettings
from due_diligence.state import Check, DecisionHumana, RevisionHumana, Validacion, estado_inicial, serializador

from .conftest import CALCULOS_COMPLETOS, analisis_con, decision, estado, guion_feliz, modelos_guionados, respuesta

SIEMPRE = DDSettings(hitl_modo="siempre")  # VozActiva es un descarte claro: en modo "criticidad" no pausa


def armar(corpus, guion, settings: DDSettings = SIEMPRE):
    modelos = modelos_guionados(guion)
    grafo, _, _ = crear_grafo(InMemorySaver(serde=serializador()), settings=settings, modelos=modelos, corpus=corpus)
    return grafo, modelos


def cfg(hilo: str) -> dict:
    return {"configurable": {"thread_id": hilo}, "recursion_limit": 40}


async def test_la_recomendacion_espera_la_decision_humana_y_se_aprueba(corpus):
    grafo, _ = armar(corpus, guion_feliz())
    await grafo.ainvoke(estado_inicial("vozactiva", "DD de VozActiva."), cfg("a"))

    pausa = await grafo.aget_state(cfg("a"))
    assert pausa.next == ("aprobacion",)
    resumen = pausa.interrupts[0].value
    assert resumen["nivel_riesgo"] == "medio" and "DD_HITL_MODO=siempre" in resumen["motivos_pausa"][0]
    assert resumen["recomendacion"] == "NO AVANZAR" and resumen["score"] == 41
    assert resumen["acciones"] == ["aprobar", "rechazar", "otra_vuelta"] and resumen["rondas_restantes"] == 2
    assert pausa.values["informe"] and pausa.values["decision_final"] is None  # nada se emitió todavía

    await grafo.ainvoke(Command(resume={"decision": "aprobar", "aprobador": "Ana (comité)"}), cfg("a"))
    final = await grafo.aget_state(cfg("a"))
    assert final.next == () and final.values["decision_final"] == "APROBADA"
    assert "## Decisión del comité (human-in-the-loop)" in final.values["informe_final"]
    assert "Ana (comité) eligió **aprobar**" in final.values["informe_final"]


async def test_otra_vuelta_vuelve_al_supervisor_con_el_pedido_y_despues_se_rechaza(corpus):
    guion = guion_feliz(
        supervisor=[decision("critico", "Re-auditá la concentración de clientes.")],
        critico_extra=[respuesta("La concentración en TelcoAndina es del 46 %: riesgo alto.")],
    )
    grafo, modelos = armar(corpus, guion)
    await grafo.ainvoke(estado_inicial("vozactiva", "DD de VozActiva."), cfg("b"))
    await grafo.ainvoke(Command(resume={"decision": "otra_vuelta", "comentario": "Revisá la concentración de clientes."}), cfg("b"))

    segunda = await grafo.aget_state(cfg("b"))
    assert segunda.next == ("aprobacion",)  # segunda pausa, con el análisis revisado
    assert segunda.interrupts[0].value["pausa"] == 2 and segunda.interrupts[0].value["rondas_pedidas"] == 1
    assert len(modelos.supervisor.recibidos) == 1  # el pedido del comité es la única decisión con LLM
    assert "Revisá la concentración de clientes." in modelos.critico.recibidos[1][1].text
    assert segunda.values["critica"].version == 2

    await grafo.ainvoke(Command(resume={"decision": "rechazar", "comentario": "La concentración es demasiado alta."}), cfg("b"))
    final = (await grafo.aget_state(cfg("b"))).values
    assert final["decision_final"] == "RECHAZADA"
    assert [d.accion for d in final["revision"].decisiones] == ["otra_vuelta", "rechazar"]


@pytest.mark.parametrize(
    ("pedido", "rondas", "mensaje"),
    [
        ({"decision": "rechazar"}, 0, "comentario es obligatorio"),
        ({"decision": "otra_vuelta", "comentario": "ok"}, 0, "comentario es obligatorio"),
        ({"decision": "otra_vuelta", "comentario": "Revisá el churn."}, 2, "máximo 2"),
    ],
)
def test_validacion_del_pedido(pedido, rondas, mensaje):
    with pytest.raises(DecisionInvalida, match=mensaje):
        validar_pedido(PedidoAprobacion(**pedido), rondas, DDSettings())


def test_aprobar_no_exige_comentario():
    validar_pedido(PedidoAprobacion(decision="aprobar"), 2, DDSettings())


# --- Triage: qué casos son críticos ------------------------------------------------------------

LIMPIA = Validacion(aprobado=True, intento=1, checks=[Check(nombre="investigacion", ok=True, detalle="ok")])
CON_SALVEDAD = Validacion(
    aprobado=True, intento=1, checks=[Check(nombre="metricas_clave", ok=True, advertencia=True, detalle="faltan 2")]
)


def con(recomendacion: str, score: float, validacion: Validacion = LIMPIA, **extra):
    calculos = {**CALCULOS_COMPLETOS, "calcular_scoring": {"score": score, "banda": "x", "criterios": {}, "sin_datos": []}}
    return estado(recomendacion=recomendacion, analisis=analisis_con(calculos), validacion=validacion, **extra)


@pytest.mark.parametrize(
    ("recomendacion", "score", "validacion", "critica", "nivel", "motivo"),
    [
        ("AVANZAR A COMITÉ", 92, LIMPIA, True, "alto", "recomendación positiva"),
        ("PROFUNDIZAR", 89, LIMPIA, True, "alto", "recomendación positiva"),  # bajada por red flags altas
        ("NO AVANZAR", 47, LIMPIA, True, "medio", "zona de ambigüedad: score 47 a 3 puntos del umbral 50"),
        ("NO AVANZAR", 41, CON_SALVEDAD, True, "medio", "salvedades (metricas_clave)"),
        ("NO AVANZAR", 41, LIMPIA, False, "bajo", "descarte claro"),
        ("SIN RECOMENDACIÓN", 12, CON_SALVEDAD, False, "bajo", "abstención"),
    ],
)
def test_criterio_de_criticidad(recomendacion, score, validacion, critica, nivel, motivo):
    resultado = evaluar_criticidad(con(recomendacion, score, validacion), DDSettings())
    assert (resultado.critica, resultado.nivel_riesgo) == (critica, nivel)
    assert any(motivo in m for m in resultado.motivos)


def test_si_el_comite_ya_intervino_siempre_decide_el_cierre():
    revision = RevisionHumana(decisiones=[DecisionHumana(accion="otra_vuelta", comentario="Revisá el churn.", ronda=1)])
    resultado = evaluar_criticidad(con("NO AVANZAR", 30, revision=revision), DDSettings())
    assert resultado.critica and resultado.nivel_riesgo == "alto" and "el comité ya intervino" in resultado.motivos[0]


async def test_un_descarte_claro_no_pausa_el_grafo(corpus):
    grafo, _ = armar(corpus, guion_feliz(), DDSettings())
    await grafo.ainvoke(estado_inicial("vozactiva", "DD de VozActiva."), cfg("auto"))
    final = await grafo.aget_state(cfg("auto"))
    assert final.next == () and final.interrupts == ()
    assert final.values["decision_final"] == "EMITIDA SIN PAUSA" and final.values["criticidad"].critica is False
    assert [a.agente for a in final.values["aportes"]][-3:] == ["informe", "triage", "cierre"]
