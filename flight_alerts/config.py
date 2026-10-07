from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import yaml

from .models import Query
from .rules import RuleConfig


@dataclass
class Route:
    origin: str
    destination: str
    depart_from: date
    depart_to: date
    step_days: int = 7
    trip_days: list[int] = field(default_factory=list)  # vazio = só ida
    rules: RuleConfig = field(default_factory=RuleConfig)

    @property
    def route_id(self) -> str:
        return f"{self.origin}-{self.destination}"

    def queries(self) -> list[Query]:
        out: list[Query] = []
        d = self.depart_from
        while d <= self.depart_to:
            if self.trip_days:
                for n in self.trip_days:
                    out.append(Query(self.origin, self.destination, d, d + timedelta(days=n)))
            else:
                out.append(Query(self.origin, self.destination, d))
            d += timedelta(days=self.step_days)
        return out


@dataclass
class Config:
    routes: list[Route]
    channels: list[str]
    monthly_quota: int = 250
    max_searches_per_run: int = 4
    currency: str = "BRL"
    db_path: str = "flights.db"
    deals: dict = field(default_factory=dict)   # {enabled, keywords, max_price}


def routes_with_watches(cfg: Config, store) -> list[Route]:
    """Rotas do config.yaml + alertas criados pelo CLI (tabela watches)."""
    routes = list(cfg.routes)
    for w in store.list_watches():
        routes.append(
            Route(
                origin=w["origin"],
                destination=w["destination"],
                depart_from=date.fromisoformat(w["depart_from"]),
                depart_to=date.fromisoformat(w["depart_to"]),
                step_days=w["step_days"],
                trip_days=[int(x) for x in w["trip_days"].split(",") if x],
                rules=RuleConfig(max_price=w["max_price"], max_stops=w["max_stops"],
                                 allow_hidden_city=bool(w.get("allow_hidden", 0))),
            )
        )
    return routes


def _d(v) -> date:
    return v if isinstance(v, date) else date.fromisoformat(str(v))


def load(path: str) -> Config:
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    defaults = raw.get("defaults", {})
    routes: list[Route] = []
    for r in raw["routes"]:
        merged = {**defaults, **r}
        rules = RuleConfig(
            max_price=merged.get("max_price"),
            drop_pct=merged.get("drop_pct", 25.0),
            min_samples=merged.get("min_samples", 8),
            realert_drop_pct=merged.get("realert_drop_pct", 5.0),
            max_stops=merged.get("max_stops"),
            allow_hidden_city=bool(merged.get("allow_hidden_city", False)),
        )
        routes.append(
            Route(
                origin=merged["origin"].upper(),
                destination=merged["destination"].upper(),
                depart_from=_d(merged["depart_from"]),
                depart_to=_d(merged["depart_to"]),
                step_days=merged.get("step_days", 7),
                trip_days=merged.get("trip_days", []),
                rules=rules,
            )
        )

    return Config(
        routes=routes,
        channels=raw.get("channels", ["console"]),
        monthly_quota=raw.get("monthly_quota", 250),
        max_searches_per_run=raw.get("max_searches_per_run", 4),
        currency=raw.get("currency", "BRL"),
        db_path=raw.get("db_path", "flights.db"),
        deals=raw.get("deals") or {},
    )
