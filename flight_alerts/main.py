from __future__ import annotations

import argparse
import logging
from datetime import date

from .config import Config, load, routes_with_watches
from .db import Store
from .notifiers import build_notifiers
from .providers import Provider
from .sources import SOURCES, current_source, make_provider
from .rules import evaluate

log = logging.getLogger("flight_alerts")


def pick_queries(cfg: Config, store: Store, limit: int):
    """Rodízio: checa primeiro o que está há mais tempo sem ser consultado."""
    today = date.today()
    pool = []
    for route in routes_with_watches(cfg, store):
        for q in route.queries():
            if q.depart >= today:
                pool.append((store.last_seen(q.key), route, q))
    pool.sort(key=lambda t: t[0])
    return [(r, q) for _, r, q in pool[:limit]]


def run_once(cfg: Config, provider: Provider, store: Store, notifiers, dry_run: bool = False) -> int:
    used = store.searches_this_month()
    metered = getattr(provider, "metered", True)
    room = cfg.monthly_quota - used if metered else cfg.max_searches_per_run
    if room <= 0:
        log.warning("Cota mensal esgotada (%s/%s). Nada a fazer.", used, cfg.monthly_quota)
        return 0

    limit = min(cfg.max_searches_per_run, room)
    sent = 0
    for route, q in pick_queries(cfg, store, limit):
        try:
            offer = provider.cheapest(q, allow_hidden=route.rules.allow_hidden_city)
        except Exception as exc:  # uma busca falha, as outras seguem
            log.error("Falha em %s: %s", q.key, exc)
            continue
        if getattr(provider, "metered", True):
            store.count_search()
        if offer is None:
            log.info("Sem resultado: %s", q.key)
            continue

        # avalia ANTES de gravar, para a oferta não contaminar a mediana/mínimo
        median, n = store.route_median(route.route_id)
        alert = evaluate(
            offer, route.rules, median, n,
            store.route_min(route.route_id), store.last_alert_price(q.key),
        )
        store.save_price(
            route.route_id, q.key, offer.price, offer.currency, offer.airline, offer.stops
        )
        log.info("%s -> %s %.0f", q.key, offer.currency, offer.price)

        if alert:
            text = alert.render()
            if not dry_run:
                for n_ in notifiers:
                    try:
                        n_.send(text)
                    except Exception as exc:
                        log.error("Notificador %s falhou: %s", n_.name, exc)
                store.save_alert(q.key, offer.price)
            else:
                print("[dry-run]\n" + text)
            sent += 1
    return sent


def run_deals(cfg: Config, store: Store, notifiers, dry_run: bool = False, session=None) -> int:
    """Avisa promoções novas do Passagens Imperdíveis que casem com `deals:` do config."""
    from . import deals as dl

    d = cfg.deals or {}
    if not d.get("enabled"):
        return 0
    try:
        _, fresh = dl.check(store, session, d.get("keywords") or [], d.get("max_price"), pages=d.get("pages"))
    except dl.DealsError as exc:
        log.error("Promoções: %s", exc)
        return 0
    for deal in fresh:
        text = f"Promoção nova: {deal.line()}\n{deal.link}"
        if dry_run:
            print("[dry-run]\n" + text)
            continue
        for n_ in notifiers:
            try:
                n_.send(text)
            except Exception as exc:
                log.error("Notificador %s falhou: %s", n_.name, exc)
    return len(fresh)


def main() -> None:
    ap = argparse.ArgumentParser(description="Caçador de passagens baratas")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--mock", action="store_true", help="usa preços falsos (não gasta cota)")
    ap.add_argument("--source", choices=sorted(SOURCES), help="fonte de preços (padrão: FLIGHT_SOURCE ou skiplagged)")
    ap.add_argument("--dry-run", action="store_true", help="não envia nem registra alertas")
    ap.add_argument("--status", action="store_true", help="mostra uso da cota e sai")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    try:  # .env opcional
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    cfg = load(args.config)
    store = Store(cfg.db_path)

    if args.status:
        print(f"Buscas este mês: {store.searches_this_month()}/{cfg.monthly_quota}")
        return

    try:
        provider: Provider = make_provider("mock" if args.mock else (args.source or current_source()), cfg.currency)
    except RuntimeError as e:
        raise SystemExit(str(e))
    notifiers = build_notifiers(cfg.channels)
    n = run_once(cfg, provider, store, notifiers, dry_run=args.dry_run)
    m = run_deals(cfg, store, notifiers, dry_run=args.dry_run)
    print(f"{n} alerta(s) de preço, {m} promoção(ões) nova(s).")


if __name__ == "__main__":
    main()
