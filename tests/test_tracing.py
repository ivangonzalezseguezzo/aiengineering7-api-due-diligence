"""Traza: llamadas y tokens por agente, y líneas legibles del recorrido."""

import uuid

from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from due_diligence.state import Aporte
from due_diligence.tracing import ContadorLLM, describir


def generacion(entrada: int, salida: int) -> LLMResult:
    mensaje = AIMessage(content="", usage_metadata={"input_tokens": entrada, "output_tokens": salida, "total_tokens": entrada + salida})
    return LLMResult(generations=[[ChatGeneration(message=mensaje)]])


def test_contador_por_agente_con_etiqueta_o_namespace():
    contador = ContadorLLM()
    llamadas = [
        ({"tags": ["agente:supervisor"]}, generacion(900, 60)),
        ({"tags": ["seq:step:1", "agente:investigador"]}, generacion(1500, 300)),
        ({"metadata": {"langgraph_checkpoint_ns": "analista:abc|agente:def"}}, generacion(1200, 200)),
        ({"tags": ["agente:investigador"]}, generacion(2000, 100)),
    ]
    for kwargs, resultado in llamadas:
        run_id = uuid.uuid4()
        contador.on_chat_model_start({}, [], run_id=run_id, **kwargs)
        contador.on_llm_end(resultado, run_id=run_id)
    resumen = contador.resumen()
    assert resumen["por_agente"]["investigador"] == {"llamadas": 2, "tokens_entrada": 3500, "tokens_salida": 400, "tokens_cache": 0}
    assert resumen["por_agente"]["analista"]["llamadas"] == 1
    assert resumen["total"] == {"llamadas": 4, "tokens_entrada": 5600, "tokens_salida": 660, "tokens_cache": 0}


def test_un_error_cuenta_la_llamada_pero_no_tokens():
    contador = ContadorLLM()
    run_id = uuid.uuid4()
    contador.on_chat_model_start({}, [], run_id=run_id, tags=["agente:critico"])
    contador.on_llm_error(RuntimeError("429"), run_id=run_id)
    assert contador.resumen()["por_agente"]["critico"] == {"llamadas": 1, "tokens_entrada": 0, "tokens_salida": 0, "tokens_cache": 0}


def test_describir_el_recorrido():
    supervisor = describir("supervisor", {"instruccion": "Relevá el data room.", "aportes": [Aporte(paso=1, agente="supervisor", accion="→ investigador", detalle="Empieza por datos.")]})
    assert supervisor == ["[paso 1] supervisor → investigador: Relevá el data room.", "           motivo: Empieza por datos."]
    assert describir("analista", {"aportes": [Aporte(paso=2, agente="analista", accion="análisis", detalle="v1: score 41")]}) == ["    analista: v1: score 41"]
