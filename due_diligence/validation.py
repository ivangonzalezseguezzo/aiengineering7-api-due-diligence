"""Rúbrica de suficiencia y nodo VALIDADOR (determinístico, sin LLM).

La misma rúbrica se usa en tres lugares, así nunca se contradicen:
- el supervisor la ve en su tablero (es la "rúbrica en el prompt" que pide la consigna);
- el supervisor la usa como guarda (`siguiente_pendiente`) cuando el LLM falla o insiste en cerrar;
- el nodo `validador` la aplica antes del END: si no se cumple y quedan pasos, vuelve al
  supervisor con los errores puntuales (bucle de reparación); si no quedan pasos, el informe
  sale sin recomendación (abstenerse también es una respuesta válida).

Cada check nombra al especialista que puede resolverlo. Si ese especialista ya agotó sus
intentos, el check pasa con ADVERTENCIA: lo que no se puede resolver no bloquea el cierre, pero
queda escrito en el informe.
"""

from typing import Literal

from langchain_core.messages import AIMessage

from due_diligence.config import DDSettings
from due_diligence.state import (
    METRICAS_LLM,
    METRICAS_MERCADO,
    Aporte,
    Check,
    Critica,
    Destino,
    EstadoDD,
    Validacion,
)

CALCULOS_BASE = ("calcular_unit_economics", "calcular_scoring")


def rondas_humanas(state: EstadoDD) -> int:
    """Veces que el comité pidió otra vuelta (PE7). Cada una amplía los límites de pasos e intentos."""
    revision = state.get("revision")
    return revision.rondas_pedidas if revision else 0


def limite_intentos(state: EstadoDD, settings: DDSettings, agente: str) -> int:
    return settings.max_intentos(agente) + rondas_humanas(state)


def limite_pasos(state: EstadoDD, settings: DDSettings) -> int:
    return settings.max_pasos + settings.pasos_por_ronda_humana * rondas_humanas(state)


def _correcciones_altas_pendientes(critica: Critica | None, agente: str, atendidas: list[str]) -> list[str]:
    if critica is None:
        return []
    return [c.id for c in critica.correcciones if c.agente_afectado == agente and c.severidad == "alta" and c.id not in atendidas]


def calculos_requeridos(state: EstadoDD) -> list[str]:
    """Los cálculos que el análisis tiene que tener según los datos disponibles."""
    inv = state.get("investigacion")
    requeridos = list(CALCULOS_BASE)
    if inv and not inv.faltantes(METRICAS_LLM):
        requeridos.insert(0, "calcular_costo_llm")
    if inv and not inv.faltantes(METRICAS_MERCADO):
        requeridos.append("estimar_tamano_mercado")
    return requeridos


def evaluar(state: EstadoDD, settings: DDSettings) -> list[Check]:
    inv, ana, cri = state.get("investigacion"), state.get("analisis"), state.get("critica")
    intentos = state.get("intentos", {})

    def agotado(agente: str) -> bool:
        return intentos.get(agente, 0) >= limite_intentos(state, settings, agente)

    def check(nombre: str, ok: bool, detalle: str, responsable: str, *, resoluble: bool = True) -> Check:
        """Si falla pero el responsable ya no puede intentar más, pasa con advertencia."""
        if not ok and (agotado(responsable) or not resoluble):
            return Check(nombre=nombre, ok=True, advertencia=True, responsable=responsable, detalle=f"{detalle} (sin más intentos: queda como salvedad)")
        return Check(nombre=nombre, ok=ok, detalle=detalle, responsable=responsable)

    busquedas = intentos.get("investigador", 0)
    sin_mas_busquedas = busquedas >= settings.rondas_para_no_disponible
    checks: list[Check] = []

    # 1-3. Investigación
    if inv is None:
        checks.append(check("investigacion", False, "El investigador todavía no entregó datos.", "investigador"))
    else:
        n, h = len(inv.metricas), len(inv.hallazgos)
        checks.append(
            check(
                "investigacion",
                n >= settings.minimo_metricas and h >= settings.minimo_hallazgos,
                f"{n} métricas ancladas a su fuente (mínimo {settings.minimo_metricas}) y {h} hallazgos con cita "
                f"(mínimo {settings.minimo_hallazgos}).",
                "investigador",
                resoluble=not sin_mas_busquedas,
            )
        )
    faltan = inv.faltantes() if inv else []
    if inv is not None:
        if not faltan:
            checks.append(Check(nombre="metricas_clave", ok=True, detalle="Todas las métricas clave están registradas.", responsable="investigador"))
        elif sin_mas_busquedas:
            checks.append(
                Check(
                    nombre="metricas_clave", ok=True, advertencia=True, responsable="investigador",
                    detalle=f"No disponibles en las fuentes tras {busquedas} búsquedas: {', '.join(faltan)}.",
                )  # fmt: skip
            )
        else:
            checks.append(check("metricas_clave", False, f"Faltan métricas clave: {', '.join(faltan)}.", "investigador"))
        pendientes = _correcciones_altas_pendientes(cri, "investigador", inv.correcciones_atendidas)
        checks.append(
            check(
                "correcciones_investigador",
                not pendientes,
                f"Correcciones de severidad alta sin atender: {', '.join(pendientes)}." if pendientes else "Sin correcciones altas pendientes.",
                "investigador",
            )
        )

    # 4-5. Análisis
    if ana is None:
        checks.append(check("analisis_vigente", False, "El analista todavía no calculó.", "analista"))
    else:
        faltan_calculos = [c for c in calculos_requeridos(state) if c not in ana.calculos]
        if inv is not None and ana.version_investigacion < inv.version:
            checks.append(
                check(
                    "analisis_vigente",
                    False,
                    f"El análisis v{ana.version} usa la investigación v{ana.version_investigacion}, pero la vigente es la v{inv.version}.",
                    "analista",
                )
            )
        else:
            checks.append(
                check(
                    "analisis_vigente",
                    not faltan_calculos,
                    f"Faltan cálculos: {', '.join(faltan_calculos)}." if faltan_calculos else f"Análisis v{ana.version} completo y al día.",
                    "analista",
                )
            )
        pendientes = _correcciones_altas_pendientes(cri, "analista", ana.correcciones_atendidas)
        checks.append(
            check(
                "correcciones_analista",
                not pendientes,
                f"Correcciones de severidad alta sin atender: {', '.join(pendientes)}." if pendientes else "Sin correcciones altas pendientes.",
                "analista",
            )
        )

    # 6. Crítica
    if ana is not None:
        if cri is None:
            checks.append(check("critica_vigente", False, "El crítico todavía no auditó el análisis.", "critico"))
        else:
            checks.append(
                check(
                    "critica_vigente",
                    cri.version_analisis >= ana.version,
                    f"La crítica v{cri.version} auditó el análisis v{cri.version_analisis}"
                    + ("." if cri.version_analisis >= ana.version else f", pero el vigente es el v{ana.version}."),
                    "critico",
                )
            )
    return checks


def siguiente_pendiente(state: EstadoDD, settings: DDSettings) -> tuple[Destino, str]:
    """Primer check que falla y que su responsable todavía puede resolver; si no hay, finalizar."""
    for c in evaluar(state, settings):
        if not c.ok and c.responsable:
            return c.responsable, f"Resolvé este punto de la rúbrica: {c.detalle}"
    return "finalizar", ""


def formatear_rubrica(checks: list[Check]) -> str:
    lineas = []
    for c in checks:
        marca = "[!]" if c.advertencia else ("[x]" if c.ok else "[ ]")
        responsable = f" (resuelve: {c.responsable})" if not c.ok and c.responsable else ""
        lineas.append(f"{marca} {c.nombre}: {c.detalle}{responsable}")
    return "\n".join(lineas)


def crear_nodo_validador(settings: DDSettings):
    async def validador(state: EstadoDD) -> dict:
        checks = evaluar(state, settings)
        previa = state.get("validacion")
        validacion = Validacion(aprobado=all(c.ok for c in checks), intento=(previa.intento + 1) if previa else 1, checks=checks)
        if validacion.aprobado:
            detalle = f"aprobada (intento {validacion.intento}, {len(validacion.advertencias)} advertencias)"
        else:
            detalle = f"rechazada (intento {validacion.intento}): " + "; ".join(f"{c.nombre}: {c.detalle}" for c in validacion.errores)
        return {
            "validacion": validacion,
            "aportes": [Aporte(paso=state["pasos"], agente="validador", accion="validación", detalle=detalle)],
            "messages": [AIMessage(content=f"Validación {detalle}", name="validador")],
        }

    return validador


def crear_ruta_validador(settings: DDSettings):
    def ruta_validador(state: EstadoDD) -> Literal["supervisor", "informe"]:
        """Aprobada, o sin pasos para corregir -> informe. Rechazada con pasos disponibles -> supervisor."""
        if state["validacion"].aprobado or state["pasos"] >= limite_pasos(state, settings):
            return "informe"
        return "supervisor"

    return ruta_validador
