"""Fixtures compartidas: el corpus real, modelos guionados y las métricas de VozActiva con su cita.

El grafo, los nodos, las herramientas, el anclaje y las fórmulas son los reales: lo único falso
es el LLM, que devuelve decisiones y tool_calls predefinidas en lugar de decidirlas.
"""

import os
import uuid
from typing import Any

# Los tests nunca mandan trazas a LangSmith: la app de prueba carga el .env (con
# LANGSMITH_TRACING=true) y, sin esto, los grafos guionados ensuciaban el proyecto real.
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableLambda
from pydantic import Field

from due_diligence.agents.supervisor import DecisionSupervisor
from due_diligence.config import DDSettings
from due_diligence.corpus import Corpus
from due_diligence.state import Analisis, Calculo, Hallazgo, Investigacion, MetricaRegistrada, estado_inicial


class ScriptedChatModel(BaseChatModel):
    """Devuelve, en orden, las respuestas recibidas y registra lo que le llega en cada llamada.

    Sirve tanto para bind_tools (AIMessage con tool_calls) como para with_structured_output
    (DecisionSupervisor). Si una "respuesta" es una excepción, la lanza.
    """

    respuestas: list[Any]
    recibidos: list[list[BaseMessage]] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def _siguiente(self, messages: list[BaseMessage]) -> Any:
        self.recibidos.append(list(messages))
        if not self.respuestas:
            raise RuntimeError("el modelo guionado se quedó sin respuestas")
        siguiente = self.respuestas.pop(0)
        if isinstance(siguiente, BaseException):
            raise siguiente
        return siguiente

    def _generate(self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kwargs: Any) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self._siguiente(messages))])

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ScriptedChatModel":
        return self

    def with_structured_output(self, schema: Any, **kwargs: Any) -> RunnableLambda:
        return RunnableLambda(lambda mensajes: self._siguiente(list(mensajes)))


def llamadas(*pedidos: tuple[str, dict[str, Any]]) -> AIMessage:
    """Un mensaje del modelo con una o varias tool_calls en paralelo."""
    return AIMessage(
        content="",
        tool_calls=[{"name": nombre, "args": args, "id": f"call_{uuid.uuid4().hex[:8]}"} for nombre, args in pedidos],
    )


def llamada(herramienta: str, **argumentos: Any) -> AIMessage:
    return llamadas((herramienta, argumentos))


def respuesta(texto: str) -> AIMessage:
    return AIMessage(content=texto)


def decision(siguiente: str, instruccion: str = "", razonamiento: str = "Según la rúbrica.") -> DecisionSupervisor:
    return DecisionSupervisor(razonamiento=razonamiento, siguiente=siguiente, instruccion=instruccion)


def metrica(nombre: str, valor: float, fuente: str, cita: str) -> dict[str, Any]:
    return {"metrica": nombre, "valor": valor, "fuente": fuente, "cita": cita}


# Métricas de VozActiva tal como las registraría un investigador desprolijo: MRR, clientes y
# crecimiento tomados del pitch (incluyen pilotos). El crítico lo detecta y lo corrige.
METRICAS_VOZACTIVA_PITCH = [
    metrica("mrr_usd", 120000, "vozactiva/pitch#3", "MRR de USD 120.000"),
    metrica("clientes_activos", 26, "vozactiva/pitch#3", "26 clientes en México, Colombia y Argentina"),
    metrica("crecimiento_mrr_mensual_pct", 14, "vozactiva/pitch#3", "Crecimiento de 14 % mensual en los últimos seis meses"),
    metrica("churn_mensual_pct", 6.5, "vozactiva/metricas#4", "Churn mensual de clientes recurrentes: 6,5 %"),
    metrica("clientes_nuevos_mes", 2.5, "vozactiva/metricas#4", "Clientes recurrentes nuevos por mes (promedio de los últimos 6 meses): 2,5"),
    metrica("gasto_comercial_mensual_usd", 78000, "vozactiva/finanzas#3", "Gasto mensual en ventas y marketing: USD 78.000"),
    metrica("costo_infra_mensual_usd", 9500, "vozactiva/finanzas#1", "Infraestructura cloud (sin incluir el LLM): USD 9.500 por mes"),
    metrica("otros_costos_directos_mensual_usd", 26000, "vozactiva/finanzas#1", "Otros costos directos totales (soporte más voz y telefonía): USD 26.000 por mes"),
    metrica("margen_bruto_declarado_pct", 74, "vozactiva/finanzas#2", "Margen bruto informado: 74 %"),
    metrica("burn_neto_mensual_usd", 95000, "vozactiva/finanzas#4", "Burn neto mensual: USD 95.000"),
    metrica("caja_usd", 700000, "vozactiva/finanzas#4", "Caja disponible al 31/10/2026: USD 700.000"),
    metrica("runway_declarado_meses", 20, "vozactiva/finanzas#4", "Runway informado: 20 meses"),
    metrica("llamadas_llm_mes", 2400000, "vozactiva/tecnologia#2", "Llamadas al LLM por mes: 2.400.000"),
    metrica("tokens_entrada_por_llamada", 1800, "vozactiva/tecnologia#2", "Tokens de entrada promedio por llamada: 1.800"),
    metrica("tokens_salida_por_llamada", 250, "vozactiva/tecnologia#2", "Tokens de salida promedio por llamada: 250"),
    metrica("precio_entrada_usd_millon", 2.5, "vozactiva/tecnologia#3", "Precio por millón de tokens de entrada: USD 2,50"),
    metrica("precio_salida_usd_millon", 10, "vozactiva/tecnologia#3", "Precio por millón de tokens de salida: USD 10"),
    metrica("concentracion_top_cliente_pct", 46, "vozactiva/clientes-y-equipo#1", "El cliente más grande, TelcoAndina, representa el 46 % del MRR recurrente"),
    metrica("clientes_potenciales_tam", 9800, "mercado/contact-centers-latam#1", "Centros de contacto con 50 o más posiciones en la región: 9.800"),
    metrica("ticket_anual_usd", 54000, "mercado/contact-centers-latam#2", "Ticket anual mediano de una plataforma de agentes de voz con IA: USD 54.000"),
    metrica("porcentaje_sam_pct", 57.1, "mercado/contact-centers-latam#1", "México, Colombia y Argentina concentran 5.600 centros, el 57,1 % del total"),
]

CORRECCION_MRR_VOZACTIVA = [
    metrica("mrr_usd", 90000, "vozactiva/metricas#1", "MRR recurrente (contratos mensuales): USD 90.000"),
    metrica("clientes_activos", 20, "vozactiva/metricas#2", "Clientes con contrato recurrente: 20"),
    metrica("crecimiento_mrr_mensual_pct", 4, "vozactiva/metricas#3", "Crecimiento mensual del MRR recurrente (últimos 6 meses): 4 %"),
]

HALLAZGOS_VOZACTIVA = [
    {
        "tema": "equipo",
        "afirmacion": "La CTO trabaja con dedicación parcial.",
        "fuente": "vozactiva/tecnologia#5",
        "cita": "La CTO, Lucía Benítez, trabaja con dedicación parcial (50 %) y asesora a otra empresa.",
    },
    {
        "tema": "riesgo",
        "afirmacion": "El MRR del pitch incluye pilotos que se cobran una sola vez.",
        "fuente": "vozactiva/metricas#1",
        "cita": "Pilotos pagos por única vez incluidos en el MRR facturado: USD 30.000.",
    },
]


def investigacion_desde(metricas: list[dict], version: int = 1, *, con_hallazgos: bool = True, **extra: Any) -> Investigacion:
    registradas = {
        m["metrica"]: MetricaRegistrada(metrica=m["metrica"], valor=m["valor"], fuente=m["fuente"], cita=m["cita"], ronda=version)
        for m in metricas
    }
    hallazgos = [Hallazgo(**h) for h in HALLAZGOS_VOZACTIVA] if con_hallazgos else []
    return Investigacion(version=version, metricas=registradas, hallazgos=hallazgos, **extra)


def analisis_con(calculos: dict[str, dict], version: int = 1, version_investigacion: int = 1, **extra: Any) -> Analisis:
    return Analisis(
        version=version,
        version_investigacion=version_investigacion,
        calculos={n: Calculo(herramienta=n, entradas={}, resultado=r) for n, r in calculos.items()},
        **extra,
    )


CALCULOS_COMPLETOS = {
    "calcular_costo_llm": {"costo_llm_mensual_usd": 16800, "costo_llm_pct_mrr": 18.67},
    "calcular_unit_economics": {"margen_bruto_pct": 41.89, "ltv_cac": 0.93, "payback_meses": 16.6, "runway_meses": 7.4},
    "estimar_tamano_mercado": {"tam_usd": 529_200_000, "sam_usd": 302_173_200, "som_usd": 6_043_464, "justificacion_som": "x"},
    "calcular_scoring": {"score": 41, "banda": "no avanzar", "criterios": {}, "sin_datos": []},
}


def estado(**cambios: Any) -> dict[str, Any]:
    """Estado del grafo para tests unitarios de nodos: el inicial más los cambios pedidos."""
    base = estado_inicial("vozactiva", "Due diligence de prueba.")
    base.update(cambios)
    return base


@pytest.fixture(scope="session")
def corpus() -> Corpus:
    return Corpus.desde_directorio(DDSettings().data_dir)


# Los tests heredados de la PE6 guionan una decisión del LLM en CADA paso del supervisor y la
# lectura del data room con herramientas: corren con el supervisor clásico y sin precarga. El modo
# híbrido y la precarga de la PE7 tienen sus propios tests (test_supervisor_hibrido.py).
MODO_PE6 = {"supervisor_hibrido": False, "precargar_data_room": False}


@pytest.fixture
def settings() -> DDSettings:
    return DDSettings(**MODO_PE6)


# --- PE7: Redis falso, grafo de producción y guion del camino feliz ------------------------------

SCORING_ARGS = {
    "puntaje_equipo": 2,
    "justificacion_equipo": "CTO con dedicación parcial.",
    "puntaje_mercado": 3,
    "justificacion_mercado": "Mercado grande con precios en baja.",
}


def guion_analista() -> list:
    """El analista en 4 rondas: costo LLM + mercado, unit economics, scoring e interpretación."""
    return [
        llamadas(("calcular_costo_llm", {}), ("estimar_tamano_mercado", {"cuota_som_pct": 2, "justificacion_som": "Competencia intensa."})),
        llamada("calcular_unit_economics"),
        llamada("calcular_scoring", **SCORING_ARGS),
        respuesta("Margen real bajo después del costo de LLM y voz; LTV/CAC flojo; runway corto."),
    ]


def guion_feliz(supervisor: list | None = None, critico_extra: list | None = None) -> dict[str, list]:
    """VozActiva con el supervisor híbrido y la precarga: 7 llamadas al LLM, ninguna del supervisor.

    El investigador registra todo en su primera ronda (el data room ya viene en el mensaje).
    """
    return {
        "supervisor": list(supervisor or []),
        "investigador": [
            llamadas(
                ("registrar_metricas", {"metricas": CORRECCION_MRR_VOZACTIVA + METRICAS_VOZACTIVA_PITCH[3:]}),
                ("registrar_hallazgos", {"hallazgos": HALLAZGOS_VOZACTIVA}),
            ),
            respuesta("Registré las métricas recurrentes; el pitch suma pilotos."),
        ],
        "analista": guion_analista(),
        "critico": [respuesta("Sin observaciones nuevas.")] + list(critico_extra or []),
    }


def modelos_guionados(guion: dict[str, list]):
    from due_diligence.graph import Modelos

    return Modelos(**{rol: ScriptedChatModel(respuestas=list(respuestas)) for rol, respuestas in guion.items()})


@pytest.fixture
async def redis_falso():
    import fakeredis

    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    yield redis
    await redis.aclose()


@pytest.fixture
def store(redis_falso):
    from app.jobs import JobStore

    return JobStore(redis_falso, "test")
