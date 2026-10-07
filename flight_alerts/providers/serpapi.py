from __future__ import annotations

import requests

from ..models import Offer, Query, Seller
from .base import Provider

ENDPOINT = "https://serpapi.com/search.json"


class SerpApiProvider(Provider):
    """Google Flights via SerpApi (plano grátis: 250 buscas/mês)."""

    name = "serpapi"
    label = "Google Flights (SerpApi)"
    metered = True

    def __init__(self, api_key: str, currency: str = "BRL", hl: str = "pt", timeout: int = 60):
        if not api_key:
            raise ValueError("SERPAPI_KEY não definida")
        self.api_key = api_key
        self.currency = currency
        self.hl = hl
        self.timeout = timeout
        self.insights: dict | None = None  # price_insights da última busca

    def _params(self, query: Query) -> dict:
        params = {
            "engine": "google_flights",
            "departure_id": query.origin,
            "arrival_id": query.destination,
            "outbound_date": query.depart.isoformat(),
            "currency": self.currency,
            "hl": self.hl,
            "type": 1 if query.return_date else 2,  # 1 = ida e volta, 2 = só ida
            "api_key": self.api_key,
        }
        if query.return_date:
            params["return_date"] = query.return_date.isoformat()
        return params

    def _get(self, params: dict) -> dict:
        resp = requests.get(ENDPOINT, params=params, timeout=self.timeout)
        try:
            data = resp.json()
        except ValueError:
            resp.raise_for_status()
            raise RuntimeError("SerpApi: resposta inválida")
        err = data.get("error")
        if err:
            if "returned any results" in err or "no results" in err.lower():
                return {}  # busca válida, só não há voos
            raise RuntimeError(f"SerpApi: {err}")
        resp.raise_for_status()
        return data

    def search(self, query: Query) -> list[Offer]:
        data = self._get(self._params(query))
        self.insights = data.get("price_insights")
        link = data.get("search_metadata", {}).get("google_flights_url", "")
        options = (data.get("best_flights") or []) + (data.get("other_flights") or [])
        offers: list[Offer] = []
        for o in options:
            if not o.get("price"):
                continue
            legs = o.get("flights") or []
            offers.append(
                Offer(
                    query=query,
                    price=float(o["price"]),
                    currency=self.currency,
                    airline=legs[0].get("airline", "") if legs else "",
                    stops=max(len(legs) - 1, 0),
                    duration_min=o.get("total_duration"),
                    link=link,
                    booking_token=o.get("booking_token", ""),
                    depart_time=(legs[0].get("departure_airport") or {}).get("time", "") if legs else "",
                    arrive_time=(legs[-1].get("arrival_airport") or {}).get("time", "") if legs else "",
                )
            )
        return sorted(offers, key=lambda x: x.price)

    def sellers(self, offer: Offer) -> list[Seller]:
        if not offer.booking_token:
            return []
        params = self._params(offer.query)
        params["booking_token"] = offer.booking_token
        data = self._get(params)
        out: list[Seller] = []
        for opt in data.get("booking_options", []):
            for kind, v in opt.items():  # 'together' ou 'separate'
                if not isinstance(v, dict):
                    continue
                parts = [v] if "book_with" in v else [x for x in v.values() if isinstance(x, dict)]
                for p in parts:
                    if "book_with" not in p:
                        continue
                    req = p.get("booking_request") or {}
                    out.append(
                        Seller(
                            name=p["book_with"],
                            price=p.get("price"),
                            url=req.get("url", ""),
                            post_data=req.get("post_data", ""),
                            kind=kind,
                        )
                    )
        return sorted(out, key=lambda s: (s.price is None, s.price or 0))
