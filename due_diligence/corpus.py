"""Corpus de la due diligence: data rooms de las startups, fuentes de mercado y la tesis del fondo.

    data/startups/<startup>/*.md   data room de cada startup (ámbito = id de la startup)
    data/mercado/*.md              informes de mercado (ámbito "mercado")
    data/fondo/*.md                tesis de inversión del fondo (ámbito "fondo")

Cada documento Markdown tiene front matter y se parte por secciones "## ": cada sección es un
fragmento con un id estable `<ámbito>/<documento>#<n>` (por ejemplo `vozactiva/finanzas#4`).
Sobre los fragmentos se arma un índice BM25 (rank-bm25) con el tokenizador en español de la PE4.
Es una búsqueda léxica local: no gasta cuota de embeddings y es reproducible. En el proyecto
final la reemplaza el recuperador híbrido de la PE4.

Además resuelve el ANCLAJE de citas (la idea de `anclar_al_texto` de la PE2): un dato solo entra
al estado si su cita literal está en un fragmento real y el valor aparece dentro de la cita.
"""

import logging
import re
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from rank_bm25 import BM25Okapi

from due_diligence.lexico import normalizar, tokenizar

logger = logging.getLogger(__name__)

AMBITO_MERCADO = "mercado"
AMBITO_FONDO = "fondo"
_FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
_SECCION = re.compile(r"^## +(.+?)\s*$", re.MULTILINE)
LARGO_MINIMO_CITA = 12


@dataclass(frozen=True)
class Fragmento:
    id: str
    ambito: str
    documento: str
    titulo: str
    seccion: str
    texto: str

    def como_dict(self) -> dict[str, str]:
        return {"id": self.id, "documento": self.titulo, "seccion": self.seccion, "texto": self.texto}


def _leer_documento(ruta: Path, ambito: str) -> list[Fragmento]:
    contenido = ruta.read_text(encoding="utf-8").replace("\r\n", "\n")
    meta: dict[str, str] = {}
    if coincidencia := _FRONT_MATTER.match(contenido):
        for linea in coincidencia.group(1).splitlines():
            clave, _, valor = linea.partition(":")
            meta[clave.strip()] = valor.strip()
        contenido = contenido[coincidencia.end():]
    titulo = meta.get("titulo", ruta.stem)
    if empresa := meta.get("empresa"):
        titulo = f"{empresa} — {titulo}"
    secciones = list(_SECCION.finditer(contenido))
    fragmentos = []
    for n, seccion in enumerate(secciones, start=1):
        fin = secciones[n].start() if n < len(secciones) else len(contenido)
        texto = contenido[seccion.end():fin].strip()
        fragmentos.append(
            Fragmento(
                id=f"{ambito}/{ruta.stem}#{n}",
                ambito=ambito,
                documento=ruta.stem,
                titulo=titulo,
                seccion=seccion.group(1),
                texto=texto,
            )
        )
    return fragmentos


# --- Números y citas ------------------------------------------------------------------------

# Formato de los documentos: miles con punto y decimales con coma ("USD 1.150.000", "6,5 %").
_NUMERO = re.compile(r"(?<![\d.,])(\d{1,3}(?:\.\d{3})+|\d+)(?:,(\d+))?(?:\s*(millones|millón|millon|mil)\b)?", re.IGNORECASE)


def numeros_en(texto: str) -> list[float]:
    """Números de un texto en formato rioplatense: '1.150.000' -> 1150000, '6,5' -> 6.5, '1,2 millones' -> 1200000."""
    valores = []
    for m in _NUMERO.finditer(texto):
        valor = float(m.group(1).replace(".", "") + (f".{m.group(2)}" if m.group(2) else ""))
        if multiplicador := m.group(3):
            valor *= 1_000 if multiplicador.lower() == "mil" else 1_000_000
        valores.append(valor)
    return valores


def mismo_numero(a: float, b: float, tolerancia: float = 0.005) -> bool:
    return abs(a - b) <= max(abs(b) * tolerancia, 1e-6)


def normalizar_cita(texto: str) -> str:
    texto = normalizar(texto)
    texto = re.sub(r"[*_`|>#\"“”]", " ", texto)
    return re.sub(r"\s+", " ", texto).strip(" .;:-")


def limpiar_cita(cita: str) -> str:
    """Cita para guardar y mostrar: sin la viñeta ni el punto final que el LLM copia de la línea."""
    return cita.strip().lstrip("-*• ").rstrip(" .").strip()


def cita_en_texto(cita: str, texto: str) -> bool:
    """La cita aparece literalmente en el texto (sin distinguir mayúsculas, tildes, espacios ni markdown)."""
    buscada = normalizar_cita(cita)
    return len(buscada) >= LARGO_MINIMO_CITA and buscada in normalizar_cita(texto)


# --- Corpus ---------------------------------------------------------------------------------


class Corpus:
    def __init__(self, fragmentos: list[Fragmento]) -> None:
        if not fragmentos:
            raise ValueError("el corpus está vacío")
        self.fragmentos = fragmentos
        self._por_id = {f.id: f for f in fragmentos}
        self._indices: dict[tuple[str, ...], tuple[list[Fragmento], BM25Okapi]] = {}

    @classmethod
    def desde_directorio(cls, data_dir: Path) -> "Corpus":
        fragmentos: list[Fragmento] = []
        for ruta in sorted((data_dir / "startups").glob("*/*.md")):
            fragmentos += _leer_documento(ruta, ruta.parent.name)
        for ambito in (AMBITO_MERCADO, AMBITO_FONDO):
            for ruta in sorted((data_dir / ambito).glob("*.md")):
                fragmentos += _leer_documento(ruta, ambito)
        corpus = cls(fragmentos)
        logger.info("Corpus: %d fragmentos de %d startups + mercado y fondo", len(fragmentos), len(corpus.startups))
        return corpus

    @cached_property
    def startups(self) -> dict[str, str]:
        """id -> nombre de cada startup con data room."""
        nombres: dict[str, str] = {}
        for f in self.fragmentos:
            if f.ambito not in (AMBITO_MERCADO, AMBITO_FONDO):
                nombres.setdefault(f.ambito, f.titulo.split(" — ")[0])
        return nombres

    def fragmento(self, fragmento_id: str) -> Fragmento | None:
        return self._por_id.get(fragmento_id.strip())

    def documentos(self, ambito: str) -> dict[str, str]:
        """documento -> título de cada documento de un ámbito (el índice del data room)."""
        return {f.documento: f.titulo for f in self.fragmentos if f.ambito == ambito}

    def fragmentos_de(self, ambito: str, documento: str) -> list[Fragmento]:
        """Todas las secciones de un documento, en orden (para leerlo completo)."""
        return [f for f in self.fragmentos if f.ambito == ambito and f.documento == documento.strip()]

    def buscar(self, consulta: str, ambitos: tuple[str, ...], k: int = 4) -> list[Fragmento]:
        """Top-k fragmentos de los ámbitos pedidos según BM25 (solo los que tienen algún término en común)."""
        tokens = tokenizar(consulta)
        if not tokens:
            return []
        candidatos, indice = self._indice(ambitos)
        puntajes = indice.get_scores(tokens)
        orden = sorted(range(len(candidatos)), key=lambda i: puntajes[i], reverse=True)
        return [candidatos[i] for i in orden[:k] if puntajes[i] > 0]

    def _indice(self, ambitos: tuple[str, ...]) -> tuple[list[Fragmento], BM25Okapi]:
        clave = tuple(sorted(ambitos))
        if clave not in self._indices:
            candidatos = [f for f in self.fragmentos if f.ambito in clave]
            if not candidatos:
                raise ValueError(f"no hay documentos para los ámbitos {clave}")
            # El título y la sección suman contexto ("Caja", "Concentración de clientes").
            documentos = [tokenizar(f"{f.titulo} {f.seccion} {f.texto}") for f in candidatos]
            self._indices[clave] = (candidatos, BM25Okapi(documentos))
        return self._indices[clave]

    def anclar(self, cita: str, fuente: str, ambitos: tuple[str, ...]) -> Fragmento | None:
        """Fragmento (dentro de los ámbitos permitidos) que contiene literalmente la cita.

        Se prueba primero con la fuente indicada; si el id está mal escrito o la cita está en
        otro fragmento del mismo ámbito, se acepta ese fragmento (la cita es lo que se verifica).
        """
        indicada = self.fragmento(fuente)
        candidatos = ([indicada] if indicada and indicada.ambito in ambitos else []) + [
            f for f in self.fragmentos if f.ambito in ambitos and f is not indicada
        ]
        return next((f for f in candidatos if cita_en_texto(cita, f.texto)), None)
