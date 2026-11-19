"""Estado compartido del orquestador: el contrato de datos entre el supervisor y los especialistas.

`EstadoDD` hereda de `MessagesState` (historial legible, con el reducer `add_messages`) y suma:

- Campos de control, que escribe SOLO el supervisor: `siguiente` (el "next_agent"), `instruccion`,
  `pasos` (contador contra el supervisor infinito), `intentos` por especialista y `motivo_cierre`.
- Una parcela por agente (aislamiento de escritura): `investigacion` (investigador), `analisis`
  (analista), `critica` (crítico), `validacion` (validador) e `informe` + `recomendacion` +
  `tarea_completa` (nodo de informe). `graph.py` verifica en cada paso que ningún nodo escriba
  fuera de su parcela (`PARCELAS`).
- `aportes`: registro append-only (reducer `operator.add`) de qué agente aportó qué en cada paso.
- PE7: `criticidad` (la escribe el nodo `triage`: si el caso necesita al comité y por qué), la
  parcela `revision` (decisiones del comité en la pausa human-in-the-loop, la escribe el nodo
  `aprobacion`) y `decision_final` + `informe_final` (los escribe el nodo `cierre`).

Cada parcela es un modelo Pydantic: los datos se validan ANTES de entrar al estado compartido.
Para leer, cada especialista declara un `input_schema` con solo las claves que necesita
(`EntradaInvestigador`, `EntradaAnalista`, `EntradaCritico`): no ve el historial completo.
"""

import operator
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import MessagesState
from pydantic import BaseModel, Field

Agente = Literal["investigador", "analista", "critico"]
Destino = Literal["investigador", "analista", "critico", "finalizar"]
Severidad = Literal["alta", "media", "baja"]
TemaHallazgo = Literal["equipo", "producto", "clientes", "mercado", "competencia", "tecnologia", "riesgo"]
Recomendacion = Literal["AVANZAR A COMITÉ", "PROFUNDIZAR", "NO AVANZAR", "SIN RECOMENDACIÓN"]

AGENTES: tuple[Agente, ...] = ("investigador", "analista", "critico")

# --- Vocabulario de métricas --------------------------------------------------------------
# Es el contrato entre el investigador (que las registra con su fuente) y el analista (que solo
# puede calcular con ellas). Los porcentajes van de 0 a 100.
METRICAS: dict[str, str] = {
    "mrr_usd": "Ingreso recurrente mensual en USD, SIN pilotos ni cobros por única vez",
    "clientes_activos": "Clientes pagos con contrato recurrente (sin pilotos)",
    "crecimiento_mrr_mensual_pct": "Crecimiento mensual promedio del MRR recurrente, en %",
    "churn_mensual_pct": "Churn mensual de clientes, en %",
    "concentracion_top_cliente_pct": "Porcentaje del MRR recurrente que representa el cliente más grande",
    "gasto_comercial_mensual_usd": "Gasto mensual en ventas y marketing, en USD",
    "clientes_nuevos_mes": "Clientes recurrentes nuevos por mes (promedio)",
    "costo_infra_mensual_usd": "Infraestructura cloud mensual sin el LLM, en USD",
    "otros_costos_directos_mensual_usd": (
        "TOTAL mensual de los costos directos que no son LLM ni infraestructura (soporte, voz, telefonía, "
        "onboarding), en USD. Si el documento da los componentes y una línea de total, va el total"
    ),
    "margen_bruto_declarado_pct": "Margen bruto que declara la startup, en %",
    "burn_neto_mensual_usd": "Quema neta de caja por mes, en USD",
    "caja_usd": "Caja disponible, en USD",
    "runway_declarado_meses": "Runway que declara la startup, en meses",
    "llamadas_llm_mes": "Llamadas al LLM por mes",
    "tokens_entrada_por_llamada": "Tokens de entrada promedio por llamada al LLM",
    "tokens_salida_por_llamada": "Tokens de salida promedio por llamada al LLM",
    "precio_entrada_usd_millon": "Precio vigente por millón de tokens de entrada, en USD",
    "precio_salida_usd_millon": "Precio vigente por millón de tokens de salida, en USD",
    "clientes_potenciales_tam": "Cantidad de clientes potenciales en el mercado total (fuente de mercado)",
    "ticket_anual_usd": "Ticket anual por cliente, en USD",
    "porcentaje_sam_pct": "Porcentaje del mercado total que la startup puede atender hoy (SAM), en %",
}
NombreMetrica = Literal[tuple(METRICAS)]  # type: ignore[valid-type]

METRICAS_LLM = (
    "llamadas_llm_mes",
    "tokens_entrada_por_llamada",
    "tokens_salida_por_llamada",
    "precio_entrada_usd_millon",
    "precio_salida_usd_millon",
)
# Sin estas el análisis no es concluyente: si faltan 3 o más, el informe se abstiene.
# Los costos directos son clave: sin ellos el margen sale inflado (lo mostró la primera corrida real).
METRICAS_CLAVE = (
    "mrr_usd",
    "clientes_activos",
    "crecimiento_mrr_mensual_pct",
    "churn_mensual_pct",
    "concentracion_top_cliente_pct",
    "gasto_comercial_mensual_usd",
    "clientes_nuevos_mes",
    "costo_infra_mensual_usd",
    "otros_costos_directos_mensual_usd",
    "burn_neto_mensual_usd",
    "caja_usd",
    *METRICAS_LLM,
)
# Lo que la startup DICE de sí misma: el crítico lo contrasta con lo calculado.
METRICAS_DECLARADAS = ("margen_bruto_declarado_pct", "runway_declarado_meses")
METRICAS_MERCADO = ("clientes_potenciales_tam", "ticket_anual_usd", "porcentaje_sam_pct")


# --- Parcela del investigador ---------------------------------------------------------------


class MetricaRegistrada(BaseModel):
    metrica: NombreMetrica
    valor: float
    fuente: str = Field(description="id del fragmento, p. ej. 'vozactiva/finanzas#3'")
    cita: str = Field(description="Texto literal del fragmento que contiene el valor")
    ronda: int = Field(ge=1, description="Versión de la investigación en que se registró")


class Hallazgo(BaseModel):
    tema: TemaHallazgo
    afirmacion: str
    fuente: str
    cita: str


class Investigacion(BaseModel):
    version: int = Field(ge=1)
    metricas: dict[str, MetricaRegistrada] = Field(default_factory=dict)
    hallazgos: list[Hallazgo] = Field(default_factory=list)
    fuentes_consultadas: list[str] = Field(default_factory=list)
    reemplazos: list[str] = Field(default_factory=list, description="Métricas corregidas: valor anterior -> nuevo")
    rechazos: int = Field(default=0, description="Registros que no pasaron el anclaje a la fuente")
    correcciones_atendidas: list[str] = Field(default_factory=list)
    resumen: str = ""

    def faltantes(self, metricas: tuple[str, ...] = METRICAS_CLAVE) -> list[str]:
        return [m for m in metricas if m not in self.metricas]

    def valor(self, metrica: str) -> float | None:
        registrada = self.metricas.get(metrica)
        return registrada.valor if registrada else None


# --- Parcela del analista -------------------------------------------------------------------


class Calculo(BaseModel):
    herramienta: str
    entradas: dict[str, Any]
    resultado: dict[str, Any]


class Analisis(BaseModel):
    version: int = Field(ge=1)
    version_investigacion: int = Field(ge=1, description="Versión de la investigación con la que se calculó")
    calculos: dict[str, Calculo] = Field(default_factory=dict, description="Último cálculo aceptado por herramienta")
    rechazos: int = Field(default=0, description="Llamadas rechazadas por entradas sin respaldo")
    correcciones_atendidas: list[str] = Field(default_factory=list)
    interpretacion: str = ""

    def resultado(self, herramienta: str) -> dict[str, Any]:
        calculo = self.calculos.get(herramienta)
        return calculo.resultado if calculo else {}


# --- Parcela del crítico --------------------------------------------------------------------


class RedFlag(BaseModel):
    """Riesgo de la STARTUP: es un hallazgo de la due diligence y va al informe."""

    id: str
    titulo: str
    severidad: Severidad
    explicacion: str
    fuente: str | None = None
    cita: str | None = None
    origen: Literal["critico", "contraste"] = Field(
        default="critico", description="critico = lo registró el LLM con evidencia; contraste = regla determinística"
    )


class Correccion(BaseModel):
    """Error del EQUIPO (un dato mal registrado o un cálculo mal hecho): vuelve al agente afectado.

    Las dirigidas al investigador son verificables: nombran la métrica, el valor correcto y la cita
    que lo contiene (si ese valor ya está registrado, la corrección se rechaza por innecesaria).
    """

    id: str
    agente_afectado: Literal["investigador", "analista"]
    severidad: Severidad
    descripcion: str
    metrica: str | None = None
    valor_correcto: float | None = None
    fuente: str | None = None
    cita: str | None = None


class Discrepancia(BaseModel):
    metrica_declarada: str
    declarado: float
    calculado: float
    severidad: Severidad
    descripcion: str


class Critica(BaseModel):
    version: int = Field(ge=1)
    version_analisis: int = Field(ge=1, description="Versión del análisis que auditó")
    red_flags: list[RedFlag] = Field(default_factory=list)
    correcciones: list[Correccion] = Field(default_factory=list)
    discrepancias: list[Discrepancia] = Field(default_factory=list)
    resumen: str = ""


# --- Parcela del validador ------------------------------------------------------------------


class Check(BaseModel):
    nombre: str
    ok: bool
    detalle: str
    responsable: Agente | None = Field(default=None, description="Quién puede resolverlo si falla")
    advertencia: bool = Field(default=False, description="Pasa, pero con una salvedad que va al informe")


class Validacion(BaseModel):
    aprobado: bool
    intento: int = Field(ge=1)
    checks: list[Check]

    @property
    def errores(self) -> list[Check]:
        return [c for c in self.checks if not c.ok]

    @property
    def advertencias(self) -> list[Check]:
        return [c for c in self.checks if c.ok and c.advertencia]


# --- Registro de aportes --------------------------------------------------------------------


class Aporte(BaseModel):
    paso: int
    agente: str
    accion: str
    detalle: str


# --- Parcela de la revisión humana (PE7) ----------------------------------------------------

AccionHumana = Literal["aprobar", "rechazar", "otra_vuelta"]


class DecisionHumana(BaseModel):
    """Lo que decide el comité en la pausa human-in-the-loop (llega por POST /tasks/{id}/approve)."""

    accion: AccionHumana
    comentario: str = ""
    aprobador: str = "comité"
    ronda: int = Field(ge=1, description="Número de pausa en la que se tomó la decisión")
    fecha: str = ""


class Criticidad(BaseModel):
    """Resultado del triage: si la recomendación necesita la aprobación del comité y por qué."""

    critica: bool
    nivel_riesgo: Literal["alto", "medio", "bajo"]
    motivos: list[str]


class RevisionHumana(BaseModel):
    decisiones: list[DecisionHumana] = Field(default_factory=list)

    @property
    def rondas_pedidas(self) -> int:
        return sum(d.accion == "otra_vuelta" for d in self.decisiones)

    @property
    def ultima(self) -> DecisionHumana | None:
        return self.decisiones[-1] if self.decisiones else None


# --- Estado del grafo -----------------------------------------------------------------------


class EstadoDD(MessagesState):
    startup: str
    # Control: solo el supervisor.
    siguiente: Destino
    instruccion: str
    pasos: int
    intentos: dict[str, int]
    motivo_cierre: str
    ronda_humana_atendida: int
    # Parcelas: cada una tiene un único dueño.
    investigacion: Investigacion | None
    analisis: Analisis | None
    critica: Critica | None
    validacion: Validacion | None
    informe: str | None
    recomendacion: Recomendacion | None
    tarea_completa: bool
    criticidad: Criticidad | None
    revision: RevisionHumana | None
    decision_final: Literal["APROBADA", "RECHAZADA", "EMITIDA SIN PAUSA"] | None
    informe_final: str | None
    # Proveniencia: append-only.
    aportes: Annotated[list[Aporte], operator.add]


# Lo que puede ESCRIBIR cada nodo (graph.py lo hace cumplir).
PARCELAS: dict[str, frozenset[str]] = {
    "supervisor": frozenset(
        {"siguiente", "instruccion", "pasos", "intentos", "motivo_cierre", "ronda_humana_atendida", "messages", "aportes"}
    ),
    "investigador": frozenset({"investigacion", "messages", "aportes"}),
    "analista": frozenset({"analisis", "messages", "aportes"}),
    "critico": frozenset({"critica", "messages", "aportes"}),
    "validador": frozenset({"validacion", "messages", "aportes"}),
    "informe": frozenset({"informe", "recomendacion", "tarea_completa", "messages", "aportes"}),
    "triage": frozenset({"criticidad", "messages", "aportes"}),
    "aprobacion": frozenset({"revision", "messages", "aportes"}),
    "cierre": frozenset({"decision_final", "informe_final", "messages", "aportes"}),
}


# Lo que puede LEER cada especialista (input_schema del nodo). La crítica la leen todos para
# tomar las correcciones dirigidas a ellos; el historial de mensajes no lo lee ninguno.
class EntradaInvestigador(TypedDict):
    startup: str
    instruccion: str
    pasos: int
    investigacion: Investigacion | None
    critica: Critica | None


class EntradaAnalista(TypedDict):
    startup: str
    instruccion: str
    pasos: int
    investigacion: Investigacion | None
    analisis: Analisis | None
    critica: Critica | None


class EntradaCritico(TypedDict):
    startup: str
    instruccion: str
    pasos: int
    investigacion: Investigacion | None
    analisis: Analisis | None
    critica: Critica | None


def estado_inicial(startup: str, pedido: str) -> dict[str, Any]:
    """Entrada del grafo con todas las claves inicializadas (ningún nodo lee una clave ausente)."""
    return {
        "messages": [HumanMessage(pedido)],
        "startup": startup,
        "siguiente": "investigador",
        "instruccion": "",
        "pasos": 0,
        "intentos": {agente: 0 for agente in AGENTES},
        "motivo_cierre": "",
        "ronda_humana_atendida": 0,
        "investigacion": None,
        "analisis": None,
        "critica": None,
        "validacion": None,
        "informe": None,
        "recomendacion": None,
        "tarea_completa": False,
        "criticidad": None,
        "revision": None,
        "decision_final": None,
        "informe_final": None,
        "aportes": [],
    }


_MODELOS_DEL_ESTADO = (
    MetricaRegistrada, Hallazgo, Investigacion, Calculo, Analisis, RedFlag, Correccion,
    Discrepancia, Critica, Check, Validacion, Aporte, DecisionHumana, RevisionHumana, Criticidad,
)


def serializador() -> JsonPlusSerializer:
    """Serializador de checkpoints que permite explícitamente los contratos de este módulo.

    Desde langgraph-checkpoint 4, deserializar un tipo propio sin registrarlo emite un aviso y
    "se va a bloquear en una versión futura". Registrarlos evita el aviso y deja el estado listo
    para el checkpointer de Redis de la PE7.
    """
    return JsonPlusSerializer(allowed_msgpack_modules=[(m.__module__, m.__name__) for m in _MODELOS_DEL_ESTADO])


def serializador_redis():
    """Serializador para `AsyncRedisSaver` (PE7) con los contratos de este módulo en la lista permitida.

    El checkpointer de Redis guarda el estado como JSON y reconstruye los objetos desde un "sobre"
    con el módulo y la clase. Sin la lista permitida, los modelos Pydantic vuelven como `dict` y el
    grafo se rompe al retomar un hilo (por ejemplo, después de la pausa de aprobación humana).
    """
    from langgraph.checkpoint.redis.jsonplus_redis import JsonPlusRedisSerializer

    return JsonPlusRedisSerializer(allowed_json_modules=[(*m.__module__.split("."), m.__name__) for m in _MODELOS_DEL_ESTADO])
