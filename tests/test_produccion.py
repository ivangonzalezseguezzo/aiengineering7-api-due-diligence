"""PE7: costo propio, serializador del checkpointer de Redis y modelo de respaldo."""

import pytest
from langchain_core.exceptions import ModelError
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from langchain_core.runnables import RunnableWithFallbacks

from due_diligence import llm
from due_diligence.costos import costo_usd, precio_de
from due_diligence.state import Aporte, DecisionHumana, RevisionHumana, serializador_redis
from due_diligence.tracing import ContadorLLM

from .conftest import CALCULOS_COMPLETOS, METRICAS_VOZACTIVA_PITCH, ScriptedChatModel, analisis_con, investigacion_desde


def test_costo_por_modelo_con_precios_oficiales():
    uso = {
        "gemini-3.5-flash-lite": {"llamadas": 10, "tokens_entrada": 1_000_000, "tokens_salida": 100_000},
        "models/gemini-3.1-flash-lite": {"llamadas": 1, "tokens_entrada": 200_000, "tokens_salida": 0},
        "modelo-raro": {"llamadas": 1, "tokens_entrada": 5, "tokens_salida": 5},
    }
    costo = costo_usd(uso)
    assert costo["por_modelo"] == {"gemini-3.5-flash-lite": 0.55, "models/gemini-3.1-flash-lite": 0.05}
    assert costo["total_usd"] == pytest.approx(0.60) and costo["sin_precio"] == ["modelo-raro"]
    assert precio_de("gemini-3.5-flash-lite-preview-11-2026") == (0.30, 2.50, 0.03)


def test_el_costo_descuenta_la_cache_como_langsmith():
    """Caso real (traza de VozActiva): 4736 tokens de entrada (2003 leídos de caché) y 124 de salida.

    LangSmith calculó para esa llamada USD 0.00087999 de entrada y 0.00031 de salida.
    """
    uso = {"gemini-3.5-flash-lite": {"llamadas": 1, "tokens_entrada": 4736, "tokens_salida": 124, "tokens_cache": 2003}}
    assert costo_usd(uso)["total_usd"] == pytest.approx(0.00087999 + 0.00031, abs=1e-6)


def test_el_contador_separa_por_modelo():
    contador = ContadorLLM()
    for run, modelo in ((1, "gemini-3.5-flash-lite"), (2, "gemini-3.1-flash-lite")):
        contador.on_chat_model_start({}, [], run_id=run, tags=["agente:analista"], metadata={"ls_model_name": modelo})
        mensaje = AIMessage(
            "ok", usage_metadata={"input_tokens": 100, "output_tokens": 10, "total_tokens": 110, "input_token_details": {"cache_read": 40}}
        )
        contador.on_llm_end(LLMResult(generations=[[ChatGeneration(message=mensaje)]]), run_id=run)
    resumen = contador.resumen()
    assert resumen["por_agente"]["analista"]["llamadas"] == 2
    assert resumen["por_modelo"]["gemini-3.1-flash-lite"] == {"llamadas": 1, "tokens_entrada": 100, "tokens_salida": 10, "tokens_cache": 40}


def test_el_estado_vuelve_de_redis_con_sus_tipos():
    """Sin la lista permitida, AsyncRedisSaver devolvería los contratos Pydantic como dict."""
    serde = serializador_redis()
    valores = {
        "investigacion": investigacion_desde(METRICAS_VOZACTIVA_PITCH),
        "analisis": analisis_con(CALCULOS_COMPLETOS),
        "revision": RevisionHumana(decisiones=[DecisionHumana(accion="otra_vuelta", comentario="Revisá el churn.", ronda=1)]),
        "aportes": [Aporte(paso=1, agente="supervisor", accion="→ investigador", detalle="d")],
        "messages": [HumanMessage("hola")],
    }
    vuelta = serde.loads_typed(serde.dumps_typed(valores))
    assert vuelta == valores
    assert vuelta["investigacion"].faltantes() == valores["investigacion"].faltantes()


def test_respaldo_configurado(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "clave-falsa")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
    monkeypatch.setenv("GEMINI_MODEL_RESPALDO", "gemini-3.1-flash-lite")
    modelo = llm.get_chat_model_con_respaldo()
    assert isinstance(modelo, RunnableWithFallbacks)
    assert [m.model for m in (modelo.runnable, *modelo.fallbacks)] == ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]

    monkeypatch.setenv("GEMINI_MODEL_RESPALDO", "")
    assert not isinstance(llm.get_chat_model_con_respaldo(), RunnableWithFallbacks)


async def test_el_respaldo_atiende_cuando_el_principal_falla():
    principal = ScriptedChatModel(respuestas=[ModelError("503 UNAVAILABLE: high demand")])
    respaldo = ScriptedChatModel(respuestas=[AIMessage("desde el respaldo")])
    modelo = principal.with_fallbacks([respaldo], exceptions_to_handle=(ModelError,)).bind_tools([])
    assert (await modelo.ainvoke([HumanMessage("hola")])).text == "desde el respaldo"
