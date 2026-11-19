"""Formato de números para el informe y las trazas (miles con punto, decimales con coma)."""


def num(valor: float | None, decimales: int = 0) -> str:
    if valor is None:
        return "s/d"
    texto = f"{valor:,.{decimales}f}"
    return texto.replace(",", "_").replace(".", ",").replace("_", ".")


def usd(valor: float | None, decimales: int = 0) -> str:
    return "s/d" if valor is None else f"USD {num(valor, decimales)}"


def pct(valor: float | None, decimales: int = 1) -> str:
    return "s/d" if valor is None else f"{num(valor, decimales)} %"
