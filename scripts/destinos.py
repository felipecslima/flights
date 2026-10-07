"""Lista os destinos mais baratos saindo de uma cidade (Skiplagged), sem menu.

Uso:  python3 scripts/destinos.py [ORIGEM] [AAAA-MM-DD] [quantos]
Ex.:  python3 scripts/destinos.py GRU 2027-02-10 20
"""
from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from flight_alerts.places import money  # noqa: E402
from flight_alerts.sources import make_provider  # noqa: E402


def main() -> None:
    a = sys.argv[1:]
    origin = (a[0] if a else "GRU").upper()
    day = date.fromisoformat(a[1]) if len(a) > 1 else date.today() + timedelta(days=45)
    n = int(a[2]) if len(a) > 2 else 20
    prov = make_provider("skiplagged")
    places = prov.destinations(origin, day, day)
    print(f"Destinos mais baratos saindo de {origin} em {day:%d/%m/%Y} (só ida, 'a partir de'):\n")
    for i, p in enumerate(places[:n], 1):
        print(f"{i:>2}. {p.name:<28} {p.code:<4} {money(p.price, p.currency):>10}   {p.link.split('?')[0]}")
    if not places:
        print("Nada encontrado.")


if __name__ == "__main__":
    main()
