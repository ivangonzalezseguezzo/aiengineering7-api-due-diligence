"""Herramientas de los especialistas: anclaje del investigador, proveniencia del analista y contraste del crítico."""

import json

import pytest

from due_diligence.agents.analyst_agent import LibretaAnalisis, crear_herramientas_analista
from due_diligence.agents.critic_agent import LibretaCritica, contrastar, crear_herramientas_critico
from due_diligence.agents.research_agent import LibretaInvestigacion, correccion_aplicada, crear_herramientas_investigador
from due_diligence.state import Correccion

from .conftest import CORRECCION_MRR_VOZACTIVA, METRICAS_VOZACTIVA_PITCH, analisis_con, investigacion_desde, metrica


def herramientas(lista) -> dict:
    return {h.name: h for h in lista}


async def invocar(herramienta, **args) -> dict:
    return json.loads(await herramienta.ainvoke(args))


# --- Investigador ---------------------------------------------------------------------------


@pytest.fixture
def investigador(corpus):
    libreta = LibretaInvestigacion(version=1)
    return herramientas(crear_herramientas_investigador(corpus, "vozactiva", libreta)), libreta


async def test_registra_metricas_ancladas(investigador):
    h, libreta = investigador
    r = await invocar(h["registrar_metricas"], metricas=METRICAS_VOZACTIVA_PITCH)
    assert r["rechazadas"] == []
    assert len(libreta.metricas) == len(METRICAS_VOZACTIVA_PITCH)
    assert r["faltan_todavia"] == []


@pytest.mark.parametrize(
    ("entrada", "error"),
    [
        (metrica("churn_mensual_pct", 0.065, "vozactiva/metricas#4", "Churn mensual de clientes recurrentes: 6,5 %"), "no aparece en la cita"),
        (metrica("churn_mensual_pct", 2, "vozactiva/metricas#4", "Churn mensual de clientes recurrentes: 2 %"), "no aparece literalmente"),
        # Dato de OTRA startup: el anclaje solo mira el data room de vozactiva.
        (metrica("mrr_usd", 85500, "contralia/metricas#1", "MRR (ingreso recurrente mensual): USD 85.500"), "no aparece literalmente"),
        (metrica("mrr_recurrente", 90000, "vozactiva/metricas#1", "MRR recurrente (contratos mensuales): USD 90.000"), "no está en el vocabulario"),
    ],
)
async def test_rechaza_lo_que_no_ancla_y_explica_como_corregirlo(investigador, entrada, error):
    h, libreta = investigador
    r = await invocar(h["registrar_metricas"], metricas=[entrada])
    assert r["aceptadas"] == []
    assert error in r["rechazadas"][0]["error"]
    assert r["rechazadas"][0]["sugerencia"]
    assert libreta.rechazos == 1


async def test_una_metrica_de_mercado_puede_venir_del_informe_sectorial(investigador):
    h, _ = investigador
    r = await invocar(h["registrar_metricas"], metricas=[METRICAS_VOZACTIVA_PITCH[-1]])
    assert r["aceptadas"][0]["fuente"] == "mercado/contact-centers-latam#1"


async def test_corregir_un_dato_queda_registrado_como_reemplazo(investigador):
    h, libreta = investigador
    await invocar(h["registrar_metricas"], metricas=METRICAS_VOZACTIVA_PITCH[:1])
    await invocar(h["registrar_metricas"], metricas=CORRECCION_MRR_VOZACTIVA[:1])
    assert libreta.metricas["mrr_usd"].valor == 90000
    assert libreta.reemplazos == ["mrr_usd: 120000 [vozactiva/pitch#3] -> 90000 [vozactiva/metricas#1]"]


async def test_leer_un_documento_completo(investigador):
    h, libreta = investigador
    r = await invocar(h["leer_documento"], documento="finanzas")
    assert [f["id"] for f in r["fragmentos"]] == [f"vozactiva/finanzas#{n}" for n in range(1, 5)]
    assert len(libreta.fuentes) == 4
    r = await invocar(h["leer_documento"], documento="balance")
    assert "No existe" in r["error"] and "finanzas" in r["sugerencia"]


async def test_una_correccion_verificable_cuenta_solo_si_se_aplico(investigador):
    h, libreta = investigador
    correccion = Correccion(id="C1", agente_afectado="investigador", severidad="alta", descripcion="Usar el MRR recurrente.", metrica="mrr_usd", valor_correcto=90000)
    await invocar(h["registrar_metricas"], metricas=METRICAS_VOZACTIVA_PITCH[:1])  # deja 120000
    assert not correccion_aplicada(correccion, libreta)
    await invocar(h["registrar_metricas"], metricas=CORRECCION_MRR_VOZACTIVA[:1])
    assert correccion_aplicada(correccion, libreta)
    sin_dato = Correccion(id="C2", agente_afectado="investigador", severidad="media", descripcion="Revisar los hallazgos de equipo.")
    assert correccion_aplicada(sin_dato, libreta)


async def test_busqueda_registra_las_fuentes_consultadas(investigador):
    h, libreta = investigador
    r = await invocar(h["buscar_en_data_room"], consulta="préstamo puente runway")
    assert any(f["id"] == "vozactiva/finanzas#4" for f in r["fragmentos"])
    assert "vozactiva/finanzas#4" in libreta.fuentes


class BuscadorRoto:
    nombre = "roto"

    async def buscar(self, consulta, k):
        raise RuntimeError("sin red")


class BuscadorFalso:
    nombre = "falso"

    async def buscar(self, consulta, k):
        return [{"titulo": "Informe", "url": "https://ejemplo.org/informe", "contenido": "Hay 12.345 centros de contacto en la región según el censo 2026."}]


async def test_si_la_web_falla_busca_en_el_corpus_local(corpus):
    h = herramientas(crear_herramientas_investigador(corpus, "vozactiva", LibretaInvestigacion(version=1), BuscadorRoto()))
    r = await invocar(h["buscar_mercado"], consulta="centros de contacto tamaño del mercado")
    assert r["fragmentos"][0]["id"].startswith("mercado/")


async def test_los_resultados_web_se_anclan_como_cualquier_fuente(corpus):
    libreta = LibretaInvestigacion(version=1)
    h = herramientas(crear_herramientas_investigador(corpus, "vozactiva", libreta, BuscadorFalso()))
    r = await invocar(h["buscar_mercado"], consulta="centros de contacto")
    assert r["origen"] == "web (falso)" and r["fragmentos"][0]["id"] == "web:1"
    cita = "Hay 12.345 centros de contacto en la región"
    r = await invocar(h["registrar_metricas"], metricas=[metrica("clientes_potenciales_tam", 12345, "web:1", cita)])
    assert r["aceptadas"][0]["fuente"] == "web:1"


async def test_hallazgos_con_cita(investigador):
    h, libreta = investigador
    r = await invocar(
        h["registrar_hallazgos"],
        hallazgos=[
            {"tema": "equipo", "afirmacion": "CTO part-time.", "fuente": "vozactiva/tecnologia#5", "cita": "trabaja con dedicación parcial (50 %)"},
            {"tema": "equipo", "afirmacion": "Inventado.", "fuente": "vozactiva/tecnologia#5", "cita": "la CTO tiene 20 años de experiencia"},
        ],
    )
    assert len(r["aceptados"]) == 1 and len(r["rechazados"]) == 1
    assert libreta.hallazgos[0].fuente == "vozactiva/tecnologia#5"


# --- Analista -------------------------------------------------------------------------------


@pytest.fixture
def analista():
    inv = investigacion_desde(METRICAS_VOZACTIVA_PITCH)
    libreta = LibretaAnalisis()
    return herramientas(crear_herramientas_analista(inv, libreta)), libreta


async def test_autocompleta_con_las_metricas_registradas_y_encadena_calculos(analista):
    h, libreta = analista
    r = await invocar(h["calcular_costo_llm"])
    assert r["resultado"]["costo_llm_mensual_usd"] == 16800
    assert "precio_entrada_usd_millon" in r["autocompletadas"]
    r = await invocar(h["calcular_unit_economics"])
    assert "costo_llm_mensual_usd" in r["autocompletadas"]  # sale del cálculo anterior
    assert r["resultado"]["margen_bruto_pct"] == pytest.approx(56.42, abs=0.01)
    r = await invocar(h["calcular_scoring"], puntaje_equipo=3, justificacion_equipo="CTO part-time, CEO comercial.", puntaje_mercado=4, justificacion_mercado="Mercado grande y en crecimiento.")
    assert r["resultado"]["score"] == 60 and r["resultado"]["banda"] == "profundizar"
    assert set(libreta.calculos) == {"calcular_costo_llm", "calcular_unit_economics", "calcular_scoring"}


async def test_rechaza_entradas_sin_respaldo(analista):
    h, libreta = analista
    r = await invocar(h["calcular_unit_economics"], mrr_usd=100000, costo_llm_mensual_usd=5000)
    campos = {p["campo"]: p for p in r["campos"]}
    assert "120000" in campos["mrr_usd"]["sugerencia"]
    assert "calculalo primero" in campos["costo_llm_mensual_usd"]["sugerencia"]
    assert libreta.calculos == {} and libreta.rechazos == 1


async def test_los_juicios_son_libres_pero_los_numeros_no(analista):
    h, _ = analista
    r = await invocar(h["calcular_costo_llm"], variacion_precio_pct=40)
    assert r["resultado"]["escenario"]["costo_llm_mensual_usd"] == pytest.approx(23520)
    r = await invocar(h["estimar_tamano_mercado"], cuota_som_pct=2, justificacion_som="Competencia intensa y precios en baja.")
    assert r["resultado"]["tam_usd"] == 9800 * 54000


async def test_sin_datos_de_llm_no_calcula_el_costo():
    inv = investigacion_desde(METRICAS_VOZACTIVA_PITCH[:5])
    h = herramientas(crear_herramientas_analista(inv, LibretaAnalisis()))
    r = await invocar(h["calcular_costo_llm"])
    assert "Faltan datos de uso" in r["error"]


# --- Crítico ---------------------------------------------------------------------------------


def test_contraste_detecta_margen_y_runway_inflados():
    inv = investigacion_desde(METRICAS_VOZACTIVA_PITCH)
    discrepancias = contrastar(inv, analisis_con({"calcular_unit_economics": {"margen_bruto_pct": 41.89, "runway_meses": 7.4}}))
    assert [(d.metrica_declarada, d.severidad) for d in discrepancias] == [
        ("margen_bruto_declarado_pct", "alta"),
        ("runway_declarado_meses", "alta"),
    ]


def test_contraste_tolera_diferencias_chicas():
    inv = investigacion_desde([metrica("margen_bruto_declarado_pct", 72, "contralia/finanzas#2", "Margen bruto declarado: 72 %")])
    assert contrastar(inv, analisis_con({"calcular_unit_economics": {"margen_bruto_pct": 71.3}})) == []


CORRECCION_MRR = {
    "agente_afectado": "investigador",
    "severidad": "alta",
    "descripcion": "mrr_usd debe ser el MRR recurrente, no el facturado con pilotos.",
    "metrica": "mrr_usd",
    "valor_correcto": 90000,
    "fuente": "vozactiva/metricas#1",
    "cita": "MRR recurrente (contratos mensuales): USD 90.000",
}


async def test_observaciones_exigen_evidencia_literal(corpus):
    libreta = LibretaCritica()
    h = herramientas(crear_herramientas_critico(corpus, "vozactiva", investigacion_desde(METRICAS_VOZACTIVA_PITCH), None, None, libreta))
    r = await invocar(
        h["registrar_observaciones"],
        red_flags=[
            {"titulo": "Concentración en TelcoAndina", "severidad": "alta", "explicacion": "46 % del MRR y vence en marzo.", "fuente": "vozactiva/clientes-y-equipo#1", "cita": "El contrato con TelcoAndina vence el 31/03/2027"},
            {"titulo": "Fraude contable", "severidad": "alta", "explicacion": "Sin evidencia.", "fuente": "vozactiva/finanzas#2", "cita": "los números fueron inventados por el CEO"},
        ],
        correcciones=[CORRECCION_MRR, {"agente_afectado": "analista", "severidad": "media", "descripcion": "mal"}],
    )
    assert [a.get("red_flag") or a.get("correccion")[:7] for a in r["aceptadas"]] == ["Concentración en TelcoAndina", "mrr_usd"]
    assert len(r["rechazadas"]) == 2
    assert libreta.red_flags[0].fuente == "vozactiva/clientes-y-equipo#1"
    assert libreta.correcciones[0].metrica == "mrr_usd"


@pytest.mark.parametrize(
    ("cambios", "error"),
    [
        ({"metrica": None, "valor_correcto": None}, "necesita metrica, valor_correcto"),  # es una red flag disfrazada
        ({"valor_correcto": 95000}, "no aparece en la cita"),
        ({"metrica": "churn_mensual_pct", "valor_correcto": 6.5, "cita": "Churn mensual de clientes recurrentes: 6,5 %", "fuente": "vozactiva/metricas#4"}, "ya está registrada con 6.5"),
    ],
)
async def test_correcciones_al_investigador_verificables(corpus, cambios, error):
    h = herramientas(crear_herramientas_critico(corpus, "vozactiva", investigacion_desde(METRICAS_VOZACTIVA_PITCH), None, None, LibretaCritica()))
    r = await invocar(h["registrar_observaciones"], correcciones=[{**CORRECCION_MRR, **cambios}])
    assert r["aceptadas"] == [] and error in r["rechazadas"][0]["error"]


async def test_no_repite_correcciones_y_limita_los_registros(corpus):
    h = herramientas(crear_herramientas_critico(corpus, "vozactiva", investigacion_desde(METRICAS_VOZACTIVA_PITCH), None, None, LibretaCritica()))
    primera = await invocar(h["registrar_observaciones"], correcciones=[CORRECCION_MRR])
    segunda = await invocar(h["registrar_observaciones"], correcciones=[{**CORRECCION_MRR, "descripcion": "Otra vez: el MRR tiene que ser el recurrente."}])
    assert len(primera["aceptadas"]) == 1 and "ya fue emitida" in segunda["rechazadas"][0]["error"]
    tercera = await invocar(h["registrar_observaciones"], red_flags=[])
    assert "terminá con tu resumen" in tercera["sugerencia"]
