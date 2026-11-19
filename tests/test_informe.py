"""Informe: recomendación preliminar (con abstención) y contenido de la síntesis determinística."""

from due_diligence.informe import recomendar, redactar
from due_diligence.state import Check, Critica, RedFlag, Validacion

from .conftest import CALCULOS_COMPLETOS, METRICAS_VOZACTIVA_PITCH, analisis_con, estado, investigacion_desde

APROBADA = Validacion(aprobado=True, intento=1, checks=[Check(nombre="investigacion", ok=True, detalle="ok")])
ALTA = RedFlag(id="R1", titulo="Concentración", severidad="alta", explicacion="46 % del MRR.", fuente="vozactiva/clientes-y-equipo#1", cita="46 %")


def con_score(score: float, banda: str):
    return analisis_con({**CALCULOS_COMPLETOS, "calcular_scoring": {"score": score, "banda": banda, "criterios": {}, "sin_datos": []}})


def test_sin_validacion_aprobada_no_recomienda():
    recomendacion, motivo = recomendar(estado(validacion=Validacion(aprobado=False, intento=2, checks=[])))
    assert recomendacion == "SIN RECOMENDACIÓN" and "analista humano" in motivo


def test_informacion_insuficiente_se_abstiene():
    e = estado(validacion=APROBADA, investigacion=investigacion_desde(METRICAS_VOZACTIVA_PITCH[:4]), analisis=con_score(80, "avanzar a comité"))
    recomendacion, motivo = recomendar(e)
    assert recomendacion == "SIN RECOMENDACIÓN" and "información insuficiente" in motivo


def test_una_red_flag_alta_impide_avanzar_directo_a_comite():
    base = dict(validacion=APROBADA, investigacion=investigacion_desde(METRICAS_VOZACTIVA_PITCH), analisis=con_score(82, "avanzar a comité"))
    assert recomendar(estado(**base, critica=Critica(version=1, version_analisis=1)))[0] == "AVANZAR A COMITÉ"
    recomendacion, motivo = recomendar(estado(**base, critica=Critica(version=1, version_analisis=1, red_flags=[ALTA])))
    assert recomendacion == "PROFUNDIZAR" and "impiden avanzar" in motivo


def test_el_informe_cita_fuentes_y_muestra_la_trazabilidad():
    e = estado(
        pasos=7,
        validacion=APROBADA,
        investigacion=investigacion_desde(METRICAS_VOZACTIVA_PITCH),
        analisis=con_score(41, "no avanzar"),
        critica=Critica(version=1, version_analisis=1, red_flags=[ALTA]),
    )
    texto = redactar(e, "VozActiva", "NO AVANZAR", "score 41/100")
    assert texto.startswith("# Due diligence — VozActiva")
    assert "## Recomendación preliminar: NO AVANZAR" in texto
    assert "| mrr_usd | 120.000 | vozactiva/pitch#3 |" in texto
    assert "**[alta] Concentración**" in texto
    assert "aprobación humana" in texto
    assert "## Trazabilidad: quién aportó qué" in texto
