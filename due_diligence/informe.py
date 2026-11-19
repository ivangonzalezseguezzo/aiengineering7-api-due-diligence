"""Nodo INFORME: síntesis final determinística (sin LLM) a partir de las parcelas validadas.

Que la síntesis no use un LLM es una decisión: todos los números y las citas del informe salen
del estado, que ya pasó por el anclaje y la proveniencia. Así la fase final no puede alucinar.
La aprobación humana (HITL) antes de emitir la recomendación está en app/hitl.py (PE7); el agente
redactor para la narrativa del memo queda para la entrega final.

Recomendación preliminar:
- SIN RECOMENDACIÓN si la validación no se superó o si faltan 3 o más métricas clave
  ("abstenerse es válido": se escala a un analista humano y se piden los datos);
- si no, la banda del scoring (avanzar a comité / profundizar / no avanzar), pero una red flag
  de severidad alta impide "avanzar a comité" (baja a "profundizar").
"""

from datetime import date

from langchain_core.messages import AIMessage

from due_diligence.corpus import Corpus
from due_diligence.formato import num, pct, usd
from due_diligence.state import METRICAS, METRICAS_CLAVE, Aporte, EstadoDD, Recomendacion

_BANDA_A_RECOMENDACION: dict[str, Recomendacion] = {
    "avanzar a comité": "AVANZAR A COMITÉ",
    "profundizar": "PROFUNDIZAR",
    "no avanzar": "NO AVANZAR",
}


def recomendar(state: EstadoDD) -> tuple[Recomendacion, str]:
    inv, ana, cri, val = state.get("investigacion"), state.get("analisis"), state.get("critica"), state.get("validacion")
    if val is None or not val.aprobado:
        return "SIN RECOMENDACIÓN", "la validación no se superó dentro del límite de pasos: escalar a un analista humano"
    faltan = inv.faltantes() if inv else list(METRICAS_CLAVE)
    if len(faltan) >= 3:
        return "SIN RECOMENDACIÓN", f"información insuficiente: faltan {len(faltan)} métricas clave ({', '.join(faltan)}); pedirlas a la startup"
    scoring = ana.resultado("calcular_scoring") if ana else {}
    if not scoring:
        return "SIN RECOMENDACIÓN", "no hay scoring calculado"
    recomendacion = _BANDA_A_RECOMENDACION[scoring["banda"]]
    altas = [r for r in cri.red_flags if r.severidad == "alta"] if cri else []
    motivo = f"score {num(scoring['score'], 1)}/100 (banda: {scoring['banda']})"
    if altas:
        motivo += f"; {len(altas)} red flags de severidad alta"
        if recomendacion == "AVANZAR A COMITÉ":
            return "PROFUNDIZAR", motivo + " impiden avanzar directo a comité"
    return recomendacion, motivo


def _tabla(encabezados: list[str], filas: list[list[str]]) -> str:
    lineas = ["| " + " | ".join(encabezados) + " |", "|" + "---|" * len(encabezados)]
    lineas += ["| " + " | ".join(f.replace("|", "/") for f in fila) + " |" for fila in filas]
    return "\n".join(lineas)


def redactar(state: EstadoDD, nombre: str, recomendacion: Recomendacion, motivo: str) -> str:
    inv, ana, cri, val = state.get("investigacion"), state.get("analisis"), state.get("critica"), state.get("validacion")
    partes = [
        f"# Due diligence — {nombre}",
        f"Fecha: {date.today():%Y-%m-%d} · Decisiones del supervisor: {state['pasos']} · "
        f"Validación: {'aprobada' if val and val.aprobado else 'NO superada'}",
        f"## Recomendación preliminar: {recomendacion}",
        f"Motivo: {motivo}.",
        "> Recomendación generada por el sistema a partir de datos anclados a sus fuentes. La decisión de "
        "invertir la toma el comité con aprobación humana explícita (ver la sección «Decisión del comité» al final).",
    ]

    # Métricas
    partes.append("## Métricas registradas (investigador)")
    if inv and inv.metricas:
        filas = [[n, num(m.valor, 2).rstrip("0").rstrip(","), m.fuente, f'"{m.cita}"'] for n, m in inv.metricas.items()]
        partes.append(_tabla(["Métrica", "Valor", "Fuente", "Cita"], filas))
        if faltan := inv.faltantes():
            partes.append(f"**No disponibles en las fuentes:** {', '.join(faltan)}.")
        if inv.reemplazos:
            partes.append("**Datos corregidos durante el análisis:**\n" + "\n".join(f"- {r}" for r in inv.reemplazos))
    else:
        partes.append("Sin métricas registradas.")
    if inv and inv.hallazgos:
        partes.append("### Hallazgos cualitativos\n" + "\n".join(f"- ({h.tema}) {h.afirmacion} [{h.fuente}]" for h in inv.hallazgos))

    # Análisis
    partes.append(f"## Análisis (analista, v{ana.version})" if ana else "## Análisis")
    if ana and ana.calculos:
        if llm := ana.resultado("calcular_costo_llm"):
            linea = f"**Costo de LLM:** {usd(llm['costo_llm_mensual_usd'])} por mes ({pct(llm.get('costo_llm_pct_mrr'))} del MRR)."
            if esc := llm.get("escenario"):
                linea += (
                    f" Escenario (uso ×{num(esc['multiplicador_uso'], 1)}, precio {num(esc['variacion_precio_pct'], 0)} %): "
                    f"{usd(esc['costo_llm_mensual_usd'])} ({pct(esc.get('costo_llm_pct_mrr'))} del MRR)."
                )
            partes.append(linea)
        if ue := ana.resultado("calcular_unit_economics"):
            partes.append(
                _tabla(
                    ["ARPA", "Margen bruto", "CAC", "LTV", "LTV/CAC", "Payback", "Runway"],
                    [[
                        usd(ue.get("arpa_usd")), pct(ue.get("margen_bruto_pct")), usd(ue.get("cac_usd")), usd(ue.get("ltv_usd")),
                        num(ue.get("ltv_cac"), 2), f"{num(ue.get('payback_meses'), 1)} meses", f"{num(ue.get('runway_meses'), 1)} meses",
                    ]],
                )  # fmt: skip
            )
            for advertencia in ue.get("advertencias", []):
                partes.append(f"- Advertencia: {advertencia}")
            for metrica, razon in ue.get("no_calculables", {}).items():
                partes.append(f"- No calculable: {metrica} ({razon})")
        if mercado := ana.resultado("estimar_tamano_mercado"):
            partes.append(
                f"**Mercado:** TAM {usd(mercado['tam_usd'])} · SAM {usd(mercado['sam_usd'])} · SOM {usd(mercado['som_usd'])} "
                f"por año. Supuesto de cuota: {mercado.get('justificacion_som', '')}"
            )
        if sc := ana.resultado("calcular_scoring"):
            filas = [[c, num(d["puntaje_0_5"], 1), f"{d['peso_pct']} %", num(d["aporte"], 1)] for c, d in sc["criterios"].items()]
            partes.append(f"**Scoring: {num(sc['score'], 1)}/100 ({sc['banda']})**\n\n" + _tabla(["Criterio", "Puntaje 0-5", "Peso", "Aporte"], filas))
            if sc.get("sin_datos"):
                partes.append(f"Criterios sin dato (suman 0): {', '.join(sc['sin_datos'])}.")
            partes.append(f"- Equipo: {sc.get('justificacion_equipo', '')}\n- Mercado: {sc.get('justificacion_mercado', '')}")
        partes.append(f"**Interpretación del analista:** {ana.interpretacion}")
    else:
        partes.append("Sin cálculos.")

    # Crítica
    partes.append("## Red flags (crítico)")
    if cri and cri.red_flags:
        orden = {"alta": 0, "media": 1, "baja": 2}
        for r in sorted(cri.red_flags, key=lambda r: orden[r.severidad]):
            evidencia = f' Evidencia: "{r.cita}" [{r.fuente}]' if r.cita else ""
            origen = " (regla de contraste)" if r.origen == "contraste" else ""
            partes.append(f"- **[{r.severidad}] {r.titulo}**{origen}: {r.explicacion}{evidencia}")
    else:
        partes.append("Sin red flags registradas.")
    if cri and cri.correcciones:
        atendidas = set((inv.correcciones_atendidas if inv else []) + (ana.correcciones_atendidas if ana else []))
        partes.append("### Correcciones al equipo durante el análisis")
        for c in cri.correcciones:
            estado = "atendida" if c.id in atendidas else "PENDIENTE"
            partes.append(f"- {c.id} → {c.agente_afectado} ({c.severidad}, {estado}): {c.descripcion}")

    # Validación y trazabilidad
    partes.append("## Validación")
    if val:
        for c in val.checks:
            marca = "⚠️" if c.advertencia else ("✅" if c.ok else "❌")
            partes.append(f"- {marca} {c.nombre}: {c.detalle}")
    partes.append("## Trazabilidad: quién aportó qué")
    filas = [[str(a.paso), a.agente, a.accion, a.detalle] for a in state.get("aportes", [])]
    partes.append(_tabla(["Paso", "Agente", "Acción", "Detalle"], filas))
    partes.append(f"*Definiciones de métricas: {len(METRICAS)} en el vocabulario de `state.py`.*")
    return "\n\n".join(partes) + "\n"


def crear_nodo_informe(corpus: Corpus):
    async def informe(state: EstadoDD) -> dict:
        recomendacion, motivo = recomendar(state)
        texto = redactar(state, corpus.startups[state["startup"]], recomendacion, motivo)
        detalle = f"{recomendacion} ({motivo})"
        return {
            "informe": texto,
            "recomendacion": recomendacion,
            "tarea_completa": True,
            "aportes": [Aporte(paso=state["pasos"], agente="informe", accion="síntesis", detalle=detalle)],
            "messages": [AIMessage(content=f"Recomendación preliminar: {detalle}", name="informe")],
        }

    return informe
