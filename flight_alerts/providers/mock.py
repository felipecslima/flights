from __future__ import annotations

import random
import zlib

from ..models import Offer, Query, Seller
from .base import Provider


class MockProvider(Provider):
    """Preços falsos mas estáveis por busca, para testar o fluxo sem gastar cota (use --mock)."""

    name = "mock"
    label = "Teste (preços falsos)"
    metered = False

    def __init__(self, seed: int = 0):
        self.seed = seed
        self.insights = {"price_level": "low", "typical_price_range": [700, 1200]}

    def search(self, query: Query) -> list[Offer]:
        rng = random.Random(zlib.crc32(query.key.encode()) ^ self.seed)
        offers = []
        for i in range(6):
            offers.append(
                Offer(
                    query=query,
                    price=float(rng.randint(250, 1800)),
                    currency="BRL",
                    airline=rng.choice(["LATAM", "GOL", "Azul"]),
                    stops=rng.choice([0, 0, 1]),
                    duration_min=rng.choice([130, 150, 300]),
                    link="https://www.google.com/travel/flights",
                    booking_token=f"mock-{i}",
                    depart_time=f"{query.depart.isoformat()} {rng.randint(5, 21):02d}:00",
                    arrive_time=f"{query.depart.isoformat()} {rng.randint(6, 23):02d}:30",
                )
            )
        return sorted(offers, key=lambda o: o.price)

    def sellers(self, offer: Offer) -> list[Seller]:
        base = offer.price
        return [
            Seller(offer.airline, base, "https://example.com/airline", "a=1&b=2", "together"),
            Seller("Decolar", base + 25, "https://example.com/decolar", "", "together"),
            Seller("MaxMilhas", base + 60, "", "", "together"),
        ]
