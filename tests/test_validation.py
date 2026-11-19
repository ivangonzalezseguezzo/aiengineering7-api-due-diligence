"""Rúbrica de suficiencia: qué bloquea el cierre, qué pasa con advertencia y en qué orden se resuelve."""

from due_diligence.config import DDSettings
from due_diligence.state import Correccion, Critica
from due_diligence.validation import calculos_requeridos, evaluar, siguiente_pendiente

from .conftest import CALCULOS_COMPLETOS, METRICAS_VOZACTIVA_PITCH, analisis_con, estado, investigacion_desde

UNO_CADA_UNO = {"investigador": 1, "analista": 1, "critico": 1}


def checks(e, settings=None) -> dict:
    return {c.nombre: c for c in evaluar(e, settings or DDSettings())}


def completo(**cambios):
    base = dict(
        investigacion=investigacion_desde(METRICAS_VOZACTIVA_PITCH),
        analisis=analisis_con(CALCULOS_COMPLETOS),
        critica=Critica(version=1, version_analisis=1),
        intentos=dict(UNO_CADA_UNO),
    )
    base.update(cambios)
    return estado(**base)


def test_estado_vacio_empieza_por_el_investigador():
    assert not checks(estado())["investigacion"].ok
    assert siguiente_pendiente(estado(), DDSettings())[0] == "investigador"


def test_estado_completo_aprueba_sin_advertencias():
    resultado = checks(completo())
    assert all(c.ok and not c.advertencia for c in resultado.values())
    assert siguiente_pendiente(completo(), DDSettings()) == ("finalizar", "")


def test_analisis_viejo_vuelve_al_analista():
    e = completo(investigacion=investigacion_desde(METRICAS_VOZACTIVA_PITCH, version=2))
    assert "usa la investigación v1" in checks(e)["analisis_vigente"].detalle
    assert siguiente_pendiente(e, DDSettings())[0] == "analista"


def test_critica_vieja_vuelve_al_critico():
    e = completo(analisis=analisis_con(CALCULOS_COMPLETOS, version=2))
    assert not checks(e)["critica_vigente"].ok
    assert siguiente_pendiente(e, DDSettings())[0] == "critico"


def test_correccion_alta_pendiente_va_primero_al_dueno_del_dato():
    critica = Critica(
        version=1,
        version_analisis=1,
        correcciones=[
            Correccion(id="C1", agente_afectado="investigador", severidad="alta", descripcion="Usar el MRR recurrente."),
            Correccion(id="C2", agente_afectado="analista", severidad="alta", descripcion="Recalcular con el MRR recurrente."),
            Correccion(id="C3", agente_afectado="analista", severidad="media", descripcion="Sumar el escenario de precio."),
        ],
    )
    e = completo(critica=critica)
    resultado = checks(e)
    assert "C1" in resultado["correcciones_investigador"].detalle
    assert "C2" in resultado["correcciones_analista"].detalle and "C3" not in resultado["correcciones_analista"].detalle
    assert siguiente_pendiente(e, DDSettings())[0] == "investigador"


def test_metricas_faltantes_bloquean_hasta_la_segunda_busqueda():
    pocas = investigacion_desde(METRICAS_VOZACTIVA_PITCH[:8])
    primera = checks(completo(investigacion=pocas))["metricas_clave"]
    assert not primera.ok and "llamadas_llm_mes" in primera.detalle
    segunda = checks(completo(investigacion=pocas, intentos={**UNO_CADA_UNO, "investigador": 2}))["metricas_clave"]
    assert segunda.ok and segunda.advertencia and "No disponibles" in segunda.detalle


def test_lo_irresoluble_pasa_con_advertencia():
    e = completo(analisis=analisis_con({}), intentos={**UNO_CADA_UNO, "analista": 3})
    analisis = checks(e)["analisis_vigente"]
    assert analisis.ok and analisis.advertencia and "sin más intentos" in analisis.detalle


def test_calculos_requeridos_segun_los_datos_disponibles():
    sin_llm = estado(investigacion=investigacion_desde(METRICAS_VOZACTIVA_PITCH[:12]))
    assert calculos_requeridos(sin_llm) == ["calcular_unit_economics", "calcular_scoring"]
    assert calculos_requeridos(completo()) == ["calcular_costo_llm", "calcular_unit_economics", "calcular_scoring", "estimar_tamano_mercado"]
