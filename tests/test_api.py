"""PE7: la API (FastAPI) sobre un Redis falso. No corre el grafo: solo registra, encola y consulta."""

import json

import httpx
import pytest

from app.config import AppSettings
from app.jobs import Estado
from app.main import crear_app
from due_diligence.config import DDSettings

CLAVE = "clave-de-prueba"


@pytest.fixture
async def cliente(store):
    app = crear_app(store=store, ajustes=AppSettings(api_key_aprobacion=CLAVE), dd=DDSettings())
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            yield c


async def en_pausa(store, job_id: str, rondas: int = 0) -> None:
    """Lleva el job a awaiting_approval como lo haría el worker al llegar al interrupt."""
    await store.transicion(job_id, {Estado.PENDING}, Estado.RUNNING)
    await store.transicion(job_id, {Estado.RUNNING}, Estado.AWAITING_APPROVAL, aprobacion={"rondas_pedidas": rondas, "recomendacion": "NO AVANZAR"})


async def test_health_informa_redis_y_las_startups(cliente):
    r = await cliente.get("/health")
    assert r.status_code == 200
    cuerpo = r.json()
    assert cuerpo["redis"] == "ok" and len(cuerpo["startups"]) == 5
    assert cuerpo["observabilidad"]["plataforma"] == "langsmith"


async def test_crear_tarea_registra_pending_y_encola_en_la_misma_transaccion(cliente, store):
    r = await cliente.post("/tasks", json={"startup": "vozactiva"})
    assert r.status_code == 202
    job_id = r.json()["job_id"]
    assert r.json()["estado"] == "pending" and r.json()["links"]["aprobar"] == f"/tasks/{job_id}/approve"
    assert (await store.obtener(job_id))["estado"] == "pending"
    assert [json.loads(m) for m in await store.redis.lrange(store.cola, 0, -1)] == [{"job_id": job_id, "tipo": "iniciar"}]

    detalle = (await cliente.get(f"/tasks/{job_id}")).json()
    assert detalle["startup"] == "vozactiva" and detalle["llamadas_llm"] == 0 and detalle["ejecuciones_detalle"] == []
    assert detalle["eventos"] == []
    assert [j["job_id"] for j in (await cliente.get("/tasks")).json()] == [job_id]


async def test_errores_de_consulta(cliente):
    assert (await cliente.post("/tasks", json={"startup": "inexistente"})).status_code == 404
    assert (await cliente.get("/tasks/no-existe")).status_code == 404
    job_id = (await cliente.post("/tasks", json={"startup": "contralia"})).json()["job_id"]
    r = await cliente.get(f"/tasks/{job_id}/informe")
    assert r.status_code == 409 and "pending" in r.json()["detail"]


async def test_aprobar_exige_la_clave(cliente, store):
    job_id = (await cliente.post("/tasks", json={"startup": "vozactiva"})).json()["job_id"]
    await en_pausa(store, job_id)
    assert (await cliente.post(f"/tasks/{job_id}/approve", json={"decision": "aprobar"})).status_code == 401
    r = await cliente.post(f"/tasks/{job_id}/approve", json={"decision": "aprobar"}, headers={"X-API-Key": "otra"})
    assert r.status_code == 401


async def test_sin_clave_configurada_no_se_puede_aprobar(store):
    app = crear_app(store=store, ajustes=AppSettings(), dd=DDSettings())
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/tasks/x/approve", json={"decision": "aprobar"}, headers={"X-API-Key": "algo"})
    assert r.status_code == 503 and "API_KEY_APROBACION" in r.json()["detail"]


async def test_aprobar_registra_la_decision_y_encola_la_reanudacion(cliente, store):
    job_id = (await cliente.post("/tasks", json={"startup": "vozactiva"})).json()["job_id"]
    await store.redis.delete(store.cola)
    cabecera = {"X-API-Key": CLAVE}

    r = await cliente.post(f"/tasks/{job_id}/approve", json={"decision": "aprobar"}, headers=cabecera)
    assert r.status_code == 409 and "pending" in r.json()["detail"]  # todavía no está en pausa

    await en_pausa(store, job_id)
    r = await cliente.post(f"/tasks/{job_id}/approve", json={"decision": "rechazar"}, headers=cabecera)
    assert r.status_code == 422 and "comentario es obligatorio" in r.json()["detail"]

    pedido = {"decision": "otra_vuelta", "comentario": "Revisá el churn.", "aprobador": "Ana"}
    r = await cliente.post(f"/tasks/{job_id}/approve", json=pedido, headers=cabecera)
    assert r.status_code == 202 and r.json()["estado"] == "pending"
    job = await store.obtener(job_id)
    assert job["estado"] == "pending" and job["decision"]["comentario"] == "Revisá el churn."
    mensaje = json.loads((await store.redis.lrange(store.cola, 0, -1))[0])
    assert mensaje == {"job_id": job_id, "tipo": "reanudar", "decision": pedido}

    # Una segunda decisión sobre el mismo job ya no está en pausa: 409.
    r = await cliente.post(f"/tasks/{job_id}/approve", json={"decision": "aprobar"}, headers=cabecera)
    assert r.status_code == 409


async def test_no_se_puede_pedir_otra_vuelta_despues_del_maximo(cliente, store):
    job_id = (await cliente.post("/tasks", json={"startup": "vozactiva"})).json()["job_id"]
    await en_pausa(store, job_id, rondas=2)
    r = await cliente.post(
        f"/tasks/{job_id}/approve", json={"decision": "otra_vuelta", "comentario": "Una más."}, headers={"X-API-Key": CLAVE}
    )
    assert r.status_code == 409 and "máximo 2" in r.json()["detail"]


async def test_eventos_sse_hasta_el_cierre(cliente, store):
    job_id = (await cliente.post("/tasks", json={"startup": "vozactiva"})).json()["job_id"]
    await store.agregar_evento(job_id, "▶ comienza")
    await store.transicion(job_id, {Estado.PENDING}, Estado.FAILED, error="falla de prueba")
    r = await cliente.get(f"/tasks/{job_id}/eventos")
    assert r.headers["content-type"].startswith("text/event-stream")
    assert "event: progreso" in r.text and "▶ comienza" in r.text
    assert '"estado": "failed"' in r.text and r.text.rstrip().endswith("data: {}")
