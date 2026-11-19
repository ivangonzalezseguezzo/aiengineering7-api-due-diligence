"""Agente CRÍTICO (abogado del diablo): audita a la startup y al propio equipo.

Herramientas:
- `contrastar_metricas`: regla determinística que compara lo que la startup DECLARA (margen bruto,
  runway) con lo que CALCULÓ el analista. No depende del LLM: sus discrepancias siempre llegan al
  informe como red flags de origen "contraste" (determinístico antes que semántico).
- `buscar_evidencia`: BM25 sobre el data room (letra chica) o sobre mercado y tesis del fondo.
- `registrar_observaciones`: dos tipos de salida bien separados:
    · red flags = riesgos de la STARTUP (van al informe), cada uno con cita literal anclada;
    · correcciones = errores del EQUIPO (un dato mal registrado o un cálculo con un insumo
      equivocado), dirigidas al agente dueño de ese dato. Vuelven a ese agente por el supervisor.

Es el "agente árbitro" de la clase: resuelve los conflictos entre fuentes con evidencia (no por
votación) y no puede tocar las parcelas de los demás. No recalcula ni decide la inversión.
"""

from dataclasses import dataclass, field
from typing import Any, Literal

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field

from due_diligence.agents.comun import a_json, error, formatear_hallazgos, formatear_metricas, fragmentos_a_json
from due_diligence.agents.react import crear_agente_react, ejecutar_especialista, texto_final
from due_diligence.config import DDSettings
from due_diligence.corpus import AMBITO_FONDO, AMBITO_MERCADO, Corpus, limpiar_cita, mismo_numero, normalizar_cita, numeros_en
from due_diligence.formato import num, pct
from due_diligence.state import METRICAS, Analisis, Aporte, Correccion, Critica, Discrepancia, EntradaCritico, Investigacion, RedFlag

SEVERIDADES = ["alta", "media", "baja"]

PROMPT = """\
Sos el CRÍTICO (abogado del diablo) de un equipo de due diligence de startups de IA para un fondo.
Tu trabajo es auditar: encontrar lo que la startup no muestra y los errores del propio equipo.
No investigás de cero, no recalculás y no decidís la inversión.

Cómo trabajás:
1. Llamá a contrastar_metricas: compara lo que la startup declara con lo que calculó el analista.
2. Buscá letra chica con buscar_evidencia, con varias búsquedas en paralelo. Revisá como mínimo:
   ingresos no recurrentes dentro del MRR (pilotos, cobros únicos), costos excluidos del margen
   (que cada costo registrado sea el TOTAL que pide su definición y no un subtotal: buscá "costos
   directos" y compará las cifras del documento con las registradas),
   financiamiento no firmado dentro del runway, concentración y vencimiento de contratos de clientes,
   dependencia de un proveedor de LLM y cláusulas de precio, créditos promocionales y dedicación del
   equipo técnico. La tesis del fondo (ambito "mercado") tiene los umbrales de referencia.
3. Registrá todo en UNA llamada a registrar_observaciones (una segunda solo para corregir rechazos):
   - red_flags: riesgos de la STARTUP, cada uno con la cita LITERAL que lo prueba y su fuente.
     Severidad: alta = puede cambiar la decisión; media = hay que negociarlo o seguirlo; baja = menor.
     Un riesgo de la startup (pilotos dentro del MRR, runway que cuenta un préstamo sin firmar,
     concentración de clientes, CTO part-time) es SIEMPRE una red flag, nunca una corrección.
   - correcciones: errores del EQUIPO que hay que rehacer.
     · Al "investigador", solo si una métrica REGISTRADA tiene un valor que no cumple su definición o
       si falta registrar un dato que está en el data room: indicá metrica, valor_correcto y la cita
       literal que lo contiene. Compará con la lista de métricas registradas: si ya tiene ese valor, no
       hay nada que corregir.
     · Al "analista", solo si un cálculo usó un supuesto equivocado (por ejemplo, no modeló una
       cláusula de suba de precio que figura en el data room).
4. No repitas red flags ni correcciones que ya figuran. Si algo está bien, no inventes problemas.
5. Terminá con un resumen de 3 a 5 líneas: los riesgos principales y si el análisis necesita correcciones.
"""


# --- Contraste determinístico -----------------------------------------------------------------


def contrastar(investigacion: Investigacion | None, analisis: Analisis | None) -> list[Discrepancia]:
    """Declarado por la startup vs. calculado por el analista (margen bruto y runway)."""
    if investigacion is None or analisis is None:
        return []
    ue = analisis.resultado("calcular_unit_economics")
    discrepancias = []

    declarado, calculado = investigacion.valor("margen_bruto_declarado_pct"), ue.get("margen_bruto_pct")
    if declarado is not None and calculado is not None and abs(declarado - calculado) > 5:
        diferencia = declarado - calculado
        discrepancias.append(
            Discrepancia(
                metrica_declarada="margen_bruto_declarado_pct",
                declarado=declarado,
                calculado=calculado,
                severidad="alta" if abs(diferencia) > 15 else "media",
                descripcion=f"Margen bruto declarado {pct(declarado)} vs. calculado con todos los costos directos "
                f"{pct(calculado)} ({num(diferencia, 1)} puntos de diferencia).",
            )
        )

    declarado, calculado = investigacion.valor("runway_declarado_meses"), ue.get("runway_meses")
    if declarado and calculado is not None and calculado < 0.9 * declarado:
        discrepancias.append(
            Discrepancia(
                metrica_declarada="runway_declarado_meses",
                declarado=declarado,
                calculado=calculado,
                severidad="alta" if calculado < 0.7 * declarado else "media",
                descripcion=f"Runway declarado {num(declarado)} meses vs. {num(calculado, 1)} meses con la caja "
                "y el burn actuales.",
            )
        )
    return discrepancias


def red_flags_de_contraste(discrepancias: list[Discrepancia], investigacion: Investigacion | None) -> list[RedFlag]:
    flags = []
    for d in discrepancias:
        respaldo = investigacion.metricas.get(d.metrica_declarada) if investigacion else None
        titulo = "Margen bruto declarado inflado" if d.metrica_declarada.startswith("margen") else "Runway declarado mayor al real"
        flags.append(
            RedFlag(
                id=f"K-{d.metrica_declarada}",
                titulo=titulo,
                severidad=d.severidad,
                explicacion=d.descripcion,
                fuente=respaldo.fuente if respaldo else None,
                cita=respaldo.cita if respaldo else None,
                origen="contraste",
            )
        )
    return flags


# --- Esquemas de las herramientas -------------------------------------------------------------


class BuscarEvidenciaInput(BaseModel):
    consulta: str = Field(description="Qué evidencia buscás, por ejemplo 'pilotos única vez' o 'préstamo puente'.")
    ambito: Literal["data_room", "mercado"] = Field(default="data_room", description="data_room de la startup, o mercado + tesis del fondo.")


class RedFlagEntrada(BaseModel):
    titulo: str = Field(description="El riesgo en pocas palabras.")
    severidad: str = Field(json_schema_extra={"enum": SEVERIDADES})
    explicacion: str = Field(description="Por qué importa para la inversión.")
    fuente: str = Field(description="id del fragmento que lo prueba.")
    cita: str = Field(description="Frase LITERAL del fragmento que lo prueba.")


class CorreccionEntrada(BaseModel):
    agente_afectado: str = Field(json_schema_extra={"enum": ["investigador", "analista"]})
    severidad: str = Field(json_schema_extra={"enum": SEVERIDADES})
    descripcion: str = Field(description="Qué está mal y qué hay que hacer.")
    metrica: str | None = Field(
        default=None, description="Obligatoria para el investigador: métrica del vocabulario a corregir o agregar.", json_schema_extra={"enum": list(METRICAS)}
    )
    valor_correcto: float | None = Field(default=None, description="Obligatorio para el investigador: el valor que debería tener la métrica.")
    fuente: str | None = Field(default=None, description="id del fragmento que lo prueba (obligatorio para el investigador).")
    cita: str | None = Field(default=None, description="Frase LITERAL del fragmento que contiene el valor correcto.")


class RegistrarObservacionesInput(BaseModel):
    red_flags: list[RedFlagEntrada] = Field(default_factory=list, max_length=12)
    correcciones: list[CorreccionEntrada] = Field(default_factory=list, max_length=8)


MAX_REGISTROS = 2  # llamadas a registrar_observaciones por revisión: la segunda es para corregir rechazos


@dataclass
class LibretaCritica:
    red_flags: list[RedFlagEntrada] = field(default_factory=list)
    correcciones: list[CorreccionEntrada] = field(default_factory=list)
    rechazos: int = 0
    registros: int = 0


def _clave_correccion(agente: str, metrica: str | None, descripcion: str) -> str:
    """Dos correcciones son la misma si piden cambiar la misma métrica (o dicen lo mismo, para el analista)."""
    return f"{agente}:{metrica}" if metrica else f"{agente}:{normalizar_cita(descripcion)[:60]}"


def crear_herramientas_critico(
    corpus: Corpus, startup: str, investigacion: Investigacion | None, analisis: Analisis | None, previa: Critica | None, libreta: LibretaCritica
) -> list[BaseTool]:
    ambitos = (startup, AMBITO_MERCADO, AMBITO_FONDO)
    titulos_previos = {normalizar_cita(r.titulo) for r in (previa.red_flags if previa else [])}
    correcciones_previas = {_clave_correccion(c.agente_afectado, c.metrica, c.descripcion) for c in (previa.correcciones if previa else [])}

    def validar_correccion(c: CorreccionEntrada) -> tuple[str, str] | None:
        """(error, sugerencia) si la corrección no es válida; None si se acepta."""
        if c.agente_afectado not in ("investigador", "analista") or c.severidad not in SEVERIDADES:
            return "agente_afectado o severidad inválidos", "agente: investigador o analista; severidad: alta, media o baja"
        if len(c.descripcion.strip()) < 15:
            return "descripción demasiado vaga", "decí qué está mal y qué hay que hacer"
        anclado = corpus.anclar(c.cita, c.fuente or "", ambitos) if c.cita else None
        if c.cita and anclado is None:
            return "la cita no aparece literalmente en las fuentes", "copiá la frase exacta del fragmento"
        if _clave_correccion(c.agente_afectado, c.metrica, c.descripcion) in correcciones_previas:
            return "esa corrección ya fue emitida", "no la repitas"
        if c.agente_afectado == "investigador":
            if c.metrica not in METRICAS or c.valor_correcto is None or anclado is None:
                return (
                    "una corrección al investigador necesita metrica, valor_correcto y la cita literal que lo contiene",
                    "si en realidad es un riesgo de la startup, registralo como red flag",
                )
            if not any(mismo_numero(c.valor_correcto, n) for n in numeros_en(c.cita)):
                return f"el valor_correcto {c.valor_correcto:g} no aparece en la cita", "citá la línea que contiene ese valor"
            registrada = investigacion.valor(c.metrica) if investigacion else None
            if registrada is not None and mismo_numero(registrada, c.valor_correcto):
                return f"{c.metrica} ya está registrada con {registrada:g}", "no hay nada que corregir"
        return None

    @tool("contrastar_metricas")
    async def contrastar_metricas() -> str:
        """Compara lo que declara la startup (margen bruto, runway) con lo que calculó el analista y devuelve las discrepancias."""
        discrepancias = contrastar(investigacion, analisis)
        return a_json({"discrepancias": [d.model_dump() for d in discrepancias], "total": len(discrepancias)})

    @tool("buscar_evidencia", args_schema=BuscarEvidenciaInput)
    async def buscar_evidencia(consulta: str, ambito: str = "data_room") -> str:
        """Busca evidencia (letra chica, notas al pie, cláusulas) en el data room o en mercado y tesis del fondo."""
        fragmentos = corpus.buscar(consulta, (startup,) if ambito == "data_room" else (AMBITO_MERCADO, AMBITO_FONDO))
        if not fragmentos:
            return error("Sin resultados.", "Probá con otras palabras clave.")
        return fragmentos_a_json(fragmentos)

    @tool("registrar_observaciones", args_schema=RegistrarObservacionesInput)
    async def registrar_observaciones(red_flags: list[RedFlagEntrada] | None = None, correcciones: list[CorreccionEntrada] | None = None) -> str:
        """Registra red flags (riesgos de la startup, con cita) y correcciones (errores del equipo, dirigidas a un agente)."""
        libreta.registros += 1
        if libreta.registros > MAX_REGISTROS:
            return error(f"Ya registraste observaciones {MAX_REGISTROS} veces en esta revisión.", "No llames más herramientas: terminá con tu resumen.")
        aceptadas, rechazadas = [], []
        for r in red_flags or []:
            anclado = corpus.anclar(r.cita, r.fuente, ambitos)
            if r.severidad not in SEVERIDADES:
                rechazadas.append({"titulo": r.titulo, "error": "severidad inválida", "sugerencia": f"usá {SEVERIDADES}"})
            elif anclado is None:
                rechazadas.append({"titulo": r.titulo, "error": "la cita no aparece literalmente en las fuentes", "sugerencia": "copiá la frase exacta del fragmento"})
            elif normalizar_cita(r.titulo) in titulos_previos:
                rechazadas.append({"titulo": r.titulo, "error": "ya estaba registrada", "sugerencia": "no la repitas"})
            else:
                libreta.red_flags.append(r.model_copy(update={"fuente": anclado.id, "cita": limpiar_cita(r.cita)}))
                titulos_previos.add(normalizar_cita(r.titulo))
                aceptadas.append({"red_flag": r.titulo, "fuente": anclado.id})
        for c in correcciones or []:
            if problema := validar_correccion(c):
                rechazadas.append({"correccion": c.descripcion, "error": problema[0], "sugerencia": problema[1]})
                continue
            anclado = corpus.anclar(c.cita, c.fuente or "", ambitos) if c.cita else None
            libreta.correcciones.append(c.model_copy(update={"fuente": anclado.id if anclado else None, "cita": limpiar_cita(c.cita) if c.cita else None}))
            correcciones_previas.add(_clave_correccion(c.agente_afectado, c.metrica, c.descripcion))
            aceptadas.append({"correccion": c.descripcion[:80], "para": c.agente_afectado})
        libreta.rechazos += len(rechazadas)
        siguiente = "Corregí los rechazos en UNA llamada más o terminá con tu resumen." if rechazadas else "Listo: terminá con tu resumen."
        return a_json({"aceptadas": aceptadas, "rechazadas": rechazadas, "siguiente_paso": siguiente})

    return [contrastar_metricas, buscar_evidencia, registrar_observaciones]


def _analisis_para_critico(analisis: Analisis | None) -> str:
    if analisis is None or not analisis.calculos:
        return "(sin cálculos)"
    lineas = [f"- {nombre}: {a_json(calculo.resultado)}" for nombre, calculo in analisis.calculos.items()]
    lineas.append(f"Interpretación del analista: {analisis.interpretacion}")
    return "\n".join(lineas)


def mensaje_para_critico(nombre: str, startup: str, instruccion: str, investigacion: Investigacion | None, analisis: Analisis | None, previa: Critica | None) -> str:
    flags = "\n".join(f"- {r.id} ({r.severidad}) {r.titulo}" for r in previa.red_flags) if previa and previa.red_flags else "(ninguna)"
    correcciones = "\n".join(f"- {c.id} → {c.agente_afectado}: {c.descripcion}" for c in previa.correcciones) if previa and previa.correcciones else "(ninguna)"
    version = analisis.version if analisis else "-"
    return f"""\
Startup: {nombre} (id: {startup}).

Instrucción del supervisor: {instruccion or "Auditá la investigación y el análisis."}

Métricas registradas por el investigador (con fuente):
{formatear_metricas(investigacion)}

Hallazgos del investigador:
{formatear_hallazgos(investigacion)}

Análisis v{version} (resultados de las herramientas del analista):
{_analisis_para_critico(analisis)}

Red flags ya registradas (no las repitas):
{flags}

Correcciones ya emitidas:
{correcciones}"""


def _siguiente_id(prefijo: str, existentes: list[Any]) -> str:
    return f"{prefijo}{len(existentes) + 1}"


def crear_nodo_critico(model: BaseChatModel, corpus: Corpus, settings: DDSettings, *, reintentos_llm: int = 0):
    async def critico(state: EntradaCritico) -> dict[str, Any]:
        startup = state["startup"]
        investigacion, analisis, previa = state.get("investigacion"), state.get("analisis"), state.get("critica")
        libreta = LibretaCritica()
        agente = crear_agente_react(
            model,
            crear_herramientas_critico(corpus, startup, investigacion, analisis, previa, libreta),
            PROMPT,
            nombre="critico",
            max_iteraciones=settings.max_iteraciones_especialista,
            reintentos_llm=reintentos_llm,
        )
        mensaje = mensaje_para_critico(corpus.startups[startup], startup, state.get("instruccion", ""), investigacion, analisis, previa)
        mensajes = await ejecutar_especialista(agente, mensaje, nombre="critico", max_iteraciones=settings.max_iteraciones_especialista)
        resumen = texto_final(mensajes) or "(el crítico no dejó resumen)"

        # Las red flags del LLM se acumulan entre revisiones; las de contraste se recalculan con el análisis vigente.
        red_flags = [r for r in (previa.red_flags if previa else []) if r.origen == "critico"]
        for r in libreta.red_flags:
            red_flags.append(RedFlag(id=_siguiente_id("R", red_flags), **r.model_dump()))
        discrepancias = contrastar(investigacion, analisis)
        red_flags += red_flags_de_contraste(discrepancias, investigacion)
        correcciones = list(previa.correcciones) if previa else []
        nuevas = []
        for c in libreta.correcciones:
            correccion = Correccion(id=_siguiente_id("C", correcciones), **c.model_dump())
            correcciones.append(correccion)
            nuevas.append(correccion)

        critica = Critica(
            version=(previa.version + 1) if previa else 1,
            version_analisis=analisis.version if analisis else 1,
            red_flags=red_flags,
            correcciones=correcciones,
            discrepancias=discrepancias,
            resumen=resumen,
        )
        altas = sum(r.severidad == "alta" for r in red_flags)
        detalle = (
            f"v{critica.version} (auditó el análisis v{critica.version_analisis}): {len(red_flags)} red flags ({altas} altas), "
            f"{len(discrepancias)} discrepancias declarado/calculado, {len(nuevas)} correcciones nuevas"
            + (": " + ", ".join(f"{c.id}→{c.agente_afectado}" for c in nuevas) if nuevas else "")
        )
        return {
            "critica": critica,
            "aportes": [Aporte(paso=state["pasos"], agente="critico", accion="auditoría", detalle=detalle)],
            "messages": [AIMessage(content=f"{detalle}\n{resumen}", name="critico")],
        }

    return critico
