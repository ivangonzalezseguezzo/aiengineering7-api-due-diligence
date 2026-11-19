"""Un análisis con el LLM real SIN Redis ni API (checkpointer en memoria): sirve para probar el grafo.

    python scripts/analisis_local.py lupacode
    python scripts/analisis_local.py vozactiva --decision rechazar

Corre el mismo grafo de producción (supervisor híbrido, precarga, pausa human-in-the-loop) hasta la
pausa, muestra el resumen que vería el comité, decide automáticamente y muestra el uso y el costo.
No reemplaza la prueba de producción (scripts/carga.py contra la API): no usa Redis ni la cola.
"""

import argparse
import asyncio
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from app.graph import config_hilo, crear_grafo  # noqa: E402
from app.worker import PEDIDO_POR_DEFECTO, describir_excepcion  # noqa: E402
from due_diligence.costos import costo_usd  # noqa: E402
from due_diligence.state import estado_inicial, serializador  # noqa: E402
from due_diligence.tracing import ContadorLLM, describir  # noqa: E402


async def main() -> int:
    load_dotenv(RAIZ / ".env")
    parser = argparse.ArgumentParser()
    parser.add_argument("startup")
    parser.add_argument("--decision", default="aprobar", choices=["aprobar", "rechazar"])
    args = parser.parse_args()

    grafo, corpus, settings = crear_grafo(InMemorySaver(serde=serializador()))
    contador = ContadorLLM()
    config = {**config_hilo(f"local-{args.startup}", settings), "callbacks": [contador], "metadata": {"origen": "analisis_local"}}
    inicio = time.perf_counter()
    entrada = estado_inicial(args.startup, PEDIDO_POR_DEFECTO.format(nombre=corpus.startups[args.startup]))
    try:
        decision = {"decision": args.decision, "comentario": "Decisión automática del análisis local.", "aprobador": "analisis_local"}
        paso: object = entrada
        while paso is not None:
            async for actualizacion in grafo.astream(paso, config, stream_mode="updates"):
                for nodo, cambios in actualizacion.items():
                    if nodo == "__interrupt__":
                        resumen = cambios[0].value
                        print(f"\n⏸ PAUSA ({resumen['nivel_riesgo']}): {resumen['recomendacion']} · score {resumen['score']} · motivos: {resumen['motivos_pausa']}\n")
                        continue
                    for linea in describir(nodo, cambios or {}):
                        print(linea)
            # Un caso crítico queda en pausa y se decide; uno no crítico ya terminó (emisión automática).
            paso = Command(resume=decision) if (await grafo.aget_state(config)).interrupts else None
    except Exception as exc:
        print(f"✘ {describir_excepcion(exc)}", file=sys.stderr)
        return 1
    valores = (await grafo.aget_state(config)).values
    uso = contador.resumen()
    print(f"\nDecisión final: {valores.get('decision_final')} · recomendación {valores.get('recomendacion')}")
    print(f"Llamadas al LLM: {uso['total']} · por agente: { {k: v['llamadas'] for k, v in uso['por_agente'].items()} }")
    print(f"Por modelo: {uso['por_modelo']}")
    print(f"Costo (plan pago): {costo_usd(uso['por_modelo'])} · {time.perf_counter() - inicio:.1f} s")
    salida = RAIZ / "evidencia" / f"local-{args.startup}.md"
    salida.parent.mkdir(exist_ok=True)
    salida.write_text(valores.get("informe_final") or valores.get("informe") or "", encoding="utf-8")
    print(f"Informe: {salida.relative_to(RAIZ)}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
