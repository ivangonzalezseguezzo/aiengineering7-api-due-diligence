"""Agente ANALISTA: convierte las métricas registradas en números de decisión, siempre con herramientas.

Herramientas (envuelven las fórmulas puras de `finanzas.py`):
- `calcular_costo_llm`: costo mensual del LLM, % del MRR y escenarios de uso o de precio.
- `calcular_unit_economics`: ARPA, margen bruto real, CAC, LTV, LTV/CAC, payback y runway.
- `estimar_tamano_mercado`: TAM/SAM/SOM bottom-up.
- `calcular_scoring`: score 0-100 con los umbrales y pesos de la tesis del fondo.

Regla de proveniencia (determinística, adentro de cada herramienta): toda entrada numérica tiene
que coincidir con la métrica registrada por el investigador que tiene ese nombre, o con el
resultado de un cálculo anterior que tiene ese nombre. Las únicas entradas libres son los juicios
explícitos (cuota_som_pct, multiplicador_uso, variacion_precio_pct y los puntajes de equipo y
mercado, que exigen justificación). Si una entrada no tiene respaldo, la herramienta no calcula y
explica qué valor usar: el analista no puede "inventar" un número que llegue al informe.
Los campos que el analista omite se completan con esos mismos respaldos.
"""

from dataclasses import dataclass, field
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field

from due_diligence import finanzas
from due_diligence.agents.comun import (
    a_json,
    correcciones_pendientes,
    error,
    formatear_correcciones,
    formatear_hallazgos,
    formatear_metricas,
)
from due_diligence.agents.react import crear_agente_react, ejecutar_especialista, texto_final
from due_diligence.config import DDSettings
from due_diligence.corpus import Corpus, mismo_numero
from due_diligence.formato import num, pct, usd
from due_diligence.state import METRICAS, METRICAS_CLAVE, Analisis, Aporte, Calculo, EntradaAnalista, Investigacion

JUICIOS = frozenset({"multiplicador_uso", "variacion_precio_pct", "cuota_som_pct", "puntaje_equipo", "puntaje_mercado"})

PROMPT = """\
Sos el ANALISTA financiero de un equipo de due diligence de startups de IA para un fondo de inversión.
Convertís las métricas que registró el investigador en números de decisión, SIEMPRE con tus herramientas.

Orden de trabajo:
1. calcular_costo_llm (si hay datos de uso del LLM). Si el data room menciona un ajuste de precio o
   un crecimiento de uso, sumá el escenario con variacion_precio_pct o multiplicador_uso.
   En la misma ronda podés llamar a estimar_tamano_mercado (cuota_som_pct es tu supuesto: justificalo).
2. calcular_unit_economics. Los campos que omitas se completan con las métricas registradas y con el
   costo de LLM ya calculado.
3. calcular_scoring: completá puntaje_equipo y puntaje_mercado (0 a 5) con una justificación basada en
   los hallazgos del investigador. El resto se completa con los cálculos anteriores.

Reglas:
- Usá solo métricas registradas o resultados de tus cálculos: las herramientas rechazan valores sin
  respaldo. No inventes ni estimes datos faltantes: omití el campo y la herramienta te dirá qué no pudo calcular.
- Tus únicos supuestos propios son cuota_som_pct, multiplicador_uso, variacion_precio_pct y los puntajes
  de equipo y mercado.
- Si recibís correcciones del crítico, aplicalas. Si una corrección pide cambiar un DATO registrado, no
  podés hacerlo vos (es del investigador): decilo en tu resumen.
- No buscás información ni evaluás red flags (eso es del investigador y del crítico).
- Terminá con una interpretación de 4 a 6 líneas: margen real después del costo de LLM, eficiencia
  comercial (LTV/CAC y payback), runway y qué dice el scoring.
"""


# --- Esquemas de las herramientas -------------------------------------------------------------


class CostoLLMInput(BaseModel):
    llamadas_llm_mes: float | None = None
    tokens_entrada_por_llamada: float | None = None
    tokens_salida_por_llamada: float | None = None
    precio_entrada_usd_millon: float | None = None
    precio_salida_usd_millon: float | None = None
    mrr_usd: float | None = None
    multiplicador_uso: float = Field(default=1.0, ge=0.1, le=100, description="Escenario: veces que crece el uso sin subir el precio (1 = sin escenario).")
    variacion_precio_pct: float = Field(default=0.0, ge=-90, le=500, description="Escenario: variación del precio por token en % (por ejemplo 40 para +40 %).")


class UnitEconomicsInput(BaseModel):
    mrr_usd: float | None = None
    clientes_activos: float | None = None
    churn_mensual_pct: float | None = None
    gasto_comercial_mensual_usd: float | None = None
    clientes_nuevos_mes: float | None = None
    costo_llm_mensual_usd: float | None = Field(default=None, description="Resultado de calcular_costo_llm.")
    costo_infra_mensual_usd: float | None = None
    otros_costos_directos_mensual_usd: float | None = None
    burn_neto_mensual_usd: float | None = None
    caja_usd: float | None = None


class MercadoInput(BaseModel):
    clientes_potenciales_tam: float | None = None
    ticket_anual_usd: float | None = None
    porcentaje_sam_pct: float | None = None
    cuota_som_pct: float = Field(ge=0.1, le=30, description="Cuota del SAM alcanzable en 5 años (tu supuesto; típico 1 a 10).")
    justificacion_som: str = Field(min_length=10, description="Por qué esa cuota.")


class ScoringInput(BaseModel):
    ltv_cac: float | None = None
    payback_meses: float | None = None
    margen_bruto_pct: float | None = None
    runway_meses: float | None = None
    crecimiento_mrr_mensual_pct: float | None = None
    concentracion_top_cliente_pct: float | None = None
    puntaje_equipo: int = Field(ge=0, le=5, description="0 a 5: experiencia y dedicación del equipo según los hallazgos.")
    justificacion_equipo: str = Field(min_length=10)
    puntaje_mercado: int = Field(ge=0, le=5, description="0 a 5: tamaño, crecimiento y competencia del mercado.")
    justificacion_mercado: str = Field(min_length=10)


# --- Proveniencia -----------------------------------------------------------------------------


def _salidas(calculos: dict[str, Calculo]) -> dict[str, float]:
    salidas: dict[str, float] = {}
    for calculo in calculos.values():
        for clave, valor in calculo.resultado.items():
            if isinstance(valor, (int, float)) and not isinstance(valor, bool):
                salidas[clave] = valor
    return salidas


def entradas_sin_respaldo(entradas: dict[str, Any], investigacion: Investigacion | None, calculos: dict[str, Calculo]) -> list[dict[str, Any]]:
    """Entradas numéricas que no coinciden con una métrica registrada ni con un cálculo previo del mismo nombre."""
    salidas = _salidas(calculos)
    problemas = []
    for campo, valor in entradas.items():
        if campo in JUICIOS or valor is None or isinstance(valor, bool) or not isinstance(valor, (int, float)):
            continue
        if campo in METRICAS:
            registrada = investigacion.valor(campo) if investigacion else None
            if registrada is None:
                problemas.append({"campo": campo, "valor": valor, "problema": "no hay una métrica registrada con ese nombre", "sugerencia": "omití el campo; si hace falta, decí en tu resumen que falta el dato"})
            elif not mismo_numero(valor, registrada):
                problemas.append({"campo": campo, "valor": valor, "problema": f"no coincide con la métrica registrada ({registrada:g})", "sugerencia": f"usá {registrada:g} u omití el campo"})
        elif campo in salidas:
            if not mismo_numero(valor, salidas[campo]):
                problemas.append({"campo": campo, "valor": valor, "problema": f"no coincide con el cálculo previo ({salidas[campo]:g})", "sugerencia": f"usá {salidas[campo]:g} u omití el campo"})
        else:
            problemas.append({"campo": campo, "valor": valor, "problema": "no proviene de ninguna métrica ni cálculo previo", "sugerencia": "calculalo primero con la herramienta que lo produce, u omití el campo"})
    return problemas


def _completar(entradas: dict[str, Any], investigacion: Investigacion | None, calculos: dict[str, Calculo]) -> tuple[dict[str, Any], list[str]]:
    """Completa los campos omitidos con la métrica registrada o el cálculo previo del mismo nombre."""
    salidas = _salidas(calculos)
    completas, autocompletadas = dict(entradas), []
    for campo, valor in entradas.items():
        if valor is not None or campo in JUICIOS:
            continue
        respaldo = investigacion.valor(campo) if investigacion and campo in METRICAS else salidas.get(campo)
        if respaldo is not None:
            completas[campo] = respaldo
            autocompletadas.append(campo)
    return completas, autocompletadas


@dataclass
class LibretaAnalisis:
    calculos: dict[str, Calculo] = field(default_factory=dict)
    rechazos: int = 0


def crear_herramientas_analista(investigacion: Investigacion | None, libreta: LibretaAnalisis) -> list[BaseTool]:
    def ejecutar(herramienta: str, entradas: dict[str, Any], calcular) -> str:
        if problemas := entradas_sin_respaldo(entradas, investigacion, libreta.calculos):
            libreta.rechazos += 1
            return error("Hay entradas sin respaldo: no se calculó nada.", "Corregí los campos indicados y volvé a llamar.", campos=problemas)
        completas, autocompletadas = _completar(entradas, investigacion, libreta.calculos)
        try:
            resultado = calcular(completas)
        except (TypeError, ZeroDivisionError) as exc:
            libreta.rechazos += 1
            faltan = sorted(k for k, v in completas.items() if v is None and k not in JUICIOS)
            return error(f"No se puede calcular: {exc}.", "Faltan datos obligatorios para este cálculo.", faltan=faltan)
        libreta.calculos[herramienta] = Calculo(
            herramienta=herramienta, entradas={k: v for k, v in completas.items() if v is not None}, resultado=resultado
        )
        return a_json({"resultado": resultado, "autocompletadas": autocompletadas})

    @tool("calcular_costo_llm", args_schema=CostoLLMInput)
    async def calcular_costo_llm(**entradas: Any) -> str:
        """Calcula el costo mensual del LLM (llamadas × tokens × precio), su peso sobre el MRR y un escenario opcional.

        Usala primero: su resultado costo_llm_mensual_usd alimenta a calcular_unit_economics.
        """
        def calcular(e: dict[str, Any]) -> dict[str, Any]:
            return finanzas.costo_llm(
                e["llamadas_llm_mes"], e["tokens_entrada_por_llamada"], e["tokens_salida_por_llamada"],
                e["precio_entrada_usd_millon"], e["precio_salida_usd_millon"], e.get("mrr_usd"),
                e.get("multiplicador_uso", 1.0), e.get("variacion_precio_pct", 0.0),
            )  # fmt: skip

        if any(entradas.get(m) is None and (investigacion is None or investigacion.valor(m) is None) for m in CostoLLMInput.model_fields if m in METRICAS and m != "mrr_usd"):
            libreta.rechazos += 1
            return error("Faltan datos de uso o de precio del LLM registrados por el investigador.", "Sin esos datos no se calcula el costo: mencionalo en tu resumen.")
        return ejecutar("calcular_costo_llm", CostoLLMInput(**entradas).model_dump(), calcular)

    @tool("calcular_unit_economics", args_schema=UnitEconomicsInput)
    async def calcular_unit_economics(**entradas: Any) -> str:
        """Calcula ARPA, margen bruto real (incluye el costo del LLM), CAC, LTV, LTV/CAC, payback y runway.

        Lo que no se pueda calcular por falta de datos se informa en 'no_calculables'.
        """
        return ejecutar("calcular_unit_economics", UnitEconomicsInput(**entradas).model_dump(), lambda e: finanzas.unit_economics(**e))

    @tool("estimar_tamano_mercado", args_schema=MercadoInput)
    async def estimar_tamano_mercado(**entradas: Any) -> str:
        """Estima TAM, SAM y SOM bottom-up (anual, en USD) con las métricas de mercado registradas y tu cuota objetivo."""
        datos = MercadoInput(**entradas).model_dump()
        justificacion = datos.pop("justificacion_som")

        def calcular(e: dict[str, Any]) -> dict[str, Any]:
            return {**finanzas.tamano_mercado(e["clientes_potenciales_tam"], e["ticket_anual_usd"], e["porcentaje_sam_pct"], e["cuota_som_pct"]), "justificacion_som": justificacion}

        return ejecutar("estimar_tamano_mercado", datos, calcular)

    @tool("calcular_scoring", args_schema=ScoringInput)
    async def calcular_scoring(**entradas: Any) -> str:
        """Calcula el score de inversión 0-100 con los umbrales y pesos de la tesis del fondo. Usala al final.

        Los criterios sin dato suman 0 y se listan en 'sin_datos'.
        """
        datos = ScoringInput(**entradas).model_dump()
        justificaciones = {k: datos.pop(k) for k in ("justificacion_equipo", "justificacion_mercado")}

        def calcular(e: dict[str, Any]) -> dict[str, Any]:
            return {**finanzas.scoring(**e), **justificaciones}

        return ejecutar("calcular_scoring", datos, calcular)

    return [calcular_costo_llm, calcular_unit_economics, estimar_tamano_mercado, calcular_scoring]


def resumen_calculos(analisis: Analisis | None) -> str:
    """Una línea con los números principales del análisis (para el tablero y las trazas)."""
    if analisis is None or not analisis.calculos:
        return "sin cálculos"
    llm = analisis.resultado("calcular_costo_llm")
    ue = analisis.resultado("calcular_unit_economics")
    sc = analisis.resultado("calcular_scoring")
    partes = []
    if llm:
        partes.append(f"costo LLM {usd(llm.get('costo_llm_mensual_usd'))}/mes ({pct(llm.get('costo_llm_pct_mrr'))} del MRR)")
    if ue:
        partes.append(f"margen {pct(ue.get('margen_bruto_pct'))} · LTV/CAC {num(ue.get('ltv_cac'), 2)} · payback {num(ue.get('payback_meses'), 1)} m · runway {num(ue.get('runway_meses'), 1)} m")
    if sc:
        partes.append(f"score {num(sc['score'], 1)} ({sc['banda']})")
    return " · ".join(partes)


def mensaje_para_analista(nombre: str, startup: str, instruccion: str, investigacion: Investigacion | None, correcciones: list) -> str:
    faltan = investigacion.faltantes() if investigacion else list(METRICAS_CLAVE)
    return f"""\
Startup: {nombre} (id: {startup}).

Instrucción del supervisor: {instruccion or "Calculá costo de LLM, unit economics, tamaño de mercado y scoring."}

Métricas registradas por el investigador (usá exactamente estos valores):
{formatear_metricas(investigacion, con_definicion=True)}

Métricas clave no disponibles: {", ".join(faltan) or "ninguna"}

Hallazgos del investigador (para los puntajes de equipo y mercado):
{formatear_hallazgos(investigacion)}

Correcciones del crítico dirigidas a vos:
{formatear_correcciones(correcciones)}"""


def crear_nodo_analista(model: BaseChatModel, corpus: Corpus, settings: DDSettings, *, reintentos_llm: int = 0):
    async def analista(state: EntradaAnalista) -> dict[str, Any]:
        investigacion = state.get("investigacion")
        previa = state.get("analisis")
        atendidas_antes = previa.correcciones_atendidas if previa else []
        correcciones = correcciones_pendientes(state.get("critica"), "analista", atendidas_antes)
        libreta = LibretaAnalisis()  # cada versión recalcula todo con la investigación vigente
        agente = crear_agente_react(
            model,
            crear_herramientas_analista(investigacion, libreta),
            PROMPT,
            nombre="analista",
            max_iteraciones=settings.max_iteraciones_especialista,
            reintentos_llm=reintentos_llm,
        )
        startup = state["startup"]
        mensaje = mensaje_para_analista(corpus.startups[startup], startup, state.get("instruccion", ""), investigacion, correcciones)
        mensajes = await ejecutar_especialista(agente, mensaje, nombre="analista", max_iteraciones=settings.max_iteraciones_especialista)
        interpretacion = texto_final(mensajes) or "(el analista no dejó interpretación)"
        analisis = Analisis(
            version=(previa.version + 1) if previa else 1,
            version_investigacion=investigacion.version if investigacion else 1,
            calculos=libreta.calculos,
            rechazos=libreta.rechazos,
            correcciones_atendidas=atendidas_antes + [c.id for c in correcciones],
            interpretacion=interpretacion,
        )
        detalle = f"v{analisis.version}: {resumen_calculos(analisis)}; {analisis.rechazos} llamadas rechazadas por falta de respaldo"
        if correcciones:
            detalle += f"; correcciones atendidas: {', '.join(c.id for c in correcciones)}"
        return {
            "analisis": analisis,
            "aportes": [Aporte(paso=state["pasos"], agente="analista", accion="análisis", detalle=detalle)],
            "messages": [AIMessage(content=f"{detalle}\n{interpretacion}", name="analista")],
        }

    return analista
