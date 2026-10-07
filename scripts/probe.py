"""Teste rápido das fontes de preço. Rode no seu computador (precisa de internet livre).

    python scripts/probe.py GRU SSA 2027-01-17 [2027-01-24]

Cada fonte só roda se a chave existir (SERPAPI_KEY, TRAVELPAYOUTS_TOKEN no .env);
fast-flights não precisa de chave (pip install fast-flights).
Gasta ~2 buscas da SerpApi (busca + lista de sites de venda).
"""
from __future__ import annotations

import os
import sys

import requests

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


def fast_flights(o, d, dep, ret):
    from fast_flights import FlightQuery, Passengers, create_query, get_flights

    legs = [FlightQuery(date=dep, from_airport=o, to_airport=d)]
    if ret:
        legs.append(FlightQuery(date=ret, from_airport=d, to_airport=o))
    q = create_query(
        flights=legs, seat="economy", trip="round-trip" if ret else "one-way",
        passengers=Passengers(adults=1), language="pt-BR", currency="BRL",
    )
    res = get_flights(q)
    print(f"{len(res)} resultados (mostrando 5; esta fonte NÃO informa onde comprar)")
    for f in list(res)[:5]:
        print("  ", repr(f)[:300])


def serpapi(o, d, dep, ret):
    key = os.environ["SERPAPI_KEY"]
    base = {"engine": "google_flights", "currency": "BRL", "hl": "pt", "api_key": key}
    p = {**base, "departure_id": o, "arrival_id": d, "outbound_date": dep, "type": 2}
    if ret:
        p.update(type=1, return_date=ret)
    data = requests.get("https://serpapi.com/search.json", params=p, timeout=60).json()
    if data.get("error"):
        print("erro:", data["error"])
        return
    opts = (data.get("best_flights") or []) + (data.get("other_flights") or [])
    opts = sorted((x for x in opts if x.get("price")), key=lambda x: x["price"])
    print(f"{len(opts)} opções; insights:", data.get("price_insights"))
    for x in opts[:5]:
        legs = x.get("flights", [])
        print("  R$", x["price"], "|", legs[0].get("airline") if legs else "?", "|", len(legs) - 1, "parada(s)")
    if not opts or not opts[0].get("booking_token"):
        print("sem booking_token na melhor opção")
        return
    # 2ª chamada: quem vende a passagem mais barata
    p2 = {**p, "booking_token": opts[0]["booking_token"]}
    b = requests.get("https://serpapi.com/search.json", params=p2, timeout=60).json()
    print("\nONDE COMPRAR (opção mais barata):")
    for bo in b.get("booking_options", []):
        for kind, v in bo.items():  # 'together' ou 'separate'
            req = v.get("booking_request", {})
            print(f"  {v.get('book_with')}: R$ {v.get('price')} ({kind}) -> {req.get('url', 'sem url')}")


def travelpayouts(o, d, dep, ret):
    p = {
        "origin": o, "destination": d, "departure_at": dep[:7] if not ret else dep,
        "currency": "brl", "sorting": "price", "limit": 5,
        "token": os.environ["TRAVELPAYOUTS_TOKEN"],
    }
    if ret:
        p["return_at"] = ret
    r = requests.get("https://api.travelpayouts.com/aviasales/v3/prices_for_dates", params=p, timeout=30)
    data = r.json()
    print("sucesso:", data.get("success"), "| moeda:", data.get("currency"), "| n:", len(data.get("data", [])))
    for x in data.get("data", []):
        print("  R$", x.get("price"), x.get("airline"), x.get("departure_at"),
              "https://www.aviasales.com" + x.get("link", ""))


def main():
    if len(sys.argv) < 4:
        sys.exit(__doc__)
    o, d, dep = sys.argv[1:4]
    ret = sys.argv[4] if len(sys.argv) > 4 else None
    jobs = [("fast-flights", fast_flights, True),
            ("SerpApi", serpapi, bool(os.environ.get("SERPAPI_KEY"))),
            ("Travelpayouts", travelpayouts, bool(os.environ.get("TRAVELPAYOUTS_TOKEN")))]
    for name, fn, ok in jobs:
        print(f"\n===== {name} =====")
        if not ok:
            print("pulado (sem chave)")
            continue
        try:
            fn(o, d, dep, ret)
        except Exception as e:
            print("FALHOU:", type(e).__name__, str(e)[:300])


if __name__ == "__main__":
    main()
