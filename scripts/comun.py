"""Cliente de la API para los scripts de evidencia (prueba de carga y prueba de reinicio)."""

import asyncio
import os
import time
from typing import Any

import httpx

FINALES = {"done", "rejected", "failed"}


class ClienteAPI:
    def __init__(self, url: str, api_key: str | None = None) -> None:
        self.http = httpx.AsyncClient(base_url=url.rstrip("/"), timeout=30)
        self.api_key = api_key or os.getenv("API_KEY_APROBACION") or ""

    async def __aenter__(self) -> "ClienteAPI":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.http.aclose()

    async def salud(self) -> dict[str, Any]:
        r = await self.http.get("/health")
        if r.status_code != 200:
            raise SystemExit(f"La API no está sana ({r.status_code}): {r.text}")
        return r.json()

    async def crear(self, startup: str) -> tuple[str, float]:
        """Crea el job y devuelve (job_id, latencia del POST en ms)."""
        inicio = time.perf_counter()
        r = await self.http.post("/tasks", json={"startup": startup})
        latencia_ms = (time.perf_counter() - inicio) * 1000
        r.raise_for_status()
        return r.json()["job_id"], latencia_ms

    async def estado(self, job_id: str) -> dict[str, Any]:
        r = await self.http.get(f"/tasks/{job_id}")
        r.raise_for_status()
        return r.json()

    async def esperar(self, job_id: str, estados: set[str], timeout_s: float, cada_s: float = 2.0) -> dict[str, Any]:
        """Polling hasta que el job llegue a uno de `estados` (o a un estado final)."""
        limite = time.monotonic() + timeout_s
        while True:
            job = await self.estado(job_id)
            if job["estado"] in estados or job["estado"] in FINALES:
                return job
            if time.monotonic() > limite:
                raise TimeoutError(f"el job {job_id} sigue en '{job['estado']}' después de {timeout_s:.0f} s")
            await asyncio.sleep(cada_s)

    async def decidir(self, job_id: str, decision: str, comentario: str = "", aprobador: str = "script de evidencia") -> dict[str, Any]:
        r = await self.http.post(
            f"/tasks/{job_id}/approve",
            json={"decision": decision, "comentario": comentario, "aprobador": aprobador},
            headers={"X-API-Key": self.api_key},
        )
        if r.status_code != 202:
            raise SystemExit(f"La aprobación de {job_id} falló ({r.status_code}): {r.text}")
        return r.json()


def percentil(valores: list[float], p: float) -> float:
    """Percentil con interpolación lineal (el método por defecto de numpy). Con 5 valores, p95 ≈ el máximo."""
    ordenados = sorted(valores)
    if not ordenados:
        return float("nan")
    posicion = (len(ordenados) - 1) * p / 100
    abajo = int(posicion)
    arriba = min(abajo + 1, len(ordenados) - 1)
    return ordenados[abajo] + (ordenados[arriba] - ordenados[abajo]) * (posicion - abajo)
