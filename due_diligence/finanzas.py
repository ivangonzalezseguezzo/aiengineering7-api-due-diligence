"""Fórmulas de due diligence (puras, sin LLM): costo de LLM, unit economics, mercado y scoring.

Son el motor de las herramientas del analista. Que vivan acá, separadas de los agentes, garantiza
que todos los números del informe salen de código determinístico y testeado, no del modelo.
Las funciones aceptan datos faltantes: calculan lo que pueden y explican lo que no.

Fórmulas (también en el README):
- costo LLM mensual = llamadas × (tokens_in × precio_in + tokens_out × precio_out) / 1.000.000
- ARPA = MRR / clientes;  margen bruto = (MRR − costos directos) / MRR
- CAC = gasto comercial mensual / clientes nuevos por mes
- LTV = ARPA × margen bruto / churn mensual;  payback = CAC / (ARPA × margen bruto)
- runway = caja / burn neto mensual
- TAM = clientes potenciales × ticket anual;  SAM = TAM × % SAM;  SOM = SAM × cuota objetivo
"""

from typing import Any

# Ponderación del scoring: la misma que publica la tesis del fondo (data/fondo/tesis-de-inversion.md).
PESOS: dict[str, int] = {
    "unit_economics": 25,
    "margen_bruto": 20,
    "runway": 15,
    "crecimiento": 15,
    "equipo": 10,
    "mercado": 10,
    "concentracion": 5,
}
# (umbral, puntaje 0-5). "mayor" = cuanto más alto el valor, mejor.
_UMBRALES: dict[str, tuple[str, list[tuple[float, int]]]] = {
    "ltv_cac": ("mayor", [(3, 5), (2, 3), (1, 1)]),
    "payback_meses": ("menor", [(12, 5), (18, 4), (24, 3), (36, 1)]),
    "margen_bruto_pct": ("mayor", [(70, 5), (60, 4), (50, 3), (40, 2), (30, 1)]),
    "runway_meses": ("mayor", [(18, 5), (12, 4), (9, 3), (6, 2), (3, 1)]),
    "crecimiento_mrr_mensual_pct": ("mayor", [(15, 5), (10, 4), (7, 3), (4, 2), (1, 1)]),
    "concentracion_top_cliente_pct": ("menor", [(10, 5), (20, 4), (30, 3), (40, 2), (50, 1)]),
}
BANDAS = ((70, "avanzar a comité"), (50, "profundizar"), (0, "no avanzar"))


def _r(valor: float, decimales: int = 2) -> float:
    return round(valor, decimales)


def costo_llm(
    llamadas_llm_mes: float,
    tokens_entrada_por_llamada: float,
    tokens_salida_por_llamada: float,
    precio_entrada_usd_millon: float,
    precio_salida_usd_millon: float,
    mrr_usd: float | None = None,
    multiplicador_uso: float = 1.0,
    variacion_precio_pct: float = 0.0,
) -> dict[str, Any]:
    """Costo mensual del LLM, su peso sobre el MRR y (opcional) un escenario de más uso o de otro precio."""
    entrada = llamadas_llm_mes * tokens_entrada_por_llamada / 1e6 * precio_entrada_usd_millon
    salida = llamadas_llm_mes * tokens_salida_por_llamada / 1e6 * precio_salida_usd_millon
    total = entrada + salida
    resultado: dict[str, Any] = {
        "costo_llm_mensual_usd": _r(total),
        "costo_entrada_usd": _r(entrada),
        "costo_salida_usd": _r(salida),
        "costo_por_llamada_usd": _r(total / llamadas_llm_mes, 6) if llamadas_llm_mes else None,
    }
    if mrr_usd:
        resultado["costo_llm_pct_mrr"] = _r(total / mrr_usd * 100)
    if multiplicador_uso != 1 or variacion_precio_pct:
        costo_escenario = total * multiplicador_uso * (1 + variacion_precio_pct / 100)
        escenario: dict[str, Any] = {
            "multiplicador_uso": multiplicador_uso,
            "variacion_precio_pct": variacion_precio_pct,
            "costo_llm_mensual_usd": _r(costo_escenario),
        }
        if mrr_usd:
            # El costo cambia pero lo que paga el cliente no: la diferencia sale del margen.
            escenario["costo_llm_pct_mrr"] = _r(costo_escenario / mrr_usd * 100)
        resultado["escenario"] = escenario
    return resultado


def unit_economics(
    *,
    mrr_usd: float | None = None,
    clientes_activos: float | None = None,
    churn_mensual_pct: float | None = None,
    gasto_comercial_mensual_usd: float | None = None,
    clientes_nuevos_mes: float | None = None,
    costo_llm_mensual_usd: float | None = None,
    costo_infra_mensual_usd: float | None = None,
    otros_costos_directos_mensual_usd: float | None = None,
    burn_neto_mensual_usd: float | None = None,
    caja_usd: float | None = None,
) -> dict[str, Any]:
    """ARPA, margen bruto, CAC, LTV, LTV/CAC, payback y runway con lo que haya; explica lo que falta."""
    r: dict[str, Any] = {}
    no_calculables: dict[str, str] = {}
    advertencias: list[str] = []

    arpa = mrr_usd / clientes_activos if mrr_usd and clientes_activos else None
    if arpa is None:
        no_calculables["arpa_usd"] = "faltan mrr_usd o clientes_activos"
    else:
        r["arpa_usd"] = _r(arpa)

    costos = {
        "costo_llm_mensual_usd": costo_llm_mensual_usd,
        "costo_infra_mensual_usd": costo_infra_mensual_usd,
        "otros_costos_directos_mensual_usd": otros_costos_directos_mensual_usd,
    }
    incluidos = {k: v for k, v in costos.items() if v is not None}
    margen = None
    if not mrr_usd:
        no_calculables["margen_bruto_pct"] = "falta mrr_usd"
    elif not incluidos:
        no_calculables["margen_bruto_pct"] = "faltan los costos directos"
    else:
        margen = (mrr_usd - sum(incluidos.values())) / mrr_usd * 100
        r["margen_bruto_pct"] = _r(margen)
        r["costos_directos_mensual_usd"] = _r(sum(incluidos.values()))
        if faltan := [k for k in costos if k not in incluidos]:
            advertencias.append(f"El margen no incluye {', '.join(faltan)}: puede estar sobreestimado.")

    cac = None
    if gasto_comercial_mensual_usd is None or not clientes_nuevos_mes:
        no_calculables["cac_usd"] = "faltan gasto_comercial_mensual_usd o clientes_nuevos_mes"
    else:
        cac = gasto_comercial_mensual_usd / clientes_nuevos_mes
        r["cac_usd"] = _r(cac)

    margen_por_cliente = arpa * margen / 100 if arpa is not None and margen is not None else None
    if margen_por_cliente is not None:
        r["margen_mensual_por_cliente_usd"] = _r(margen_por_cliente)

    if margen_por_cliente is None or churn_mensual_pct is None:
        no_calculables["ltv_usd"] = "faltan ARPA, margen bruto o churn_mensual_pct"
    elif churn_mensual_pct <= 0:
        no_calculables["ltv_usd"] = "churn 0: el LTV no está acotado"
    else:
        ltv = margen_por_cliente / (churn_mensual_pct / 100)
        r["ltv_usd"] = _r(ltv)
        if cac:
            r["ltv_cac"] = _r(ltv / cac)
        else:
            no_calculables["ltv_cac"] = "falta el CAC"

    if cac is None or margen_por_cliente is None:
        no_calculables["payback_meses"] = "faltan el CAC o el margen por cliente"
    elif margen_por_cliente <= 0:
        no_calculables["payback_meses"] = "margen por cliente negativo: el CAC no se recupera"
    else:
        r["payback_meses"] = _r(cac / margen_por_cliente, 1)

    if caja_usd is None or burn_neto_mensual_usd is None:
        no_calculables["runway_meses"] = "faltan caja_usd o burn_neto_mensual_usd"
    elif burn_neto_mensual_usd <= 0:
        advertencias.append("La empresa no quema caja: el runway no está acotado.")
    else:
        r["runway_meses"] = _r(caja_usd / burn_neto_mensual_usd, 1)

    if no_calculables:
        r["no_calculables"] = no_calculables
    if advertencias:
        r["advertencias"] = advertencias
    return r


def tamano_mercado(
    clientes_potenciales_tam: float,
    ticket_anual_usd: float,
    porcentaje_sam_pct: float,
    cuota_som_pct: float,
) -> dict[str, Any]:
    """TAM/SAM/SOM bottom-up (anual, en USD)."""
    tam = clientes_potenciales_tam * ticket_anual_usd
    sam = tam * porcentaje_sam_pct / 100
    som = sam * cuota_som_pct / 100
    return {
        "tam_usd": _r(tam, 0),
        "sam_usd": _r(sam, 0),
        "som_usd": _r(som, 0),
        "clientes_som": _r(clientes_potenciales_tam * porcentaje_sam_pct / 100 * cuota_som_pct / 100, 0),
    }


def puntaje(criterio: str, valor: float | None) -> int | None:
    """Puntaje 0-5 de una métrica según los umbrales de la tesis del fondo (None si no hay dato)."""
    if valor is None:
        return None
    sentido, escalones = _UMBRALES[criterio]
    for umbral, nota in escalones:
        if (valor >= umbral) if sentido == "mayor" else (valor <= umbral):
            return nota
    return 0


def banda(score: float) -> str:
    return next(nombre for minimo, nombre in BANDAS if score >= minimo)


def scoring(
    *,
    ltv_cac: float | None = None,
    payback_meses: float | None = None,
    margen_bruto_pct: float | None = None,
    runway_meses: float | None = None,
    crecimiento_mrr_mensual_pct: float | None = None,
    concentracion_top_cliente_pct: float | None = None,
    puntaje_equipo: int,
    puntaje_mercado: int,
) -> dict[str, Any]:
    """Score de inversión 0-100: criterios cuantitativos por umbrales + equipo y mercado (juicio 0-5).

    Un criterio sin dato suma 0 y queda listado: la falta de información penaliza.
    """
    sin_datos: list[str] = []

    def nota(criterio: str, valor: float | None) -> int:
        resultado = puntaje(criterio, valor)
        if resultado is None:
            sin_datos.append(criterio)
            return 0
        return resultado

    criterios = {
        "unit_economics": (nota("ltv_cac", ltv_cac) + nota("payback_meses", payback_meses)) / 2,
        "margen_bruto": nota("margen_bruto_pct", margen_bruto_pct),
        "runway": nota("runway_meses", runway_meses),
        "crecimiento": nota("crecimiento_mrr_mensual_pct", crecimiento_mrr_mensual_pct),
        "equipo": max(0, min(5, puntaje_equipo)),
        "mercado": max(0, min(5, puntaje_mercado)),
        "concentracion": nota("concentracion_top_cliente_pct", concentracion_top_cliente_pct),
    }
    detalle = {
        nombre: {"puntaje_0_5": valor, "peso_pct": PESOS[nombre], "aporte": _r(PESOS[nombre] * valor / 5, 1)}
        for nombre, valor in criterios.items()
    }
    total = _r(sum(d["aporte"] for d in detalle.values()), 1)
    return {"score": total, "banda": banda(total), "criterios": detalle, "sin_datos": sin_datos}
