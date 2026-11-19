"""Fórmulas del analista (sin LLM): los números del informe tienen que ser exactos."""

import pytest

from due_diligence import finanzas


def test_costo_llm_de_vozactiva():
    r = finanzas.costo_llm(2_400_000, 1800, 250, 2.5, 10, mrr_usd=90_000)
    assert r["costo_entrada_usd"] == 10_800
    assert r["costo_salida_usd"] == 6_000
    assert r["costo_llm_mensual_usd"] == 16_800
    assert r["costo_llm_pct_mrr"] == pytest.approx(18.67, abs=0.01)
    assert "escenario" not in r


def test_costo_llm_con_escenario_de_suba_de_precio():
    r = finanzas.costo_llm(2_400_000, 1800, 250, 2.5, 10, mrr_usd=90_000, variacion_precio_pct=40)
    assert r["escenario"]["costo_llm_mensual_usd"] == pytest.approx(23_520)
    assert r["escenario"]["costo_llm_pct_mrr"] == pytest.approx(26.13, abs=0.01)


def test_unit_economics_completos():
    r = finanzas.unit_economics(
        mrr_usd=90_000, clientes_activos=20, churn_mensual_pct=6.5, gasto_comercial_mensual_usd=78_000,
        clientes_nuevos_mes=2.5, costo_llm_mensual_usd=16_800, costo_infra_mensual_usd=9_500,
        otros_costos_directos_mensual_usd=26_000, burn_neto_mensual_usd=95_000, caja_usd=700_000,
    )  # fmt: skip
    assert r["arpa_usd"] == 4_500
    assert r["margen_bruto_pct"] == pytest.approx(41.89, abs=0.01)
    assert r["cac_usd"] == 31_200
    assert r["ltv_usd"] == pytest.approx(29_000, rel=1e-3)
    assert r["ltv_cac"] == pytest.approx(0.93, abs=0.01)
    assert r["payback_meses"] == pytest.approx(16.6, abs=0.05)
    assert r["runway_meses"] == pytest.approx(7.4, abs=0.05)
    assert "no_calculables" not in r and "advertencias" not in r


def test_unit_economics_con_datos_faltantes_explica_que_no_calcula():
    r = finanzas.unit_economics(mrr_usd=6_200, clientes_activos=11, caja_usd=150_000)
    assert r["arpa_usd"] == pytest.approx(563.64, abs=0.01)
    assert set(r["no_calculables"]) >= {"margen_bruto_pct", "cac_usd", "ltv_usd", "payback_meses", "runway_meses"}


def test_margen_sin_todos_los_costos_avisa_que_puede_estar_inflado():
    r = finanzas.unit_economics(mrr_usd=120_000, clientes_activos=26, costo_llm_mensual_usd=16_800, costo_infra_mensual_usd=9_500)
    assert r["margen_bruto_pct"] == pytest.approx(78.08, abs=0.01)
    assert "otros_costos_directos_mensual_usd" in r["advertencias"][0]


def test_churn_cero_y_empresa_que_no_quema_caja():
    r = finanzas.unit_economics(
        mrr_usd=10_000, clientes_activos=10, churn_mensual_pct=0, costo_infra_mensual_usd=1_000,
        burn_neto_mensual_usd=-5_000, caja_usd=100_000,
    )  # fmt: skip
    assert "LTV no está acotado" in r["no_calculables"]["ltv_usd"]
    assert "runway_meses" not in r
    assert any("no quema caja" in a for a in r["advertencias"])


def test_tamano_de_mercado_bottom_up():
    r = finanzas.tamano_mercado(21_000, 11_400, 42.9, 5)
    assert r["tam_usd"] == 239_400_000
    assert r["sam_usd"] == pytest.approx(102_702_600)
    assert r["som_usd"] == pytest.approx(5_135_130)


@pytest.mark.parametrize(
    ("criterio", "valor", "esperado"),
    [
        ("ltv_cac", 5.8, 5), ("ltv_cac", 2.5, 3), ("ltv_cac", 0.93, 0),
        ("payback_meses", 9.6, 5), ("payback_meses", 16.6, 4), ("payback_meses", 40, 0),
        ("margen_bruto_pct", 71.3, 5), ("margen_bruto_pct", 41.9, 2),
        ("runway_meses", 24, 5), ("runway_meses", 7.4, 2),
        ("concentracion_top_cliente_pct", 7, 5), ("concentracion_top_cliente_pct", 46, 1),
        ("crecimiento_mrr_mensual_pct", 8.5, 3), ("crecimiento_mrr_mensual_pct", None, None),
    ],
)  # fmt: skip
def test_umbrales_de_la_tesis_del_fondo(criterio, valor, esperado):
    assert finanzas.puntaje(criterio, valor) == esperado


def test_scoring_de_vozactiva_no_avanza():
    r = finanzas.scoring(
        ltv_cac=0.93, payback_meses=16.6, margen_bruto_pct=41.89, runway_meses=7.4,
        crecimiento_mrr_mensual_pct=4, concentracion_top_cliente_pct=46, puntaje_equipo=2, puntaje_mercado=3,
    )  # fmt: skip
    assert r["score"] == 41
    assert r["banda"] == "no avanzar"
    assert sum(d["peso_pct"] for d in r["criterios"].values()) == 100


def test_scoring_penaliza_los_datos_faltantes():
    r = finanzas.scoring(margen_bruto_pct=75, puntaje_equipo=5, puntaje_mercado=5)
    assert set(r["sin_datos"]) == {"ltv_cac", "payback_meses", "runway_meses", "crecimiento_mrr_mensual_pct", "concentracion_top_cliente_pct"}
    assert r["score"] == 40  # margen 20 + equipo 10 + mercado 10
