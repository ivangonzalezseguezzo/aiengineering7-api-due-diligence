"""Piezas compartidas por los especialistas: formato de las herramientas y del contexto mínimo."""

import json
from typing import Any

from due_diligence.corpus import Fragmento
from due_diligence.state import METRICAS, Correccion, Critica, Investigacion


def a_json(datos: Any) -> str:
    return json.dumps(datos, ensure_ascii=False, default=str)


def error(mensaje: str, sugerencia: str, **extra: Any) -> str:
    """Error devuelto como dato (no excepción): el agente lo lee y corrige en la ronda siguiente."""
    return a_json({"error": mensaje, "sugerencia": sugerencia, **extra})


def fragmentos_a_json(fragmentos: list[Fragmento]) -> str:
    return a_json({"total": len(fragmentos), "fragmentos": [f.como_dict() for f in fragmentos]})


def correcciones_pendientes(critica: Critica | None, agente: str, atendidas: list[str]) -> list[Correccion]:
    if critica is None:
        return []
    return [c for c in critica.correcciones if c.agente_afectado == agente and c.id not in atendidas]


def formatear_correcciones(correcciones: list[Correccion]) -> str:
    if not correcciones:
        return "(ninguna)"
    lineas = []
    for c in correcciones:
        dato = f" Registrá {c.metrica} = {c.valor_correcto:g}." if c.metrica and c.valor_correcto is not None else ""
        evidencia = f' Evidencia: "{c.cita}" [{c.fuente}]' if c.cita else ""
        lineas.append(f"- {c.id} ({c.severidad}): {c.descripcion}{dato}{evidencia}")
    return "\n".join(lineas)


def formatear_metricas(investigacion: Investigacion | None, *, con_definicion: bool = False) -> str:
    if investigacion is None or not investigacion.metricas:
        return "(ninguna)"
    lineas = []
    for nombre, m in investigacion.metricas.items():
        definicion = f" — {METRICAS[nombre]}" if con_definicion else ""
        lineas.append(f"- {nombre} = {m.valor:g}{definicion} [fuente: {m.fuente}]")
    return "\n".join(lineas)


def formatear_hallazgos(investigacion: Investigacion | None) -> str:
    if investigacion is None or not investigacion.hallazgos:
        return "(ninguno)"
    return "\n".join(f"- ({h.tema}) {h.afirmacion} [fuente: {h.fuente}]" for h in investigacion.hallazgos)


def vocabulario() -> str:
    return "\n".join(f"- {nombre}: {definicion}" for nombre, definicion in METRICAS.items())
