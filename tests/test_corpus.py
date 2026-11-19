"""Corpus: fragmentos con id estable, búsqueda acotada por ámbito y anclaje de citas."""

import pytest

from due_diligence.corpus import cita_en_texto, limpiar_cita, mismo_numero, numeros_en


def test_carga_los_cinco_data_rooms_y_las_fuentes_de_mercado(corpus):
    assert corpus.startups == {
        "anamnesis": "Anamnesis",
        "aulaviva": "Aula Viva",
        "contralia": "Contralia",
        "lupacode": "Lupa Code",
        "vozactiva": "VozActiva",
    }
    ambitos = {f.ambito for f in corpus.fragmentos}
    assert {"mercado", "fondo"} <= ambitos
    assert corpus.fragmento("vozactiva/finanzas#4").seccion == "Caja"


def test_busqueda_encuentra_la_letra_chica(corpus):
    ids = [f.id for f in corpus.buscar("pilotos única vez", ("vozactiva",))]
    assert ids[0] == "vozactiva/metricas#1"
    assert "vozactiva/finanzas#4" in [f.id for f in corpus.buscar("préstamo puente", ("vozactiva",))]


def test_busqueda_no_mezcla_data_rooms(corpus):
    for f in corpus.buscar("churn mensual de clientes", ("vozactiva",), k=10):
        assert f.ambito == "vozactiva"
    assert corpus.buscar("zzz qqq", ("vozactiva",)) == []


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("Caja disponible: USD 1.150.000.", [1_150_000]),
        ("Churn mensual: 6,5 %", [6.5]),
        ("USD 2,50 por millón", [2.5]),
        ("un préstamo de USD 1,2 millones", [1_200_000]),
        ("Clientes nuevos por mes: 2,5", [2.5]),
        ("al 31/10/2026: USD 700.000", [31, 10, 2026, 700_000]),
    ],
)
def test_numeros_en_formato_rioplatense(texto, esperado):
    assert numeros_en(texto) == esperado


def test_mismo_numero_tolera_redondeo():
    assert mismo_numero(41.9, 41.89)
    assert not mismo_numero(0.065, 6.5)


def test_cita_ignora_mayusculas_tildes_y_markdown():
    texto = "- **Churn mensual** de clientes recurrentes: 6,5 %."
    assert cita_en_texto("churn mensual de clientes recurrentes: 6,5 %", texto)
    assert not cita_en_texto("6,5 %", texto)  # demasiado corta para anclar nada
    assert not cita_en_texto("Churn mensual de clientes: 2 %", texto)


def test_limpiar_cita_saca_la_vineta_y_el_punto_final():
    assert limpiar_cita("- MRR recurrente (contratos mensuales): USD 90.000.") == "MRR recurrente (contratos mensuales): USD 90.000"
    assert limpiar_cita("  Churn mensual: 6,5 %  ") == "Churn mensual: 6,5 %"


def test_anclaje_corrige_el_id_si_la_cita_esta_en_otro_fragmento(corpus):
    anclado = corpus.anclar("Burn neto mensual: USD 95.000", "vozactiva/finanzas#1", ("vozactiva",))
    assert anclado.id == "vozactiva/finanzas#4"


def test_anclaje_respeta_el_ambito(corpus):
    cita_de_contralia = "MRR (ingreso recurrente mensual): USD 85.500"
    assert corpus.anclar(cita_de_contralia, "contralia/metricas#1", ("contralia",)) is not None
    assert corpus.anclar(cita_de_contralia, "contralia/metricas#1", ("vozactiva",)) is None
