from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class Query:
    """Uma busca concreta: origem, destino e datas fixas."""

    origin: str
    destination: str
    depart: date
    return_date: date | None = None  # None = só ida

    @property
    def key(self) -> str:
        r = self.return_date.isoformat() if self.return_date else "OW"
        return f"{self.origin}-{self.destination}:{self.depart.isoformat()}:{r}"


@dataclass
class Offer:
    query: Query
    price: float
    currency: str
    airline: str = ""
    stops: int = 0
    duration_min: int | None = None
    link: str = ""
    booking_token: str = ""   # usado para pedir "onde comprar" (SerpApi)
    depart_time: str = ""     # ex.: "2027-01-17 08:00"
    arrive_time: str = ""
    hidden_city: bool = False  # passagem "cidade escondida" (você não pega o último trecho)
    partial: bool = False      # só preço de calendário, sem detalhes de voo (busque a data para ver)


@dataclass
class Place:
    """Destino sugerido por uma busca 'para qualquer lugar'."""

    name: str
    code: str
    price: float
    currency: str = "BRL"
    link: str = ""


@dataclass
class Seller:
    """Quem vende a passagem e por quanto."""

    name: str
    price: float | None = None
    url: str = ""
    post_data: str = ""       # a SerpApi devolve um POST para o checkout do vendedor
    kind: str = ""            # 'together' / 'separate'


@dataclass
class Alert:
    offer: Offer
    reasons: list[str] = field(default_factory=list)

    def render(self) -> str:
        q = self.offer.query
        trecho = f"{q.origin} → {q.destination}"
        datas = q.depart.strftime("%d/%m/%Y")
        if q.return_date:
            datas += f" – {q.return_date.strftime('%d/%m/%Y')}"
        paradas = "direto" if self.offer.stops == 0 else f"{self.offer.stops} parada(s)"
        linhas = [
            f"Passagem barata: {trecho}",
            f"{datas} | {self.offer.airline or 'cia n/d'} | {paradas}",
            f"{self.offer.currency} {self.offer.price:,.0f}".replace(",", "."),
            "Motivo: " + "; ".join(self.reasons),
        ]
        if self.offer.hidden_city:
            linhas.append("Atenção: cidade escondida (só ida, só bagagem de mão; a companhia proíbe).")
        if self.offer.link:
            linhas.append(self.offer.link)
        return "\n".join(linhas)
