"""Prueba de persistencia: el estado sobrevive a la caída del worker (y de la API).

Requisitos: la API corriendo y NINGÚN worker activo (el script levanta y mata los suyos).

    python scripts/prueba_reinicio.py                      # corta el worker con el job en pausa de aprobación
    python scripts/prueba_reinicio.py --modo ejecucion     # lo corta a mitad del análisis

Modo "pausa" (lo que pide la clase): el job queda en awaiting_approval, se mata el worker, se
levanta otro y se aprueba igual: el hilo se retoma desde el checkpoint guardado en Redis. La
startup tiene que ser un caso crítico (por defecto Contralia, que recomienda avanzar a comité).

Modo "ejecucion": se mata el worker en pleno análisis (después del primer especialista). El
mensaje queda en la cola de "procesando"; el worker nuevo lo recupera y el grafo sigue desde el
último checkpoint, sin repetir los nodos que ya habían terminado.

El corte es un kill del proceso (TerminateProcess en Windows, SIGTERM en Linux): no hay apagado
prolijo, como en una caída real. Los logs de cada worker quedan en evidencia/.
"""

import argparse
import asyncio
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))
from comun import ClienteAPI  # noqa: E402

RAIZ = Path(__file__).resolve().parents[1]


def levantar_worker(numero: int, sello: str) -> tuple[subprocess.Popen, Path]:
    log = RAIZ / "evidencia" / f"reinicio-{sello}-worker{numero}.log"
    log.parent.mkdir(exist_ok=True)
    proceso = subprocess.Popen(
        [sys.executable, "-m", "app.worker"], cwd=RAIZ, stdout=log.open("w", encoding="utf-8"), stderr=subprocess.STDOUT
    )
    print(f"[worker {numero}] iniciado (pid {proceso.pid}, log {log.relative_to(RAIZ)})")
    return proceso, log


def matar(proceso: subprocess.Popen, numero: int) -> None:
    proceso.kill()
    proceso.wait(timeout=10)
    print(f"[worker {numero}] MATADO (pid {proceso.pid})")


async def esperar_avance(api: ClienteAPI, job_id: str, eventos_minimos: int, timeout_s: float) -> None:
    """Espera a que el análisis esté corriendo y haya terminado al menos un especialista."""
    limite = time.monotonic() + timeout_s
    while time.monotonic() < limite:
        job = await api.estado(job_id)
        if job["estado"] == "running" and len(job["eventos"]) >= eventos_minimos:
            return
        if job["estado"] not in ("pending", "running"):
            raise SystemExit(f"el job llegó a '{job['estado']}' antes del corte: usá --modo pausa")
        await asyncio.sleep(1)
    raise TimeoutError("el análisis no avanzó a tiempo")


async def main() -> int:
    load_dotenv(RAIZ / ".env")
    parser = argparse.ArgumentParser(description="El estado sobrevive al reinicio del worker")
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--startup", default="contralia")
    parser.add_argument("--modo", choices=["pausa", "ejecucion"], default="pausa")
    parser.add_argument("--timeout", type=float, default=900)
    args = parser.parse_args()
    sello = f"{datetime.now():%Y%m%d-%H%M}-{args.modo}"

    async with ClienteAPI(args.url) as api:
        await api.salud()
        worker, _ = levantar_worker(1, sello)
        try:
            job_id, _ = await api.crear(args.startup)
            print(f"Job {job_id} creado para {args.startup}")
            if args.modo == "pausa":
                job = await api.esperar(job_id, {"awaiting_approval"}, args.timeout)
                if job["estado"] != "awaiting_approval":
                    raise SystemExit(
                        f"{args.startup} terminó en '{job['estado']}' sin pausa (no fue crítica: {job.get('criticidad')}). "
                        "Usá una startup con recomendación positiva (contralia, anamnesis o lupacode)."
                    )
                print(f"Estado antes del corte: {job['estado']} · recomendación {job.get('recomendacion')}")
            else:
                await esperar_avance(api, job_id, eventos_minimos=4, timeout_s=args.timeout)
                print("Estado antes del corte: running (el investigador ya terminó)")
        finally:
            matar(worker, 1)

        job = await api.estado(job_id)
        print(f"Con el worker caído, Redis conserva el job en '{job['estado']}' ({job['ejecuciones']} ejecuciones registradas)")

        worker, _ = levantar_worker(2, sello)
        try:
            if args.modo == "ejecucion":
                job = await api.esperar(job_id, {"awaiting_approval"}, args.timeout)
                print(f"El worker 2 retomó el análisis y llegó a '{job['estado']}'")
            await api.decidir(job_id, "aprobar", aprobador="prueba de reinicio")
            print("Aprobado después del reinicio")
            job = await api.esperar(job_id, set(), args.timeout)
        finally:
            matar(worker, 2)

    print("\nEventos del job:")
    for evento in job["eventos"]:
        print(f"  {evento}")
    print(f"\nResultado: {job['estado']} · {job.get('recomendacion')} · decisión {job.get('decision_final')}")
    return 0 if job["estado"] == "done" else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
