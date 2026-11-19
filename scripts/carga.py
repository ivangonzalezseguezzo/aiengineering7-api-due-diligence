"""Prueba de carga: análisis concurrentes contra la API, con el comité simulado y las métricas de producción.

Requisitos: la API y el worker corriendo (ver README) y API_KEY_APROBACION en el .env.

    python scripts/carga.py                     # 5 peticiones concurrentes, una por startup (lo que pide la consigna)
    python scripts/carga.py --repeticiones 5    # 25 peticiones (5 por startup): p95 con más muestra

Qué hace:
1. Lanza todas las peticiones POST /tasks al mismo tiempo (asyncio.gather) y mide cuánto tarda la
   API en responder cada una: tiene que ser del orden de milisegundos, porque solo encola.
2. Hace polling de cada job. Los casos críticos llegan a `awaiting_approval` y el script los aprueba
   como lo haría el comité (operador humano simulado); los no críticos se emiten solos.
3. Junta, de GET /tasks/{id}: estado, recomendación, criticidad, llamadas al LLM, tokens, costo
   propio (tokens × precio de lista) y el detalle de cada ejecución del worker.
4. Separa tres tiempos (ver README, "Prueba de carga"):
   - espera en cola: desde el POST hasta que el worker toma el job (contención: 1 worker, 5 a la vez);
   - ejecución del grafo: lo que tardó el worker en la ejecución inicial (incluye esperar el rate
     limit del LLM: es la latencia que mide LangSmith en cada traza);
   - total: lo que percibe el cliente, de punta a punta (incluye la aprobación automática).
5. Suma los tokens por agente y los segundos por nodo de todas las ejecuciones: es la "lectura del
   dashboard" (qué nodo consume más tokens y dónde se concentra la latencia), para contrastar con LangSmith.
6. Imprime todo con p50 y p95 y lo guarda en evidencia/carga-<fecha>.json.
"""

import argparse
import asyncio
import json
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))
from comun import ClienteAPI, percentil  # noqa: E402

STARTUPS = ["vozactiva", "contralia", "aulaviva", "anamnesis", "lupacode"]
RAIZ = Path(__file__).resolve().parents[1]


def _segundos_entre(desde: str, hasta: str) -> float:
    return (datetime.fromisoformat(hasta) - datetime.fromisoformat(desde)).total_seconds()


async def un_analisis(api: ClienteAPI, startup: str, job_id: str, post_ms: float, t0: float, timeout_s: float, decision: str) -> dict:
    primero = await api.esperar(job_id, {"awaiting_approval"}, timeout_s)
    hasta_decision = time.perf_counter() - t0
    pauso = primero["estado"] == "awaiting_approval"
    if pauso:
        print(f"  {startup:<10} crítico: en pausa a los {hasta_decision:6.1f} s · {primero.get('recomendacion')} · lo decide el comité simulado", flush=True)
        comentario = "" if decision == "aprobar" else "Rechazado en la prueba de carga."
        await api.decidir(job_id, decision, comentario, aprobador="comité simulado (prueba de carga)")
        final = await api.esperar(job_id, set(), timeout_s)
    else:
        final = primero
        print(f"  {startup:<10} cerró a los {hasta_decision:6.1f} s · {final.get('recomendacion')} · {final['estado']} ({final.get('decision_final')})", flush=True)
    ejecuciones = final.get("ejecuciones_detalle", [])
    inicial = ejecuciones[0] if ejecuciones else {}
    return {
        "startup": startup,
        "job_id": job_id,
        "estado": final["estado"],
        "recomendacion": final.get("recomendacion"),
        "critico": pauso,
        "decision_final": final.get("decision_final"),
        "motivos_criticidad": (final.get("criticidad") or {}).get("motivos", []),
        "error": final.get("error"),
        "post_ms": round(post_ms, 1),
        "espera_cola_s": round(_segundos_entre(final["creado_en"], inicial["inicio"]), 1) if inicial.get("inicio") else None,
        "ejecucion_s": inicial.get("latencia_s"),
        "hasta_decision_s": round(hasta_decision, 1),
        "total_s": round(time.perf_counter() - t0, 1),
        "llamadas_llm": final.get("llamadas_llm"),
        "tokens_entrada": final.get("tokens_entrada"),
        "tokens_salida": final.get("tokens_salida"),
        "costo_usd": final.get("costo_usd"),
        "ejecuciones": ejecuciones,
    }


def tabla(resultados: list[dict]) -> str:
    encabezado = (
        f"{'startup':<10} {'estado':<9} {'recomendación':<18} {'crítico':<7} {'POST ms':>8} {'cola s':>7} "
        f"{'grafo s':>8} {'total s':>8} {'LLM':>4} {'costo USD':>10}"
    )
    filas = [
        f"{r['startup']:<10} {r['estado']:<9} {str(r['recomendacion']):<18} {'sí' if r['critico'] else 'no':<7} {r['post_ms']:>8.1f} "
        f"{r['espera_cola_s'] or 0:>7.1f} {r['ejecucion_s'] or 0:>8.1f} {r['total_s']:>8.1f} {r['llamadas_llm'] or 0:>4} {r['costo_usd'] or 0:>10.5f}"
        for r in resultados
    ]
    return "\n".join([encabezado, "-" * len(encabezado), *filas])


def lectura_del_dashboard(resultados: list[dict]) -> dict:
    """Tokens por agente y segundos por nodo de todas las ejecuciones, en valor y en porcentaje."""
    tokens: dict[str, int] = defaultdict(int)
    segundos: dict[str, float] = defaultdict(float)
    for r in resultados:
        for e in r["ejecuciones"]:
            for agente, uso in e["uso_llm"]["por_agente"].items():
                tokens[agente] += uso["tokens_entrada"] + uso["tokens_salida"]
            for nodo, s in (e.get("duracion_por_nodo_s") or {}).items():
                segundos[nodo] += s

    def con_porcentaje(valores: dict) -> dict:
        total = sum(valores.values()) or 1
        return {k: {"valor": round(v, 1), "pct": round(100 * v / total, 1)} for k, v in sorted(valores.items(), key=lambda kv: -kv[1])}

    return {"tokens_por_agente": con_porcentaje(tokens), "segundos_por_nodo": con_porcentaje(segundos)}


def resumen(resultados: list[dict]) -> dict:
    def stats(clave: str) -> dict:
        valores = [r[clave] for r in resultados if r[clave] is not None]
        return {"n": len(valores), "p50": round(percentil(valores, 50), 2), "p95": round(percentil(valores, 95), 2), "max": max(valores, default=None)}

    costos = [r["costo_usd"] or 0 for r in resultados]
    return {
        "peticiones": len(resultados),
        "post_ms": stats("post_ms"),
        "espera_cola_s": stats("espera_cola_s"),
        "ejecucion_s": stats("ejecucion_s"),
        "total_s": stats("total_s"),
        "costo_usd": {"total": round(sum(costos), 6), "promedio_por_analisis": round(sum(costos) / len(costos), 6)},
        "llamadas_llm": {"total": sum(r["llamadas_llm"] or 0 for r in resultados), "promedio": round(sum(r["llamadas_llm"] or 0 for r in resultados) / len(resultados), 1)},
        "criticos": sum(r["critico"] for r in resultados),
        "estados": {e: sum(r["estado"] == e for r in resultados) for e in sorted({r["estado"] for r in resultados})},
        "modelos": sorted({m for r in resultados for e in r["ejecuciones"] for m in e["uso_llm"].get("por_modelo", {})}),
        "lectura_del_dashboard": lectura_del_dashboard(resultados),
    }


def imprimir_resumen(est: dict) -> None:
    print(
        f"\n{est['peticiones']} peticiones · {est['criticos']} críticas (pasaron por el comité) · estados {est['estados']} · modelos {est['modelos']}"
    )
    for clave, nombre, unidad in (
        ("post_ms", "respuesta del POST", "ms"),
        ("espera_cola_s", "espera en cola", "s"),
        ("ejecucion_s", "ejecución del grafo", "s"),
        ("total_s", "total (cliente)", "s"),
    ):
        s = est[clave]
        print(f"  {nombre:<22} p50 {s['p50']:>8} {unidad} · p95 {s['p95']:>8} {unidad} · máx {s['max']} {unidad}")
    c, ll = est["costo_usd"], est["llamadas_llm"]
    print(f"  costo propio           total USD {c['total']} · promedio por análisis USD {c['promedio_por_analisis']} · {ll['total']} llamadas ({ll['promedio']} por análisis)")
    if est["peticiones"] < 20:
        print(f"  nota: con N = {est['peticiones']} el p95 converge al máximo de la serie (para benchmarking, 30 a 50 corridas)")
    lectura = est["lectura_del_dashboard"]
    print("\nTokens por agente:  " + " · ".join(f"{k} {v['pct']} %" for k, v in lectura["tokens_por_agente"].items()))
    print("Segundos por nodo:  " + " · ".join(f"{k} {v['pct']} %" for k, v in lectura["segundos_por_nodo"].items()))


async def main() -> int:
    load_dotenv(RAIZ / ".env")
    parser = argparse.ArgumentParser(description="Prueba de carga: análisis concurrentes contra la API")
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--startups", default=",".join(STARTUPS), help="ids separados por coma (uno por petición)")
    parser.add_argument("--repeticiones", type=int, default=1, help="veces que se repite la lista (5 startups × N peticiones)")
    parser.add_argument("--decision", default="aprobar", choices=["aprobar", "rechazar"], help="qué decide el comité simulado")
    parser.add_argument("--timeout", type=float, default=3600, help="segundos máximos por análisis")
    args = parser.parse_args()
    startups = [s.strip() for s in args.startups.split(",") if s.strip()] * max(1, args.repeticiones)

    async with ClienteAPI(args.url) as api:
        salud = await api.salud()
        print(f"API ok · Redis {salud['redis']} · observabilidad: {salud['observabilidad']}")
        print(f"Lanzando {len(startups)} peticiones concurrentes: {', '.join(startups)}")
        t0 = time.perf_counter()
        creados = await asyncio.gather(*(api.crear(s) for s in startups))
        for startup, (job_id, ms) in zip(startups, creados):
            print(f"  POST {startup:<10} -> {job_id} en {ms:.1f} ms")
        resultados = await asyncio.gather(
            *(un_analisis(api, s, job_id, ms, t0, args.timeout, args.decision) for s, (job_id, ms) in zip(startups, creados))
        )

    estadisticas = resumen(resultados)
    print("\n" + tabla(resultados))
    imprimir_resumen(estadisticas)
    salida = RAIZ / "evidencia" / f"carga-{len(resultados)}-{datetime.now():%Y%m%d-%H%M}.json"
    salida.parent.mkdir(exist_ok=True)
    salida.write_text(
        json.dumps({"fecha": datetime.now().isoformat(timespec="seconds"), "resumen": estadisticas, "analisis": resultados}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nEvidencia: {salida.relative_to(RAIZ)}")
    return 0 if all(r["estado"] in ("done", "rejected") for r in resultados) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
