from __future__ import annotations

from ..models import Offer, Place, Query, Seller


class Provider:
    """Contrato de uma fonte de preços.

    Implemente `search` (todas as ofertas) e, se a fonte souber, `sellers`.
    `cheapest` é derivado de `search`; fontes simples podem sobrescrever só `cheapest`.
    Cada chamada à fonte externa conta como 1 busca de cota.
    """

    name: str = "base"
    label: str = "Fonte"
    metered: bool = True              # cada busca gasta cota?
    supports_calendar: bool = False   # calendário de preços (vários dias numa chamada)
    supports_destinations: bool = False  # "para onde for mais barato"
    supports_hidden: bool = False     # pode devolver cidade escondida

    def search(self, query: Query) -> list[Offer]:
        raise NotImplementedError

    def cheapest(self, query: Query, allow_hidden: bool = True) -> Offer | None:
        offers = self.search(query)
        if not allow_hidden:
            offers = [o for o in offers if not o.hidden_city]
        return min(offers, key=lambda o: o.price) if offers else None

    def calendar(self, origin: str, destination: str, start, end) -> list[Offer]:
        """Menor preço por dia de ida (ofertas parciais). Só se supports_calendar."""
        raise NotImplementedError

    def destinations(self, origin: str, start=None, end=None) -> list[Place]:
        """Destinos mais baratos a partir de `origin`. Só se supports_destinations."""
        raise NotImplementedError

    def sellers(self, offer: Offer) -> list[Seller]:
        """Onde comprar essa oferta. Fontes sem essa informação devolvem lista vazia."""
        return []
