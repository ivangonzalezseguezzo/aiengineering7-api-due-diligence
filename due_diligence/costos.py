"""Costo propio de cada ejecución (FinOps): tokens por modelo × precio de lista del proveedor.

Es el respaldo del costo que calcula LangSmith: si la plataforma no conoce el precio de un modelo
nuevo, este número igual queda en el job (`GET /tasks/{id}`) y en el informe de la prueba de carga.

Precios en USD por millón de tokens, plan pago "Standard" (no Batch), verificados el 2026-11-18 en
https://ai.google.dev/gemini-api/docs/pricing (redirige a aistudio.google.com/docs/pricing). La
salida incluye los tokens de razonamiento ("thinking"). En el plan gratuito estos modelos no se
cobran: el costo informado es lo que costaría la misma corrida en el plan pago.
"""

from typing import Any

PRECIOS_USD_POR_MILLON: dict[str, tuple[float, float, float]] = {
    # modelo: (entrada, salida, entrada leída de caché)
    # La caché implícita de Gemini cobra los tokens de entrada repetidos ~10 veces menos: el
    # investigador y el crítico reenvían prompts largos en cada ronda, así que pesa en el total.
    # 3.5: $0.03 verificado textual en la página de precios (2026-11-18). 3.1: $0.025 tomado de la
    # misma página pero de un resumen parcial, no confirmado palabra por palabra.
    "gemini-3.5-flash-lite": (0.30, 2.50, 0.03),
    "gemini-3.1-flash-lite": (0.25, 1.50, 0.025),
}


def precio_de(modelo: str) -> tuple[float, float, float] | None:
    """Precio del modelo; tolera el prefijo "models/" y sufijos de versión ("-preview", "-001")."""
    nombre = modelo.removeprefix("models/")
    candidatos = [clave for clave in PRECIOS_USD_POR_MILLON if nombre.startswith(clave)]
    return PRECIOS_USD_POR_MILLON[max(candidatos, key=len)] if candidatos else None


def costo_usd(por_modelo: dict[str, dict[str, int]]) -> dict[str, Any]:
    """Costo total y por modelo a partir del uso que mide `tracing.ContadorLLM`."""
    detalle: dict[str, float] = {}
    sin_precio: list[str] = []
    for modelo, uso in por_modelo.items():
        precio = precio_de(modelo)
        if precio is None:
            sin_precio.append(modelo)
            continue
        entrada, salida, cache = precio
        leidos_de_cache = min(uso.get("tokens_cache", 0), uso.get("tokens_entrada", 0))
        total = (uso.get("tokens_entrada", 0) - leidos_de_cache) * entrada + leidos_de_cache * cache + uso.get("tokens_salida", 0) * salida
        detalle[modelo] = round(total / 1_000_000, 6)
    return {"total_usd": round(sum(detalle.values()), 6), "por_modelo": detalle, "sin_precio": sin_precio}
