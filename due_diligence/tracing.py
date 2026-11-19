"""Traza de la delegación: qué decidió el supervisor, qué aportó cada especialista y cuánto LLM se usó.

- `analizar` ejecuta una due diligence con `astream(stream_mode="updates")`: cada actualización
  es un nodo que terminó, así que la traza refleja exactamente el recorrido por el grafo.
- `ContadorLLM` es un callback que cuenta llamadas y tokens por agente (el nombre sale de la
  etiqueta "agente:<rol>" o del namespace del checkpoint). Es la parte FinOps del proyecto:
  un sistema que analiza el costo de los LLM de otros mide el suyo.
"""

import json
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult
from langchain_core.runnables import RunnableConfig
from langgraph.errors import GraphRecursionError
from langgraph.graph.state import CompiledStateGraph

from due_diligence.state import estado_inicial

LARGO_INSTRUCCION = 220


def _agente(metadata: dict[str, Any] | None, tags: list[str] | None) -> str:
    for tag in tags or []:
        if tag.startswith("agente:"):
            return tag.removeprefix("agente:")
    namespace = (metadata or {}).get("langgraph_checkpoint_ns", "")
    return namespace.split(":")[0] or "otro"


def _modelo(metadata: dict[str, Any] | None, kwargs: dict[str, Any]) -> str:
    """Nombre del modelo que atiende la llamada (LangChain lo publica como `ls_model_name`)."""
    if nombre := (metadata or {}).get("ls_model_name"):
        return str(nombre)
    parametros = kwargs.get("invocation_params") or {}
    return str(parametros.get("model") or parametros.get("model_name") or "desconocido")


def _uso_vacio() -> dict[str, int]:
    # tokens_cache: parte de tokens_entrada que el proveedor leyó de caché (Gemini la cobra ~10 veces menos).
    return {"llamadas": 0, "tokens_entrada": 0, "tokens_salida": 0, "tokens_cache": 0}


class ContadorLLM(BaseCallbackHandler):
    """Llamadas al LLM y tokens por agente y por modelo (incluye reintentos: cada intento es una request).

    El desglose por modelo (PE7) muestra cuándo atendió el modelo de respaldo y alimenta el costo
    propio de cada ejecución (`costos.costo_usd`).
    """

    run_inline = True

    def __init__(self) -> None:
        self.por_agente: dict[str, dict[str, int]] = defaultdict(_uso_vacio)
        self.por_modelo: dict[str, dict[str, int]] = defaultdict(_uso_vacio)
        self._runs: dict[UUID, tuple[str, str]] = {}

    def on_chat_model_start(self, serialized: Any, messages: Any, *, run_id: UUID, tags: list[str] | None = None, metadata: dict[str, Any] | None = None, **kwargs: Any) -> None:
        agente, modelo = _agente(metadata, tags), _modelo(metadata, kwargs)
        self._runs[run_id] = (agente, modelo)
        self.por_agente[agente]["llamadas"] += 1
        self.por_modelo[modelo]["llamadas"] += 1

    def on_llm_end(self, response: LLMResult, *, run_id: UUID, **kwargs: Any) -> None:
        agente, modelo = self._runs.pop(run_id, ("otro", "desconocido"))
        for generaciones in response.generations:
            for generacion in generaciones:
                uso = getattr(getattr(generacion, "message", None), "usage_metadata", None) or {}
                cache = (uso.get("input_token_details") or {}).get("cache_read", 0) or 0
                for destino in (self.por_agente[agente], self.por_modelo[modelo]):
                    destino["tokens_entrada"] += uso.get("input_tokens", 0)
                    destino["tokens_salida"] += uso.get("output_tokens", 0)
                    destino["tokens_cache"] += cache

    def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        self._runs.pop(run_id, None)

    def resumen(self) -> dict[str, Any]:
        agentes = {k: dict(v) for k, v in sorted(self.por_agente.items())}
        modelos = {k: dict(v) for k, v in sorted(self.por_modelo.items())}
        total = {clave: sum(v[clave] for v in agentes.values()) for clave in ("llamadas", "tokens_entrada", "tokens_salida", "tokens_cache")}
        return {"por_agente": agentes, "por_modelo": modelos, "total": total}


def describir(nodo: str, cambios: dict[str, Any]) -> list[str]:
    """Líneas legibles de lo que hizo un nodo, a partir de su actualización del estado."""
    lineas = []
    for aporte in cambios.get("aportes", []):
        if nodo == "supervisor":
            instruccion = cambios.get("instruccion") or ""
            if len(instruccion) > LARGO_INSTRUCCION:
                instruccion = instruccion[:LARGO_INSTRUCCION] + "…"
            lineas.append(f"[paso {aporte.paso}] supervisor {aporte.accion}" + (f": {instruccion}" if instruccion else ""))
            lineas.append(f"           motivo: {aporte.detalle}")
        else:
            lineas.append(f"    {nodo}: {aporte.detalle}")
    return lineas


@dataclass
class ResultadoAnalisis:
    startup: str
    estado: dict[str, Any]
    lineas: list[str]
    uso_llm: dict[str, Any]
    latencia_s: float
    error: str | None = None
    generado_en: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    @property
    def informe(self) -> str | None:
        return self.estado.get("informe")

    @property
    def recomendacion(self) -> str | None:
        return self.estado.get("recomendacion")

    def a_dict(self) -> dict[str, Any]:
        """Traza exportable: recorrido, aportes, parcelas finales y uso del LLM."""
        e = self.estado
        parcelas = {k: (e[k].model_dump() if e.get(k) is not None else None) for k in ("investigacion", "analisis", "critica", "validacion")}
        return {
            "startup": self.startup,
            "generado_en": self.generado_en,
            "recomendacion": self.recomendacion,
            "pasos_del_supervisor": e.get("pasos"),
            "intentos": e.get("intentos"),
            "latencia_s": self.latencia_s,
            "uso_llm": self.uso_llm,
            "error": self.error,
            "recorrido": self.lineas,
            "aportes": [a.model_dump() for a in e.get("aportes", [])],
            "parcelas": parcelas,
        }

    def guardar(self, informes_dir: Path, trazas_dir: Path) -> tuple[Path | None, Path]:
        trazas_dir.mkdir(parents=True, exist_ok=True)
        ruta_traza = trazas_dir / f"{self.startup}.json"
        ruta_traza.write_text(json.dumps(self.a_dict(), ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        ruta_informe = None
        if self.informe:
            informes_dir.mkdir(parents=True, exist_ok=True)
            ruta_informe = informes_dir / f"{self.startup}.md"
            ruta_informe.write_text(self.informe, encoding="utf-8")
        return ruta_informe, ruta_traza


async def analizar(
    grafo: CompiledStateGraph,
    startup: str,
    pedido: str,
    config: RunnableConfig,
    mostrar: Callable[[str], None] | None = print,
) -> ResultadoAnalisis:
    """Corre una due diligence completa y devuelve el estado final, el recorrido y el uso del LLM."""
    contador = ContadorLLM()
    lineas: list[str] = []
    error = None
    inicio = time.perf_counter()
    try:
        async for actualizacion in grafo.astream(estado_inicial(startup, pedido), {**config, "callbacks": [contador]}, stream_mode="updates"):
            for nodo, cambios in actualizacion.items():
                for linea in describir(nodo, cambios or {}):
                    lineas.append(linea)
                    if mostrar:
                        mostrar(linea)
    except GraphRecursionError:
        error = f"Se alcanzó el recursion_limit ({config.get('recursion_limit')}) sin llegar al informe"
        if mostrar:
            mostrar("✘ " + error)
    estado = (await grafo.aget_state(config)).values
    return ResultadoAnalisis(startup, estado, lineas, contador.resumen(), round(time.perf_counter() - inicio, 1), error)
