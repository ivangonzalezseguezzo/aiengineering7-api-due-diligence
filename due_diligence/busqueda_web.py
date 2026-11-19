"""Búsqueda web opcional del investigador (Tavily), con fallback al corpus local de mercado.

- Si hay `TAVILY_API_KEY` en el .env, `buscar_mercado` consulta la web con `TavilySearch`
  (paquete langchain-tavily, el reemplazo de `TavilySearchResults` de langchain-community).
- Si no hay clave, o si Tavily falla (red, cuota, clave inválida), se usa el corpus local de
  mercado: la demo sigue siendo reproducible y no depende de un servicio externo.

Cada resultado web se guarda como un fragmento `web:<n>` con su URL, así las citas que se
registren desde la web pasan por el mismo anclaje que las del data room.
"""

import logging
import os
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class BuscadorWeb(Protocol):
    nombre: str

    async def buscar(self, consulta: str, k: int) -> list[dict[str, str]]:
        """Lista de resultados con claves titulo, url y contenido."""
        ...


class BuscadorTavily:
    nombre = "tavily"

    def __init__(self, api_key: str, max_resultados: int = 4) -> None:
        from langchain_tavily import TavilySearch  # import diferido: solo si hay clave

        self._herramienta = TavilySearch(max_results=max_resultados, tavily_api_key=api_key)

    async def buscar(self, consulta: str, k: int) -> list[dict[str, str]]:
        respuesta: Any = await self._herramienta.ainvoke({"query": consulta})
        if not isinstance(respuesta, dict) or "error" in respuesta:
            raise RuntimeError(f"Tavily no devolvió resultados: {str(respuesta)[:200]}")
        return [
            {"titulo": r.get("title", ""), "url": r.get("url", ""), "contenido": r.get("content", "")}
            for r in respuesta.get("results", [])[:k]
        ]


def buscador_desde_entorno() -> BuscadorWeb | None:
    """BuscadorTavily si hay TAVILY_API_KEY; None (corpus local) si no."""
    clave = os.getenv("TAVILY_API_KEY", "").strip()
    if not clave:
        logger.info("Sin TAVILY_API_KEY: buscar_mercado usa el corpus local de mercado")
        return None
    return BuscadorTavily(clave)
