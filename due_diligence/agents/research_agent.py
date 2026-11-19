"""Agente INVESTIGADOR: busca en el data room y en fuentes de mercado y registra datos con su fuente.

Herramientas:
- `leer_documento`: un documento completo del data room (es chico: leerlo entero no pierde datos).
- `buscar_en_data_room`: BM25 sobre los documentos de ESA startup (no ve otros data rooms).
- `buscar_mercado`: web con Tavily si hay clave; si no, informes de mercado y tesis del fondo.
- `registrar_metricas`: guarda métricas del vocabulario con fuente y cita literal. Cada una pasa
  por el anclaje (la cita tiene que estar en un fragmento real y el valor dentro de la cita). Lo
  que no ancla se rechaza con el motivo y el agente lo corrige en la ronda siguiente.
- `registrar_hallazgos`: datos cualitativos (equipo, clientes, competencia, riesgos) con cita.

Es el único dueño de la parcela `investigacion`: si el crítico detecta un dato mal registrado,
la corrección vuelve acá. No calcula ni opina sobre la inversión.

PE7, PRECARGA (`DDSettings.precargar_data_room`): en la primera invocación el mensaje ya trae el
data room completo y el informe de mercado del sector (`precarga_inicial`). En la PE6, la primera
ronda del agente era siempre "leé todos los documentos": esa ronda (y la de buscar el mercado) se
ahorra, y las citas siguen ancladas a los mismos fragmentos. Las herramientas de lectura quedan
disponibles para lo que falte.
"""

import logging
from dataclasses import dataclass, field
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field

from due_diligence.agents.comun import (
    a_json,
    correcciones_pendientes,
    error,
    formatear_correcciones,
    formatear_metricas,
    fragmentos_a_json,
    vocabulario,
)
from due_diligence.agents.react import crear_agente_react, ejecutar_especialista, texto_final
from due_diligence.busqueda_web import BuscadorWeb
from due_diligence.config import DDSettings
from due_diligence.corpus import AMBITO_FONDO, AMBITO_MERCADO, Corpus, Fragmento, cita_en_texto, limpiar_cita, mismo_numero, numeros_en
from due_diligence.state import (
    METRICAS,
    METRICAS_CLAVE,
    METRICAS_DECLARADAS,
    METRICAS_MERCADO,
    Aporte,
    Correccion,
    EntradaInvestigador,
    Hallazgo,
    Investigacion,
    MetricaRegistrada,
)

logger = logging.getLogger(__name__)

TEMAS = ["equipo", "producto", "clientes", "mercado", "competencia", "tecnologia", "riesgo"]

PROMPT = f"""\
Sos el INVESTIGADOR de un equipo de due diligence de startups de IA para un fondo de inversión.
Tu trabajo es relevar datos verificables: buscás en el data room de la startup y en fuentes de
mercado, y registrás cada dato con la fuente y la cita literal que lo respalda.

Cómo trabajás:
1. Si el mensaje ya trae los documentos del data room y el informe de mercado, NO los vuelvas a leer:
   registrá directamente (pasos 2 y 3, en la misma ronda). Si no los trae, en la primera ronda leé TODOS
   los documentos del data room con leer_documento (una llamada por documento, todas en paralelo) y
   buscá el tamaño de mercado con buscar_mercado. Si después te falta algo puntual, usá
   buscar_en_data_room o buscar_mercado.
2. Registrá las métricas del vocabulario con registrar_metricas, todas en una sola llamada:
   - valor: el número tal como figura, sin separadores de miles ni unidades (90000, 6.5, 2.5).
     Los porcentajes van de 0 a 100 (6,5 % -> 6.5).
   - fuente: el id del fragmento (por ejemplo "vozactiva/finanzas#2").
   - cita: la línea LITERAL del fragmento que contiene el número. Copiala, no la resumas.
   - Si las fuentes dan dos cifras para lo mismo (por ejemplo MRR total y MRR recurrente), registrá
     la que cumple la definición del vocabulario y anotá la diferencia como hallazgo de tema "riesgo".
   - Si un documento detalla componentes (soporte, voz, telefonía...) y además da una línea de TOTAL,
     y la definición pide un total, registrá el total: un subtotal deja el margen inflado.
   - Las métricas de la startup solo pueden venir de SU data room. Las de mercado
     (clientes_potenciales_tam, ticket_anual_usd, porcentaje_sam_pct) también de fuentes de mercado.
   - Registrá también lo que la startup DECLARA de sí misma (margen bruto y runway declarados): el
     crítico lo contrasta con lo que calcula el analista.
3. En la misma ronda, registrá con registrar_hallazgos al menos 3 hallazgos cualitativos que importan
   para invertir: equipo (dedicación y experiencia), clientes (concentración, contratos), competencia,
   tecnología (dependencia de proveedores) y riesgos (notas al pie, financiamiento no firmado,
   créditos promocionales).
4. Si una herramienta rechaza un registro, leé el motivo y corregilo en la ronda siguiente.
5. Si una métrica no aparece en ninguna fuente después de buscarla, NO la inventes ni la estimes:
   decilo en tu resumen.
6. No calculás nada (eso es del analista) ni opinás sobre la inversión (eso es del crítico y del comité).
7. Terminá con un resumen breve (3 a 5 líneas): qué registraste, qué no encontraste y qué
   contradicciones viste entre documentos.

Vocabulario de métricas (usá exactamente estos nombres):
{vocabulario()}
"""


# --- Esquemas de las herramientas -------------------------------------------------------------


class BuscarInput(BaseModel):
    consulta: str = Field(description="Palabras clave de lo que buscás, por ejemplo 'churn mensual clientes nuevos'.")


class LeerDocumentoInput(BaseModel):
    documento: str = Field(description="Nombre del documento del data room, por ejemplo 'finanzas' o 'metricas'.")


class MetricaEntrada(BaseModel):
    metrica: str = Field(description="Nombre exacto del vocabulario de métricas.", json_schema_extra={"enum": list(METRICAS)})
    valor: float = Field(description="Número tal como figura en la fuente, sin separadores de miles. Porcentajes de 0 a 100.")
    fuente: str = Field(description="id del fragmento que devolvió la búsqueda, por ejemplo 'vozactiva/finanzas#2'.")
    cita: str = Field(description="Línea LITERAL del fragmento que contiene el valor.")


class RegistrarMetricasInput(BaseModel):
    metricas: list[MetricaEntrada] = Field(min_length=1, max_length=30)


class HallazgoEntrada(BaseModel):
    tema: str = Field(description="Tema del hallazgo.", json_schema_extra={"enum": TEMAS})
    afirmacion: str = Field(description="El hallazgo en una oración, con tus palabras.")
    fuente: str = Field(description="id del fragmento que lo respalda.")
    cita: str = Field(description="Frase LITERAL del fragmento que lo respalda.")


class RegistrarHallazgosInput(BaseModel):
    hallazgos: list[HallazgoEntrada] = Field(min_length=1, max_length=15)


# --- Libreta: lo que el investigador acumula durante una invocación --------------------------


@dataclass
class LibretaInvestigacion:
    version: int
    metricas: dict[str, MetricaRegistrada] = field(default_factory=dict)
    hallazgos: list[Hallazgo] = field(default_factory=list)
    fuentes: list[str] = field(default_factory=list)
    reemplazos: list[str] = field(default_factory=list)
    rechazos: int = 0
    web: dict[str, Fragmento] = field(default_factory=dict)

    @classmethod
    def desde(cls, previa: Investigacion | None) -> "LibretaInvestigacion":
        if previa is None:
            return cls(version=1)
        return cls(
            version=previa.version + 1,
            metricas=dict(previa.metricas),
            hallazgos=list(previa.hallazgos),
            fuentes=list(previa.fuentes_consultadas),
            reemplazos=list(previa.reemplazos),
            rechazos=previa.rechazos,
        )

    def consultadas(self, fragmentos: list[Fragmento]) -> None:
        for f in fragmentos:
            if f.id not in self.fuentes:
                self.fuentes.append(f.id)

    def a_parcela(self, resumen: str, correcciones_atendidas: list[str]) -> Investigacion:
        return Investigacion(
            version=self.version,
            metricas=self.metricas,
            hallazgos=self.hallazgos,
            fuentes_consultadas=self.fuentes,
            reemplazos=self.reemplazos,
            rechazos=self.rechazos,
            correcciones_atendidas=correcciones_atendidas,
            resumen=resumen,
        )


def _anclar(corpus: Corpus, libreta: LibretaInvestigacion, cita: str, fuente: str, ambitos: tuple[str, ...]) -> Fragmento | None:
    """Fragmento del corpus o de los resultados web de esta corrida que contiene la cita."""
    if anclado := corpus.anclar(cita, fuente, ambitos):
        return anclado
    if AMBITO_MERCADO in ambitos:
        return next((f for f in libreta.web.values() if cita_en_texto(cita, f.texto)), None)
    return None


def crear_herramientas_investigador(
    corpus: Corpus, startup: str, libreta: LibretaInvestigacion, buscador_web: BuscadorWeb | None = None
) -> list[BaseTool]:
    ambito_startup = (startup,)
    ambito_mercado = (startup, AMBITO_MERCADO, AMBITO_FONDO)

    @tool("leer_documento", args_schema=LeerDocumentoInput)
    async def leer_documento(documento: str) -> str:
        """Devuelve un documento COMPLETO del data room de la startup, sección por sección, con el id de cada fragmento.

        Es la forma más segura de no perderse datos: el data room es chico. Usá el id como 'fuente' al registrar.
        """
        fragmentos = corpus.fragmentos_de(startup, documento)
        if not fragmentos:
            disponibles = ", ".join(corpus.documentos(startup))
            return error(f"No existe el documento '{documento}'.", f"Documentos disponibles: {disponibles}.")
        libreta.consultadas(fragmentos)
        return fragmentos_a_json(fragmentos)

    @tool("buscar_en_data_room", args_schema=BuscarInput)
    async def buscar_en_data_room(consulta: str) -> str:
        """Busca en el data room de la startup analizada (pitch, métricas, finanzas, tecnología, clientes y equipo).

        Devuelve hasta 4 fragmentos con su id (usalo como 'fuente' al registrar), el documento, la sección y el texto.
        """
        fragmentos = corpus.buscar(consulta, ambito_startup)
        libreta.consultadas(fragmentos)
        if not fragmentos:
            return error("Sin resultados.", "Probá con otras palabras clave, por ejemplo 'churn', 'caja' o 'tokens'.")
        return fragmentos_a_json(fragmentos)

    @tool("buscar_mercado", args_schema=BuscarInput)
    async def buscar_mercado(consulta: str) -> str:
        """Busca información de mercado: tamaño (cantidad de clientes potenciales), tickets, competencia y la tesis del fondo.

        Usa la web si está configurada; si no, informes de mercado locales. Devuelve fragmentos con su id.
        """
        if buscador_web is not None:
            try:
                resultados = await buscador_web.buscar(consulta, 4)
                fragmentos = []
                for r in resultados:
                    fid = f"web:{len(libreta.web) + 1}"
                    fragmento = Fragmento(fid, AMBITO_MERCADO, r["url"], r["titulo"], "web", r["contenido"])
                    libreta.web[fid] = fragmento
                    fragmentos.append(fragmento)
                libreta.consultadas(fragmentos)
                if fragmentos:
                    return a_json({"origen": f"web ({buscador_web.nombre})", "fragmentos": [{**f.como_dict(), "url": f.documento} for f in fragmentos]})
            except Exception as exc:  # la web es opcional: cualquier falla cae al corpus local
                logger.warning("Búsqueda web fallida (%s): se usa el corpus local de mercado", exc)
        fragmentos = corpus.buscar(consulta, (AMBITO_MERCADO, AMBITO_FONDO))
        libreta.consultadas(fragmentos)
        if not fragmentos:
            return error("Sin resultados.", "Probá con otras palabras clave, por ejemplo 'tamaño del mercado' o 'ticket anual'.")
        return fragmentos_a_json(fragmentos)

    @tool("registrar_metricas", args_schema=RegistrarMetricasInput)
    async def registrar_metricas(metricas: list[MetricaEntrada]) -> str:
        """Registra métricas del vocabulario con su fuente y su cita literal. Cada una se verifica contra el fragmento citado.

        Devuelve las aceptadas y, para cada rechazada, el motivo y cómo corregirla.
        """
        aceptadas, rechazadas = [], []
        for m in metricas:
            nombre = m.metrica.strip()
            motivo = None
            fragmento = None
            if nombre not in METRICAS:
                motivo = (f"'{nombre}' no está en el vocabulario.", "Usá uno de los nombres del vocabulario de métricas.")
            else:
                ambitos = ambito_mercado if nombre in METRICAS_MERCADO else ambito_startup
                fragmento = _anclar(corpus, libreta, m.cita, m.fuente, ambitos)
                if fragmento is None:
                    donde = "las fuentes permitidas" if nombre in METRICAS_MERCADO else f"el data room de {startup}"
                    motivo = (
                        f"La cita no aparece literalmente en {donde}.",
                        "Copiá la línea exacta del fragmento que devolvió la búsqueda (las métricas de la startup solo valen de su data room).",
                    )
                elif not any(mismo_numero(m.valor, n) for n in numeros_en(m.cita)):
                    motivo = (
                        f"El valor {m.valor:g} no aparece en la cita (números en la cita: {[f'{n:g}' for n in numeros_en(m.cita)]}).",
                        "Registrá el número tal como figura; los porcentajes van de 0 a 100 (6,5 % -> 6.5).",
                    )
            if motivo:
                libreta.rechazos += 1
                rechazadas.append({"metrica": nombre, "valor": m.valor, "error": motivo[0], "sugerencia": motivo[1]})
                continue
            previa = libreta.metricas.get(nombre)
            if previa and not mismo_numero(m.valor, previa.valor):
                libreta.reemplazos.append(f"{nombre}: {previa.valor:g} [{previa.fuente}] -> {m.valor:g} [{fragmento.id}]")
            libreta.metricas[nombre] = MetricaRegistrada(
                metrica=nombre, valor=m.valor, fuente=fragmento.id, cita=limpiar_cita(m.cita), ronda=libreta.version
            )
            aceptadas.append({"metrica": nombre, "valor": m.valor, "fuente": fragmento.id})
        faltan = [x for x in METRICAS_CLAVE + METRICAS_DECLARADAS + METRICAS_MERCADO if x not in libreta.metricas]
        return a_json({"aceptadas": aceptadas, "rechazadas": rechazadas, "faltan_todavia": faltan})

    @tool("registrar_hallazgos", args_schema=RegistrarHallazgosInput)
    async def registrar_hallazgos(hallazgos: list[HallazgoEntrada]) -> str:
        """Registra hallazgos cualitativos (equipo, clientes, competencia, tecnología, riesgos) con su fuente y cita literal."""
        aceptados, rechazados = [], []
        existentes = {h.afirmacion.casefold() for h in libreta.hallazgos}
        for h in hallazgos:
            tema = h.tema.strip().lower()
            fragmento = _anclar(corpus, libreta, h.cita, h.fuente, ambito_mercado)
            if tema not in TEMAS:
                rechazados.append({"afirmacion": h.afirmacion, "error": f"Tema '{tema}' inválido.", "sugerencia": f"Usá uno de {TEMAS}."})
            elif fragmento is None:
                rechazados.append(
                    {"afirmacion": h.afirmacion, "error": "La cita no aparece literalmente en las fuentes.", "sugerencia": "Copiá la frase exacta del fragmento."}
                )
            elif h.afirmacion.casefold() not in existentes:
                libreta.hallazgos.append(Hallazgo(tema=tema, afirmacion=h.afirmacion.strip(), fuente=fragmento.id, cita=limpiar_cita(h.cita)))
                existentes.add(h.afirmacion.casefold())
                aceptados.append({"tema": tema, "fuente": fragmento.id})
        libreta.rechazos += len(rechazados)
        return a_json({"aceptados": aceptados, "rechazados": rechazados})

    return [leer_documento, buscar_en_data_room, buscar_mercado, registrar_metricas, registrar_hallazgos]


def precarga_inicial(corpus: Corpus, startup: str) -> list[Fragmento]:
    """Data room completo + el informe de mercado más afín al pitch (el de su sector)."""
    fragmentos = [f for doc in corpus.documentos(startup) for f in corpus.fragmentos_de(startup, doc)]
    if informe := informe_de_mercado_afin(corpus, startup):
        fragmentos += corpus.fragmentos_de(AMBITO_MERCADO, informe)
    return fragmentos


def informe_de_mercado_afin(corpus: Corpus, startup: str, k: int = 8) -> str | None:
    """Informe de mercado del sector de la startup: votación ponderada por ranking de BM25 con su pitch.

    Votan los k mejores fragmentos (no solo el primero: con el primero solo, VozActiva caía en el
    informe de herramientas de desarrollo). Cada voto pesa 1/posición, así un empate se desempata
    por los fragmentos mejor rankeados.
    """
    pitch = " ".join(f.texto for f in corpus.fragmentos_de(startup, "pitch"))
    votos: dict[str, float] = {}
    for posicion, fragmento in enumerate(corpus.buscar(pitch, (AMBITO_MERCADO,), k=k), start=1):
        votos[fragmento.documento] = votos.get(fragmento.documento, 0) + 1 / posicion
    return max(votos, key=votos.get) if votos else None


def mensaje_para_investigador(
    nombre: str,
    startup: str,
    documentos: dict[str, str],
    instruccion: str,
    previa: Investigacion | None,
    correcciones: list,
    precarga: list[Fragmento] | None = None,
) -> str:
    def faltan(grupo: tuple[str, ...]) -> str:
        pendientes = previa.faltantes(grupo) if previa else list(grupo)
        return ", ".join(pendientes) or "ninguna"

    indice = "\n".join(f"- {doc}: {titulo}" for doc, titulo in documentos.items())
    hallazgos = len(previa.hallazgos) if previa else 0
    cargados = ""
    if precarga:
        cargados = (
            "\n\nDOCUMENTOS YA CARGADOS (data room completo e informe de mercado del sector; no hace falta "
            "leerlos de nuevo, usá el id de cada fragmento como 'fuente'):\n" + fragmentos_a_json(precarga)
        )
    return f"""\
Startup a investigar: {nombre} (id: {startup}).

Instrucción del supervisor: {instruccion or "Relevá el data room completo y el tamaño de mercado."}

Documentos del data room (leelos con leer_documento):
{indice}

Métricas ya registradas (no hace falta repetirlas salvo que una corrección lo pida):
{formatear_metricas(previa)}

Métricas que todavía faltan:
- clave: {faltan(METRICAS_CLAVE)}
- declaradas por la startup: {faltan(METRICAS_DECLARADAS)}
- de mercado: {faltan(METRICAS_MERCADO)}
Hallazgos cualitativos registrados: {hallazgos} (mínimo 3).

Correcciones del crítico dirigidas a vos (aplicalas primero):
{formatear_correcciones(correcciones)}{cargados}"""


def correccion_aplicada(correccion: Correccion, libreta: LibretaInvestigacion) -> bool:
    """Una corrección verificable (métrica + valor) cuenta como atendida solo si el dato quedó con ese valor.

    Si el investigador no la aplica, sigue pendiente: la rúbrica la vuelve a pedir hasta que el
    investigador agote sus intentos (y entonces queda como salvedad en el informe).
    """
    if correccion.metrica is None or correccion.valor_correcto is None:
        return True
    registrada = libreta.metricas.get(correccion.metrica)
    return registrada is not None and mismo_numero(registrada.valor, correccion.valor_correcto)


def crear_nodo_investigador(
    model: BaseChatModel,
    corpus: Corpus,
    settings: DDSettings,
    *,
    buscador_web: BuscadorWeb | None = None,
    reintentos_llm: int = 0,
):
    async def investigador(state: EntradaInvestigador) -> dict[str, Any]:
        startup = state["startup"]
        previa = state.get("investigacion")
        atendidas_antes = previa.correcciones_atendidas if previa else []
        correcciones = correcciones_pendientes(state.get("critica"), "investigador", atendidas_antes)
        libreta = LibretaInvestigacion.desde(previa)
        agente = crear_agente_react(
            model,
            crear_herramientas_investigador(corpus, startup, libreta, buscador_web),
            PROMPT,
            nombre="investigador",
            max_iteraciones=settings.max_iteraciones_especialista,
            reintentos_llm=reintentos_llm,
        )
        precarga = precarga_inicial(corpus, startup) if settings.precargar_data_room and previa is None else None
        if precarga:
            libreta.consultadas(precarga)
        mensaje = mensaje_para_investigador(
            corpus.startups[startup], startup, corpus.documentos(startup), state.get("instruccion", ""), previa, correcciones, precarga
        )
        mensajes = await ejecutar_especialista(agente, mensaje, nombre="investigador", max_iteraciones=settings.max_iteraciones_especialista)
        resumen = texto_final(mensajes) or "(el investigador no dejó resumen)"
        atendidas = [c.id for c in correcciones if correccion_aplicada(c, libreta)]
        investigacion = libreta.a_parcela(resumen, atendidas_antes + atendidas)

        faltan = investigacion.faltantes()
        sin_aplicar = [c.id for c in correcciones if c.id not in atendidas]
        detalle = (
            f"v{investigacion.version}: {len(investigacion.metricas)} métricas ancladas, "
            f"{len(investigacion.hallazgos)} hallazgos, {investigacion.rechazos} registros rechazados"
            + (f"; faltan {', '.join(faltan)}" if faltan else "")
            + (f"; correcciones atendidas: {', '.join(atendidas)}" if atendidas else "")
            + (f"; correcciones SIN aplicar: {', '.join(sin_aplicar)}" if sin_aplicar else "")
        )
        return {
            "investigacion": investigacion,
            "aportes": [Aporte(paso=state["pasos"], agente="investigador", accion="investigación", detalle=detalle)],
            "messages": [AIMessage(content=f"{detalle}\n{resumen}", name="investigador")],
        }

    return investigador
