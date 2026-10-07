"""Escolha da fonte de preços (skiplagged | serpapi | mock)."""
from __future__ import annotations

import os

from .providers import MockProvider, Provider, SerpApiProvider, SkiplaggedProvider

SOURCES = {
    "skiplagged": "Skiplagged (grátis, inclui cidade escondida e destinos baratos)",
    "serpapi": "Google Flights via SerpApi (250 buscas/mês, mostra onde comprar)",
    "mock": "Teste (preços falsos)",
}


def parse_fx(text: str) -> dict:
    """'USD_BRL=5.4,EUR_BRL=6.1' -> {'USD': 5.4, 'EUR': 6.1} (1 unidade na moeda de exibição)."""
    out = {}
    for part in (text or "").replace(";", ",").split(","):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        try:
            out[k.strip().split("_")[0].upper()] = float(v.strip().replace(",", "."))
        except ValueError:
            pass
    return out


def current_source(env=None) -> str:
    env = os.environ if env is None else env
    s = (env.get("FLIGHT_SOURCE") or "skiplagged").strip().lower()
    return s if s in SOURCES else "skiplagged"


def make_provider(name: str, currency: str = "BRL", env=None) -> Provider:
    env = os.environ if env is None else env
    if name == "mock":
        return MockProvider()
    if name == "serpapi":
        key = env.get("SERPAPI_KEY", "")
        if not key:
            raise RuntimeError("Falta SERPAPI_KEY no .env (ou troque a fonte para skiplagged).")
        return SerpApiProvider(key, currency)
    return SkiplaggedProvider(currency=currency, fx=parse_fx(env.get("FX_RATES", "")))
