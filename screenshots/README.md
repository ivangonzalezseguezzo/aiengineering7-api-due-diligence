# Capturas del dashboard de LangSmith

Evidencia de observabilidad de la prueba de carga (`python scripts/carga.py`). Antes de la corrida se
cargan los precios de Gemini en *Settings → Model pricing* (ver el README principal).

| Archivo | Qué muestra |
|---|---|
| `01_dashboard_traces.png` | Las trazas del proyecto (`due_diligence:<startup>:iniciar` y `:reanudar`) con estado, latencia y tokens |
| `02_trace_detail_tree.png` | Una traza abierta: árbol de nodos del grafo, llamadas al LLM y tokens de cada paso |
| `03_hitl_threads.png` | La vista de threads (`thread_id = job_id`): un análisis pausado en el `interrupt()` y su reanudación |
| `04_metrics_latency_p95.png` | Gráfico de dashboard: latencia P95 por ejecución = 424,2 s |
| `04b_metrics_latency_p95_config.png` | Cómo se calculó ese P95: percentil 95 de la latencia, filtros `Is Trace = true` y metadata que contiene `iniciar` (las 9 ejecuciones del día, listadas a la derecha) |
| `05_metrics_cost.png` | Costo por ejecución de las trazas `iniciar` |
| `06_hitl_awaiting_approval.png` | `GET /tasks/{id}` de un análisis crítico en pausa: `awaiting_approval` con el pedido de aprobación |
