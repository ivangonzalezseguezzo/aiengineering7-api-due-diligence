"""Supervisor: tablero sin historial, salida estructurada, guardas determinísticas y errores del proveedor."""

import pytest
from langchain_core.exceptions import ModelRateLimitError, OutputParserException

from due_diligence.agents.supervisor import crear_nodo_supervisor, tablero
from due_diligence.state import Critica, Correccion, Validacion

from .conftest import CALCULOS_COMPLETOS, METRICAS_VOZACTIVA_PITCH, ScriptedChatModel, analisis_con, decision, estado, investigacion_desde

DIARIO = "429 RESOURCE_EXHAUSTED quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier"
POR_MINUTO = "429 RESOURCE_EXHAUSTED quotaId: GenerateRequestsPerMinutePerProjectPerModel-FreeTier"


def supervisor(corpus, settings, *respuestas, reintentos=0):
    modelo = ScriptedChatModel(respuestas=list(respuestas))
    return crear_nodo_supervisor(modelo, corpus, settings, reintentos_llm=reintentos), modelo


def test_tablero_resume_el_estado_y_la_rubrica(settings, corpus):
    texto = tablero(estado(), settings, "VozActiva")
    assert "PASO: 1 de 12" in texto
    assert "investigador (0/3): sin entregar." in texto
    assert "[ ] investigacion: El investigador todavía no entregó datos. (resuelve: investigador)" in texto
    assert "Due diligence de prueba." in texto


async def test_decide_y_registra_el_intento(corpus, settings):
    nodo, modelo = supervisor(corpus, settings, decision("investigador", "Relevá todo."))
    cambios = await nodo(estado())
    assert (cambios["siguiente"], cambios["instruccion"], cambios["pasos"]) == ("investigador", "Relevá todo.", 1)
    assert cambios["intentos"]["investigador"] == 1
    assert cambios["aportes"][0].accion == "→ investigador"
    assert len(modelo.recibidos[0]) == 2  # system prompt + tablero, sin historial


async def test_en_el_ultimo_paso_finaliza_sin_consultar_al_llm(corpus, settings):
    nodo, modelo = supervisor(corpus, settings)  # sin respuestas: si consultara, fallaría
    cambios = await nodo(estado(pasos=settings.max_pasos - 1))
    assert cambios["siguiente"] == "finalizar" and "límite de pasos" in cambios["aportes"][0].detalle
    assert modelo.recibidos == []


async def test_guarda_de_dependencias(corpus, settings):
    nodo, _ = supervisor(corpus, settings, decision("analista", "Calculá."))
    cambios = await nodo(estado())
    assert cambios["siguiente"] == "investigador"
    assert "dependencia" in cambios["aportes"][0].detalle


async def test_no_vuelve_a_un_especialista_que_agoto_sus_intentos(corpus, settings):
    nodo, _ = supervisor(corpus, settings, decision("critico", "Otra vuelta."))
    inv = investigacion_desde(METRICAS_VOZACTIVA_PITCH)
    cambios = await nodo(estado(investigacion=inv, analisis=analisis_con(CALCULOS_COMPLETOS), intentos={"investigador": 1, "analista": 1, "critico": 2}))
    assert cambios["siguiente"] == "finalizar"  # la rúbrica no tiene nada resoluble pendiente
    assert "critico agotó sus 2 intentos" in cambios["aportes"][0].detalle


async def test_insistir_en_cerrar_tras_un_rechazo_manda_al_pendiente(corpus, settings):
    nodo, _ = supervisor(corpus, settings, decision("finalizar"))
    inv = investigacion_desde(METRICAS_VOZACTIVA_PITCH)
    critica = Critica(version=1, version_analisis=1, correcciones=[Correccion(id="C1", agente_afectado="investigador", severidad="alta", descripcion="Usar el MRR recurrente.")])
    rechazo = Validacion(aprobado=False, intento=1, checks=[])
    cambios = await nodo(estado(investigacion=inv, analisis=analisis_con(CALCULOS_COMPLETOS), critica=critica, validacion=rechazo, intentos={"investigador": 1, "analista": 1, "critico": 1}))
    assert cambios["siguiente"] == "investigador"
    assert "C1" in cambios["instruccion"]


@pytest.mark.parametrize("falla", [OutputParserException("JSON inválido"), ValueError("salida vacía")])
async def test_plan_b_si_la_salida_del_llm_es_invalida(corpus, settings, falla):
    nodo, _ = supervisor(corpus, settings, falla)
    cambios = await nodo(estado())
    assert cambios["siguiente"] == "investigador"
    assert "plan B" in cambios["aportes"][0].detalle


async def test_la_cuota_diaria_agotada_corta_el_analisis(corpus, settings):
    nodo, _ = supervisor(corpus, settings, ModelRateLimitError(DIARIO), reintentos=2)
    with pytest.raises(ModelRateLimitError):
        await nodo(estado())


async def test_reintenta_el_limite_por_minuto(corpus, settings):
    nodo, modelo = supervisor(corpus, settings, ModelRateLimitError(POR_MINUTO), decision("investigador", "Relevá."), reintentos=1)
    cambios = await nodo(estado())
    assert cambios["siguiente"] == "investigador"
    assert len(modelo.recibidos) == 2
