"""Configuración de logs compartida por los scripts del proyecto."""

import logging
import sys
import warnings

# El SDK de Gemini (google-genai sobre aiohttp) deja una corrutina sin esperar al armar el error de
# un 503 "high demand". Es inofensivo (el 503 se reintenta igual) y solo ensucia la salida.
warnings.filterwarnings("ignore", message="coroutine 'ClientResponse.json' was never awaited", category=RuntimeWarning)


def configurar_logging(nivel: int = logging.INFO) -> None:
    # La consola de Windows no usa UTF-8 por defecto y rompería las tildes. En Jupyter los streams
    # ya son UTF-8 y no tienen reconfigure.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    logging.basicConfig(
        level=nivel,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    # Los clientes HTTP de los SDK loguean cada request; se silencian para leer solo el agente.
    for ruidoso in ("httpx", "httpcore", "google_genai", "openai", "anthropic", "aiosqlite"):
        logging.getLogger(ruidoso).setLevel(logging.WARNING)
