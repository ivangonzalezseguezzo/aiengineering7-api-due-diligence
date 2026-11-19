"""El orquestador completo con LLMs guionados: delegación, refinamiento, frenos y abstención."""

import warnings

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from due_diligence.config import DDSettings
from due_diligence.graph import EscrituraFueraDeParcela, Modelos, build_graph, con_parcela
from due_diligence.state import estado_inicial, serializador
from due_diligence.tracing import analizar

from .conftest import (
    MODO_PE6,
    CORRECCION_MRR_VOZACTIVA,
    HALLAZGOS_VOZACTIVA,
    METRICAS_VOZACTIVA_PITCH,
    ScriptedChatModel,
    decision,
    llamada,
    llamadas,
    metrica,
    respuesta,
)

PEDIDO = "Hacé la due diligence de VozActiva para el comité."
SCORING_ARGS = {"puntaje_equipo": 2, "justificacion_equipo": "CTO con dedicación parcial.", "puntaje_mercado": 3, "justificacion_mercado": "Mercado grande con precios en baja."}


def config(thread: str, recursion_limit: int = 40) -> dict:
    return {"configurable": {"thread_id": thread}, "recursion_limit": recursion_limit}


def armar(corpus, settings, supervisor, investigador=(), analista=(), critico=()):
    modelos = Modelos(
        supervisor=ScriptedChatModel(respuestas=list(supervisor)),
        investigador=ScriptedChatModel(respuestas=list(investigador)),
        analista=ScriptedChatModel(respuestas=list(analista)),
        critico=ScriptedChatModel(respuestas=list(critico)),
    )
    grafo = build_graph(modelos, corpus, settings, checkpointer=InMemorySaver(serde=serializador()))
    return grafo, modelos


def analisis_completo() -> list:
    """Guion del analista: 4 rondas (costo LLM + mercado, unit economics, scoring, interpretación)."""
    return [
        llamadas(("calcular_costo_llm", {}), ("estimar_tamano_mercado", {"cuota_som_pct": 2, "justificacion_som": "Competencia intensa."})),
        llamada("calcular_unit_economics"),
        llamada("calcular_scoring", **SCORING_ARGS),
        respuesta("Margen real bajo después del costo de LLM y voz; LTV/CAC flojo; runway corto."),
    ]


def test_estructura_del_grafo(corpus, settings):
    grafo, _ = armar(corpus, settings, [])
    aristas = {(a.source, a.target, a.conditional) for a in grafo.get_graph().edges}
    for especialista in ("investigador", "analista", "critico"):
        assert ("supervisor", especialista, True) in aristas  # delegación dinámica
        assert (especialista, "supervisor", False) in aristas  # siempre vuelven al supervisor
    assert ("supervisor", "validador", True) in aristas
    assert {("validador", "supervisor", True), ("validador", "informe", True), ("informe", "__end__", False)} <= aristas
    assert not any(a[0] in ("investigador", "analista", "critico") and a[1] in ("investigador", "analista", "critico") for a in aristas)


async def test_un_nodo_no_puede_escribir_fuera_de_su_parcela():
    async def analista_invasor(state):
        return {"analisis": None, "investigacion": None}

    with pytest.raises(EscrituraFueraDeParcela, match="investigacion"):
        await con_parcela("analista", analista_invasor)({})


async def test_due_diligence_completa_con_correccion_del_critico(corpus, settings):
    grafo, modelos = armar(
        corpus,
        settings,
        supervisor=[
            decision("investigador", "Relevá el data room completo y el mercado."),
            decision("analista", "Calculá todo."),
            decision("critico", "Auditá."),
            decision("investigador", "Aplicá C1: registrá el MRR recurrente."),
            decision("analista", "Recalculá con los datos corregidos (C2)."),
            decision("critico", "Re-auditá el análisis v2."),
            decision("finalizar"),
        ],
        investigador=[
            llamadas(("buscar_en_data_room", {"consulta": "MRR clientes churn"}), ("buscar_mercado", {"consulta": "centros de contacto"})),
            llamadas(("registrar_metricas", {"metricas": METRICAS_VOZACTIVA_PITCH}), ("registrar_hallazgos", {"hallazgos": HALLAZGOS_VOZACTIVA})),
            respuesta("Registré 21 métricas; el MRR del pitch incluye pilotos."),
            llamada("registrar_metricas", metricas=CORRECCION_MRR_VOZACTIVA),
            respuesta("Corregí MRR, clientes y crecimiento con los valores recurrentes."),
        ],
        analista=analisis_completo() + analisis_completo(),
        critico=[
            llamadas(("contrastar_metricas", {}), ("buscar_evidencia", {"consulta": "pilotos única vez"})),
            llamada(
                "registrar_observaciones",
                red_flags=[{"titulo": "Concentración en TelcoAndina", "severidad": "alta", "explicacion": "46 % del MRR recurrente y el contrato vence en marzo.", "fuente": "vozactiva/clientes-y-equipo#1", "cita": "El contrato con TelcoAndina vence el 31/03/2027 y está en renegociación"}],
                correcciones=[
                    {"agente_afectado": "investigador", "severidad": "alta", "descripcion": "mrr_usd debe ser el recurrente: el pitch suma USD 30.000 de pilotos.", "metrica": "mrr_usd", "valor_correcto": 90000, "fuente": "vozactiva/metricas#1", "cita": "MRR recurrente (contratos mensuales): USD 90.000"},
                    {"agente_afectado": "analista", "severidad": "alta", "descripcion": "Recalcular unit economics y scoring con el MRR recurrente y 20 clientes."},
                ],
            ),
            respuesta("El MRR incluye pilotos y hay concentración alta."),
            llamada("contrastar_metricas"),
            respuesta("Las correcciones se aplicaron; quedan las red flags."),
        ],
    )
    resultado = await analizar(grafo, "vozactiva", PEDIDO, config("vozactiva"), mostrar=None)
    estado = resultado.estado

    # Delegación: el recorrido completo, siempre de vuelta al supervisor.
    assert [a.agente for a in estado["aportes"]] == [
        "supervisor", "investigador", "supervisor", "analista", "supervisor", "critico",
        "supervisor", "investigador", "supervisor", "analista", "supervisor", "critico",
        "supervisor", "validador", "informe",
    ]  # fmt: skip
    assert estado["pasos"] == 7
    assert estado["intentos"] == {"investigador": 2, "analista": 2, "critico": 2}

    # El conflicto entre fuentes (MRR del pitch vs. métricas) se resolvió con evidencia y en la parcela de su dueño.
    inv = estado["investigacion"]
    assert inv.version == 2 and inv.metricas["mrr_usd"].valor == 90000
    assert inv.reemplazos[0].startswith("mrr_usd: 120000")
    assert inv.correcciones_atendidas == ["C1"]
    ana = estado["analisis"]
    assert ana.version == 2 and ana.version_investigacion == 2 and ana.correcciones_atendidas == ["C2"]
    assert ana.resultado("calcular_unit_economics")["margen_bruto_pct"] == pytest.approx(41.89, abs=0.01)
    assert ana.resultado("calcular_scoring")["score"] == 41

    # El contraste determinístico entra siempre, sin depender del LLM.
    cri = estado["critica"]
    assert cri.version_analisis == 2
    assert {r.id for r in cri.red_flags} == {"R1", "K-margen_bruto_declarado_pct", "K-runway_declarado_meses"}

    assert estado["validacion"].aprobado
    assert estado["recomendacion"] == "NO AVANZAR"
    assert "## Red flags (crítico)" in estado["informe"] and "TelcoAndina" in estado["informe"]

    # Contaminación de contexto: cada especialista recibe su instrucción, no el historial ni el tablero.
    entrada_investigador = modelos.investigador.recibidos[0]
    assert len(entrada_investigador) == 2  # system prompt del rol + instrucción con el contexto mínimo
    assert PEDIDO not in entrada_investigador[1].text
    assert "Relevá el data room completo" in entrada_investigador[1].text
    assert "C1 (alta)" in modelos.investigador.recibidos[3][1].text  # la corrección le llega a su dueño
    assert "C1" not in modelos.analista.recibidos[4][1].text and "C2 (alta)" in modelos.analista.recibidos[4][1].text
    # El supervisor decide con el tablero, nunca con mensajes de herramientas.
    for mensajes in modelos.supervisor.recibidos:
        assert not any(isinstance(m, ToolMessage) for m in mensajes)
        assert "RÚBRICA DE SUFICIENCIA" in mensajes[1].text


async def test_cierre_prematuro_el_validador_rechaza_y_la_guarda_lo_repara(corpus, settings):
    grafo, modelos = armar(
        corpus,
        settings,
        supervisor=[
            decision("investigador"),
            decision("analista"),
            decision("finalizar", razonamiento="Ya alcanza."),  # sin crítica: el validador lo rechaza
            decision("finalizar", razonamiento="Insisto."),  # la guarda lo manda al crítico
            decision("finalizar"),
        ],
        investigador=[
            llamadas(
                ("registrar_metricas", {"metricas": CORRECCION_MRR_VOZACTIVA + METRICAS_VOZACTIVA_PITCH[3:]}),
                ("registrar_hallazgos", {"hallazgos": HALLAZGOS_VOZACTIVA}),
            ),
            respuesta("Listo."),
        ],
        analista=analisis_completo(),
        critico=[respuesta("Sin observaciones nuevas.")],
    )
    resultado = await analizar(grafo, "vozactiva", PEDIDO, config("prematuro"), mostrar=None)
    estado = resultado.estado
    agentes = [a.agente for a in estado["aportes"]]
    assert agentes.count("validador") == 2
    primera = next(a for a in estado["aportes"] if a.agente == "validador")
    assert "rechazada" in primera.detalle and "critica_vigente" in primera.detalle
    guarda = [a for a in estado["aportes"] if a.agente == "supervisor"][3]
    assert guarda.accion == "→ critico" and "el validador rechazó el cierre" in guarda.detalle
    assert estado["validacion"].aprobado and estado["recomendacion"] == "NO AVANZAR"


async def test_supervisor_infinito_frenado_por_los_topes(corpus, settings):
    """El LLM del supervisor pide al crítico una y otra vez: las guardas y los topes cortan el bucle."""
    grafo, modelos = armar(
        corpus,
        settings,
        supervisor=[decision("critico", "Auditá de nuevo.") for _ in range(20)],
        investigador=[respuesta("No encontré nada.") for _ in range(3)],
        analista=[respuesta("Sin datos para calcular.") for _ in range(3)],
        critico=[respuesta("Nada que observar.") for _ in range(2)],
    )
    resultado = await analizar(grafo, "vozactiva", PEDIDO, config("infinito"), mostrar=None)
    estado = resultado.estado
    assert resultado.error is None and estado["tarea_completa"]
    assert estado["intentos"] == {"investigador": 2, "analista": 3, "critico": 2}
    assert estado["pasos"] < settings.max_pasos
    guardas = [a.detalle for a in estado["aportes"] if a.agente == "supervisor" and "[guarda" in a.detalle]
    assert any("dependencia" in g for g in guardas) and any("agotó sus 2 intentos" in g for g in guardas)
    assert estado["validacion"].aprobado and estado["validacion"].advertencias  # lo irresoluble queda como salvedad
    assert estado["recomendacion"] == "SIN RECOMENDACIÓN"


async def test_limite_de_pasos_cierra_sin_llamar_al_llm_y_se_abstiene(corpus):
    settings = DDSettings(max_pasos=4, **MODO_PE6)
    grafo, modelos = armar(
        corpus,
        settings,
        supervisor=[decision("investigador"), decision("analista"), decision("analista")],
        investigador=[llamada("registrar_metricas", metricas=METRICAS_VOZACTIVA_PITCH), respuesta("Listo.")],
        analista=[respuesta("No calculé nada."), respuesta("Tampoco.")],
    )
    resultado = await analizar(grafo, "vozactiva", PEDIDO, config("limite"), mostrar=None)
    estado = resultado.estado
    assert estado["pasos"] == 4
    assert len(modelos.supervisor.recibidos) == 3  # la 4.ª decisión (finalizar) no consultó al LLM
    assert "límite de pasos" in estado["aportes"][-3].detalle
    assert not estado["validacion"].aprobado
    assert estado["recomendacion"] == "SIN RECOMENDACIÓN"
    assert "escalar a un analista humano" in estado["informe"]


async def test_data_room_incompleto_se_abstiene_por_informacion_insuficiente(corpus, settings):
    pocas = [
        metrica("mrr_usd", 6200, "aulaviva/pitch#3", "MRR de USD 6.200"),
        metrica("clientes_activos", 11, "aulaviva/carta-inversores#2", "Colegios que pagan una suscripción: 11"),
        metrica("caja_usd", 150000, "aulaviva/carta-inversores#3", "Caja disponible: USD 150.000"),
    ]
    grafo, _ = armar(
        corpus,
        settings,
        supervisor=[decision("investigador"), decision("investigador", "Buscá churn, CAC, burn y uso de LLM."), decision("analista"), decision("critico"), decision("finalizar")],
        investigador=[llamada("registrar_metricas", metricas=pocas), respuesta("Faltan casi todas las métricas."), respuesta("No están en el data room.")],
        analista=[llamada("calcular_unit_economics"), llamada("calcular_scoring", **SCORING_ARGS), respuesta("Casi nada es calculable.")],
        critico=[
            llamada(
                "registrar_observaciones",
                red_flags=[{"titulo": "Costo de IA oculto por créditos", "severidad": "alta", "explicacion": "El costo real aparece en marzo.", "fuente": "aulaviva/carta-inversores#3", "cita": "El costo de IA lo cubren créditos promocionales del proveedor hasta marzo de 2027."}],
            ),
            respuesta("Faltan datos y hay créditos que vencen."),
        ],
    )
    resultado = await analizar(grafo, "aulaviva", "Due diligence de Aula Viva.", config("aulaviva"), mostrar=None)
    estado = resultado.estado
    assert estado["validacion"].aprobado
    advertencias = {c.nombre for c in estado["validacion"].advertencias}
    assert {"investigacion", "metricas_clave"} <= advertencias
    assert estado["recomendacion"] == "SIN RECOMENDACIÓN"
    assert "información insuficiente" in estado["informe"]


async def test_el_estado_se_guarda_en_checkpoints_sin_avisos_de_serializacion(corpus):
    grafo, _ = armar(
        corpus,
        DDSettings(max_pasos=4, **MODO_PE6),
        supervisor=[decision("investigador"), decision("finalizar"), decision("finalizar")],
        investigador=[llamada("registrar_metricas", metricas=METRICAS_VOZACTIVA_PITCH[:2]), respuesta("Dos métricas."), respuesta("Nada más.")],
    )
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # un aviso de "unregistered type" haría fallar el test
        await grafo.ainvoke(estado_inicial("vozactiva", PEDIDO), config("checkpoint"))
        historial = [s async for s in grafo.aget_state_history(config("checkpoint"))]
    assert len(historial) > 5
    final = historial[0].values
    assert final["investigacion"].metricas["mrr_usd"].valor == 120000
    assert isinstance(final["messages"][0], HumanMessage)
    assert all(isinstance(m, AIMessage) for m in final["messages"][1:])
