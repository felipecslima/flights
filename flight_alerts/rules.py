from __future__ import annotations

from dataclasses import dataclass

from .models import Alert, Offer


@dataclass
class RuleConfig:
    max_price: float | None = None       # alerta se preço <= teto absoluto
    drop_pct: float = 25.0               # alerta se X% abaixo da mediana histórica da rota
    min_samples: int = 8                 # amostras mínimas para confiar na mediana
    realert_drop_pct: float = 5.0        # só re-alerta a mesma busca se cair mais X%
    max_stops: int | None = None         # filtra ofertas com paradas demais
    allow_hidden_city: bool = False      # aceita passagens de cidade escondida (com riscos)


def evaluate(
    offer: Offer,
    cfg: RuleConfig,
    median: float | None,
    n_samples: int,
    historical_min: float | None,
    last_alert_price: float | None,
) -> Alert | None:
    """Decide se uma oferta merece alerta.

    `median`, `n_samples` e `historical_min` devem refletir o histórico ANTES
    de registrar a oferta atual, senão ela contamina a própria comparação.
    """
    if cfg.max_stops is not None and offer.stops > cfg.max_stops:
        return None
    if offer.hidden_city and not cfg.allow_hidden_city:
        return None

    reasons: list[str] = []

    if cfg.max_price is not None and offer.price <= cfg.max_price:
        reasons.append(f"abaixo do seu teto de {offer.currency} {cfg.max_price:,.0f}".replace(",", "."))

    if median and n_samples >= cfg.min_samples:
        drop = (1 - offer.price / median) * 100
        if drop >= cfg.drop_pct:
            reasons.append(f"{drop:.0f}% abaixo da mediana da rota ({median:,.0f})".replace(",", "."))

    if historical_min is not None and n_samples >= cfg.min_samples and offer.price < historical_min:
        reasons.append("menor preço já visto nessa rota")

    if not reasons:
        return None

    # anti-spam: mesma busca já alertada a preço igual/parecido
    if last_alert_price is not None:
        needed = last_alert_price * (1 - cfg.realert_drop_pct / 100)
        if offer.price > needed:
            return None

    return Alert(offer=offer, reasons=reasons)
