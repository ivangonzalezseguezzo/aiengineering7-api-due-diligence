"""Configuración de la API y del worker (variables de entorno, validadas con Pydantic).

La del orquestador (frenos, rondas humanas, supervisor híbrido) sigue en `due_diligence/config.py`
y la del LLM en `due_diligence/llm.py`.
"""

import os
from typing import Any

from pydantic import BaseModel, Field, SecretStr


class AppSettings(BaseModel):
    redis_url: str = Field(default="redis://localhost:6379/0", description="Redis 8 o Redis Stack (RedisSaver usa Search y JSON)")
    prefijo: str = Field(default="dd", description="Prefijo de las claves de jobs y de la cola")
    api_key_aprobacion: SecretStr | None = Field(default=None, repr=False, description="Clave del header X-API-Key para aprobar")
    worker_concurrencia: int = Field(default=5, ge=1, le=20, description="Jobs que un worker corre a la vez")
    worker_espera_cola_s: int = Field(default=5, ge=1, le=60, description="Segundos de bloqueo esperando la cola")


def app_settings_from_env(**overrides: Any) -> AppSettings:
    variables = {
        "redis_url": "REDIS_URL",
        "prefijo": "REDIS_PREFIJO",
        "api_key_aprobacion": "API_KEY_APROBACION",
        "worker_concurrencia": "WORKER_CONCURRENCIA",
    }
    data = {campo: os.getenv(var) for campo, var in variables.items()}
    data = {k: v for k, v in data.items() if v not in (None, "")}
    data.update(overrides)
    return AppSettings.model_validate(data)
