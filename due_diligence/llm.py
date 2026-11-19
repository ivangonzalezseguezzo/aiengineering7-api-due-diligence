"""Fábrica del chat model de LangChain según el proveedor configurado en el entorno.

Es el módulo de la pre-entrega 2: las variables de entorno se validan con Pydantic y el resto
del código no sabe qué proveedor hay detrás (Gemini, OpenAI o Anthropic). Además centraliza:
- la traducción de `temperature` por proveedor (`parametros_sampling`);
- el rate limiter compartido (`InMemoryRateLimiter`, una instancia por cupo);
- la política de reintentos (`es_reintentable`): el SDK no reintenta solo, porque no distingue
  un 429 por minuto (conviene esperar) de un 429 por cupo diario (no tiene sentido reintentar);
- los mensajes de error de una línea que muestran los scripts.
"""

import logging
import os
from functools import cache
from typing import Any, Literal

from langchain_core.exceptions import (
    ModelAuthenticationError,
    ModelError,
    ModelNotFoundError,
    ModelPermissionDeniedError,
    ModelRateLimitError,
    ModelTimeoutError,
)
from langchain_core.language_models import BaseChatModel
from langchain_core.rate_limiters import InMemoryRateLimiter
from pydantic import BaseModel, Field, SecretStr, model_validator

logger = logging.getLogger(__name__)

Provider = Literal["gemini", "openai", "anthropic"]
ThinkingLevel = Literal["low", "medium", "high"]

# IDs reales de cada API (tabla "Modelos por defecto" del README, con fuentes):
# - gemini-3.5-flash-lite: Flash Lite tiene 15 requests/min y 500/día en el plan gratuito
#   (los Flash, 5/min y 20/día: no alcanzan para la demo). Respaldo: gemini-3.1-flash-lite.
#   https://ai.google.dev/gemini-api/docs/models
# - gpt-4o-mini: deprecado, se da de baja el 2027-01-14 (reemplazo: gpt-6-luna)
#   https://developers.openai.com/api/docs/deprecations
# - claude-sonnet-5-5: retiro "no antes del 2027-09-28"
#   (https://platform.claude.com/docs/en/about-claude/models/overview)
# Se pueden cambiar sin tocar código con GEMINI_MODEL / OPENAI_MODEL / ANTHROPIC_MODEL.
DEFAULT_MODELS: dict[str, str] = {
    "gemini": "gemini-3.5-flash-lite",
    "openai": "gpt-4o-mini",
    "anthropic": "claude-sonnet-5-5",
}

API_KEY_ENVS: dict[str, tuple[str, ...]] = {
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "openai": ("OPENAI_API_KEY",),
    "anthropic": ("ANTHROPIC_API_KEY",),
}

ERROR_MAX_CHARS = 200


class LLMSettings(BaseModel):
    provider: Provider = "gemini"
    model: str | None = Field(default=None, description="Si es None se usa DEFAULT_MODELS[provider]")
    api_key: SecretStr | None = Field(default=None, repr=False)
    temperature: float | None = Field(
        default=None, ge=0.0, le=2.0, description="None = valor por defecto del proveedor"
    )
    # Solo aplica a Gemini 3: los modelos que "piensan" gastan parte de max_tokens razonando.
    thinking_level: ThinkingLevel = "low"
    max_tokens: int = Field(default=2048, gt=0, le=32_000)
    timeout: float = Field(default=30.0, gt=0)
    max_retries: int = Field(
        default=2, ge=0, le=10, description="Reintentos propios ante errores transitorios (el SDK no reintenta)"
    )
    # Cupo de requests por minuto (plan gratuito de Gemini: 15 en Flash Lite, 5 en Flash). None = sin límite.
    requests_per_minute: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def completar_y_verificar(self) -> "LLMSettings":
        if not self.model:
            self.model = DEFAULT_MODELS[self.provider]
        if self.api_key is None or not self.api_key.get_secret_value():
            nombres = " o ".join(API_KEY_ENVS[self.provider])
            raise ValueError(f"falta la API key de {self.provider}: definí {nombres} en el .env")
        return self


def settings_from_env(provider: str | None = None, **overrides: Any) -> LLMSettings:
    """Arma LLMSettings desde variables de entorno (ver .env.example); `overrides` tiene prioridad."""
    provider = (provider or os.getenv("LLM_PROVIDER") or "gemini").strip().lower()
    api_key = next((os.getenv(n) for n in API_KEY_ENVS.get(provider, ()) if os.getenv(n)), None)
    data: dict[str, Any] = {
        "provider": provider,
        "model": os.getenv(f"{provider.upper()}_MODEL"),
        "api_key": api_key,
        "temperature": os.getenv("LLM_TEMPERATURE"),
        "thinking_level": os.getenv("LLM_THINKING_LEVEL"),
        "max_tokens": os.getenv("LLM_MAX_TOKENS"),
        "timeout": os.getenv("LLM_TIMEOUT"),
        "max_retries": os.getenv("LLM_MAX_RETRIES"),
        "requests_per_minute": os.getenv("LLM_REQUESTS_PER_MINUTE"),
    }
    data = {k: v for k, v in data.items() if v not in (None, "")}
    data.update(overrides)
    return LLMSettings.model_validate(data)


def _es_gemini_3(s: LLMSettings) -> bool:
    return s.provider == "gemini" and bool(s.model) and s.model.startswith("gemini-3")


def parametros_sampling(s: LLMSettings) -> dict[str, Any]:
    """Traduce LLM_TEMPERATURE a lo que acepta cada proveedor (ver tabla del README).

    - OpenAI y Gemini 2.x: se envía tal cual, solo si se configuró.
    - Gemini 3.x: el sampling es fijo (Google lo deprecó desde 3.5 Flash-Lite / 3.6 Flash).
    - Anthropic (Claude Opus 4.7 en adelante, incluida la familia 5.5): la API responde 400.
    En los dos últimos casos se avisa en el log en lugar de ignorarla en silencio, y la conducta
    del agente queda fijada por el system prompt y el contrato de las herramientas.
    """
    if s.temperature is None:
        return {}
    if s.provider == "anthropic" or _es_gemini_3(s):
        logger.warning("LLM_TEMPERATURE=%s se ignora: %s no acepta parámetros de sampling", s.temperature, s.model)
        return {}
    return {"temperature": s.temperature}


def reintentos_del_sdk(provider: str) -> int:
    """Valor de `max_retries` que desactiva los reintentos internos del SDK.

    En langchain-google-genai, `max_retries` es la cantidad total de intentos y la propia
    librería documenta que hay que pasar 1 (no 0): con versiones viejas de google-genai, 0
    significa "usar el default" (5 intentos). En OpenAI y Anthropic cuenta reintentos: 0 = ninguno.
    """
    return 1 if provider == "gemini" else 0


@cache
def _rate_limiter(requests_per_minute: float) -> InMemoryRateLimiter:
    """Un único limitador por cupo: todos los modelos creados comparten el mismo balde de requests."""
    return InMemoryRateLimiter(requests_per_second=requests_per_minute / 60, max_bucket_size=1)


def get_chat_model(provider: str | None = None, **overrides: Any) -> BaseChatModel:
    """Devuelve ChatGoogleGenerativeAI, ChatOpenAI o ChatAnthropic según la configuración.

    Los reintentos NO los hace el SDK (`reintentos_del_sdk`): los aplica el grafo con `es_reintentable`.
    """
    s = settings_from_env(provider, **overrides)

    comunes: dict[str, Any] = {"timeout": s.timeout, "max_retries": reintentos_del_sdk(s.provider)}
    if s.requests_per_minute is not None:
        comunes["rate_limiter"] = _rate_limiter(s.requests_per_minute)
    sampling = parametros_sampling(s)

    # Imports diferidos: solo hace falta instalar el paquete del proveedor que se usa.
    match s.provider:
        case "gemini":
            from langchain_google_genai import ChatGoogleGenerativeAI

            # El agente razona en el ciclo ReAct (varias llamadas cortas), no en una sola:
            # "low" reduce la latencia de cada ronda y evita que el razonamiento agote max_tokens.
            thinking = {"thinking_level": s.thinking_level} if _es_gemini_3(s) else {}
            return ChatGoogleGenerativeAI(
                model=s.model,
                google_api_key=s.api_key,
                max_output_tokens=s.max_tokens,
                **comunes,
                **sampling,
                **thinking,
            )
        case "openai":
            from langchain_openai import ChatOpenAI

            return ChatOpenAI(model=s.model, api_key=s.api_key, max_tokens=s.max_tokens, **comunes, **sampling)
        case "anthropic":
            from langchain_anthropic import ChatAnthropic

            # `sampling` siempre está vacío acá (ver parametros_sampling).
            return ChatAnthropic(model=s.model, api_key=s.api_key, max_tokens=s.max_tokens, **comunes)


# --- Modelo de respaldo (PE7) ---------------------------------------------------------------

MODELO_RESPALDO_ENV: dict[str, str] = {
    "gemini": "GEMINI_MODEL_RESPALDO",
    "openai": "OPENAI_MODEL_RESPALDO",
    "anthropic": "ANTHROPIC_MODEL_RESPALDO",
}


def modelo_de_respaldo(provider: str | None = None) -> str | None:
    """Modelo de respaldo configurado (vacío o igual al principal = sin respaldo)."""
    s = settings_from_env(provider)
    respaldo = (os.getenv(MODELO_RESPALDO_ENV[s.provider]) or "").strip()
    return respaldo if respaldo and respaldo != s.model else None


def get_chat_model_con_respaldo(provider: str | None = None, **overrides: Any) -> Any:
    """El modelo principal y, si falla con un error del proveedor, el de respaldo.

    Con Gemini, la cuota del plan gratuito es por modelo: gemini-3.1-flash-lite tiene su propio cupo
    y atiende cuando 3.5-flash-lite responde 503 (saturado) o 429 (cupo). Los reintentos propios
    (`es_reintentable`) envuelven a los dos: primero se prueba el respaldo, después se reintenta.
    Las métricas de la prueba de carga usan UN modelo; el respaldo queda registrado por modelo en
    el uso del job (`tracing.ContadorLLM.por_modelo`), así se ve si alguna llamada lo necesitó.
    `with_fallbacks` delega `bind_tools` y `with_structured_output` en los dos modelos.
    """
    principal = get_chat_model(provider, **overrides)
    if overrides.get("model") or (respaldo := modelo_de_respaldo(provider)) is None:
        return principal
    return principal.with_fallbacks([get_chat_model(provider, **{**overrides, "model": respaldo})], exceptions_to_handle=(ModelError,))


# --- Errores del proveedor ----------------------------------------------------------------


def resumir_error(exc: BaseException) -> str:
    """Primera línea del error, recortada: Google incluye el JSON completo de la respuesta."""
    linea = (str(exc).strip().splitlines() or [""])[0]
    return linea if len(linea) <= ERROR_MAX_CHARS else linea[:ERROR_MAX_CHARS] + "..."


def cuota_diaria_agotada(exc: BaseException) -> bool:
    """429 por cupo diario (quotaId GenerateRequestsPerDayPerProjectPerModel-FreeTier en Gemini).

    A diferencia del límite por minuto, no se resuelve esperando unos segundos: reintentar
    solo gasta tiempo (y, si el SDK lo hiciera en bucle, ensucia el panel de cuota).
    """
    return isinstance(exc, ModelRateLimitError) and "PerDay" in str(exc)


def es_reintentable(exc: BaseException) -> bool:
    """Política de reintento del agente ante errores del LLM.

    Se reintentan los transitorios que LangChain marca como tales (rate limit por minuto,
    timeout, conexión, 5xx). No se reintentan la cuota diaria ni los permanentes
    (autenticación, modelo inexistente, request inválido).
    """
    if cuota_diaria_agotada(exc):
        reintentar = False
    else:
        reintentar = isinstance(exc, ModelError) and exc.is_retryable
    logger.warning(
        "Llamada al LLM fallida (%s: %s) -> %s",
        type(exc).__name__,
        resumir_error(exc),
        "se reintenta" if reintentar else "no se reintenta",
    )
    return reintentar


# Paquetes de donde vienen los errores sin tipar (SDK o cliente HTTP).
_MODULOS_PROVEEDOR = frozenset(
    {"google", "openai", "anthropic", "httpx", "httpcore", "langchain_google_genai", "langchain_openai", "langchain_anthropic"}
)


def es_error_del_proveedor(exc: BaseException) -> bool:
    """True si la excepción viene del proveedor del LLM (auth, cuota, modelo inexistente, red)."""
    return isinstance(exc, ModelError) or type(exc).__module__.split(".")[0] in _MODULOS_PROVEEDOR


def describir_error_del_proveedor(exc: BaseException) -> str:
    """Mensaje de una línea con qué pasó y qué hacer, para mostrar en lugar de un traceback."""
    if cuota_diaria_agotada(exc):
        accion = (
            "se agotó el cupo DIARIO de este modelo (se renueva a la medianoche del Pacífico): "
            "no se reintenta; cambiá GEMINI_MODEL (respaldo: gemini-3.1-flash-lite)"
        )
    elif isinstance(exc, ModelRateLimitError):
        accion = "se superó el límite por minuto: bajá LLM_REQUESTS_PER_MINUTE o esperá un minuto"
    elif isinstance(exc, (ModelAuthenticationError, ModelPermissionDeniedError)) or "api key" in str(exc).lower():
        # Gemini responde una key inválida con 400 INVALID_ARGUMENT, no con 401.
        accion = "la API key fue rechazada: revisá la variable *_API_KEY del proveedor en el .env"
    elif isinstance(exc, ModelNotFoundError):
        accion = "el modelo no existe para esa API: revisá GEMINI_MODEL / OPENAI_MODEL / ANTHROPIC_MODEL"
    elif isinstance(exc, ModelTimeoutError):
        accion = "el proveedor no respondió a tiempo: reintentá o subí LLM_TIMEOUT"
    elif isinstance(exc, ModelError) and exc.is_retryable:
        # 5xx (p. ej. el 503 "high demand" de Gemini) o red caída, con los reintentos ya agotados.
        # El 429 y el timeout también son transitorios, pero tienen su propio mensaje arriba.
        accion = (
            "error transitorio del proveedor (servidor saturado, p. ej. 503, o sin conexión) y se agotaron "
            "los reintentos; no es la configuración del .env: esperá unos minutos y reintentá, subí "
            "LLM_MAX_RETRIES o cambiá de modelo (con Gemini, respaldo: gemini-3.1-flash-lite)"
        )
    else:
        accion = "revisá la conexión, la API key y el modelo en el .env"
    return f"Error del proveedor del LLM ({type(exc).__name__}): {accion}. Detalle: {resumir_error(exc)}"
