"""PE7: supervisor híbrido y precarga del investigador (menos llamadas al LLM, mismo resultado)."""

from langgraph.checkpoint.memory import InMemorySaver

from due_diligence.agents.research_agent import informe_de_mercado_afin
from due_diligence.agents.supervisor import crear_nodo_supervisor, eleccion_real
from due_diligence.config import DDSettings
from due_diligence.graph import build_graph
from due_diligence.state import Check, Critica, Correccion, DecisionHumana, RevisionHumana, Validacion, serializador
from due_diligence.tracing import analizar

from .conftest import (
    CALCULOS_COMPLETOS,
    CORRECCION_MRR_VOZACTIVA,
    METRICAS_VOZACTIVA_PITCH,
    ScriptedChatModel,
    analisis_con,
    decision,
    estado,
    guion_feliz,
    investigacion_desde,
    modelos_guionados,
)


def estado_completo(**cambios):
    """Investigación, análisis y crítica al día: la rúbrica no tiene pendientes."""
    base = dict(
        investigacion=investigacion_desde(CORRECCION_MRR_VOZACTIVA + METRICAS_VOZACTIVA_PITCH[3:]),
        analisis=analisis_con(CALCULOS_COMPLETOS),
        critica=Critica(version=1, version_analisis=1),
        pasos=3,
        intentos={"investigador": 1, "analista": 1, "critico": 1},
    )
    base.update(cambios)
    return estado(**base)


async def test_camino_feliz_sin_llamadas_del_supervisor_y_con_data_room_precargado(corpus):
    modelos = modelos_guionados(guion_feliz())
    grafo = build_graph(modelos, corpus, DDSettings(), checkpointer=InMemorySaver(serde=serializador()))
    resultado = await analizar(grafo, "vozactiva", "Due diligence de VozActiva.", {"configurable": {"thread_id": "h"}}, mostrar=None)

    assert resultado.recomendacion == "NO AVANZAR"
    assert modelos.supervisor.recibidos == []  # ni una llamada: el orden natural lo decidió el código
    decisiones = [a for a in resultado.estado["aportes"] if a.agente == "supervisor"]
    assert [a.accion for a in decisiones] == ["→ investigador", "→ analista", "→ critico", "→ finalizar"]
    assert all("decisión determinística" in a.detalle for a in decisiones)
    # El investigador registró en su primera ronda: el data room y el mercado ya venían en el mensaje.
    primer_mensaje = modelos.investigador.recibidos[0][1].text
    assert "DOCUMENTOS YA CARGADOS" in primer_mensaje
    assert "vozactiva/metricas#1" in primer_mensaje and "mercado/contact-centers-latam#1" in primer_mensaje
    assert resultado.uso_llm["total"]["llamadas"] == 7  # 2 investigador + 4 analista + 1 crítico (PE6: ~20)


def test_el_llm_decide_solo_cuando_hay_una_eleccion_real():
    assert eleccion_real(estado_completo()) is None
    correccion = Correccion(id="C1", agente_afectado="analista", severidad="media", descripcion="Recalculá el CAC.")
    assert eleccion_real(estado_completo(critica=Critica(version=1, version_analisis=1, correcciones=[correccion]))) == (
        "correcciones del crítico pendientes"
    )
    rechazada = Validacion(aprobado=False, intento=1, checks=[Check(nombre="x", ok=False, detalle="falta algo")])
    assert eleccion_real(estado_completo(validacion=rechazada)) == "el validador rechazó el cierre"
    revision = RevisionHumana(decisiones=[DecisionHumana(accion="otra_vuelta", comentario="Revisá la concentración.", ronda=1)])
    assert eleccion_real(estado_completo(revision=revision)) == "pedido del comité"
    assert eleccion_real(estado_completo(revision=revision, ronda_humana_atendida=1)) is None


async def test_pedido_del_comite_el_llm_elige_y_no_puede_finalizar(corpus):
    revision = RevisionHumana(decisiones=[DecisionHumana(accion="otra_vuelta", comentario="Revisá la concentración en TelcoAndina.", ronda=1)])
    modelo = ScriptedChatModel(respuestas=[decision("finalizar", razonamiento="Ya está todo.")])
    nodo = crear_nodo_supervisor(modelo, corpus, DDSettings())
    cambios = await nodo(estado_completo(revision=revision, intentos={"investigador": 3, "analista": 3, "critico": 2}))

    assert len(modelo.recibidos) == 1 and "PENDIENTE: Revisá la concentración" in modelo.recibidos[0][1].text
    assert cambios["siguiente"] == "critico"  # la guarda: no se cierra sin atender al comité
    assert "Revisá la concentración en TelcoAndina." in cambios["instruccion"]
    assert cambios["ronda_humana_atendida"] == 1
    assert cambios["intentos"]["critico"] == 3  # la ronda humana amplía el cupo (2 + 1)
    assert "pedido del comité" in cambios["aportes"][0].detalle


def test_cada_startup_recibe_el_informe_de_mercado_de_su_sector(corpus):
    assert {s: informe_de_mercado_afin(corpus, s) for s in corpus.startups} == {
        "anamnesis": "salud-digital-latam",
        "aulaviva": "edtech-latam",
        "contralia": "legaltech-latam",
        "lupacode": "herramientas-desarrollo-latam",
        "vozactiva": "contact-centers-latam",
    }
