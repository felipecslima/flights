"""Terminal interativo: pesquisar voos, achar o dia mais barato, onde comprar, alertas e histórico.

    python3 run.py             # recomendado: prepara tudo e abre
    python -m flight_alerts.cli [--mock] [--plain]
"""
from __future__ import annotations

import argparse
import html
import os
import statistics
import sys
import tempfile
import time
import webbrowser
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import parse_qsl

from rich import box
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .config import Config, load
from .db import Store
from .envfile import write_key
from .models import Offer, Query, Seller
from .places import bar, dur, fmt_date, hhmm, money, place_label, short_place, sparkline
from .providers import Provider
from .sources import SOURCES, current_source, make_provider
from .telegram import TelegramApi, TelegramError, mask, valid_token_format
from .ui import ACCENT, BACK, BAD, DIM, GOOD, Back, Choice, FancyPrompter, PlainPrompter, Prompter

SYMBOLS = {"BRL": "R$", "USD": "US$", "EUR": "€", "GBP": "£"}
GF_URL = "https://www.google.com/travel/flights"
MAX_FLEX_SEARCHES = 40


def stops_label(n: int) -> str:
    return "direto" if n == 0 else ("1 escala" if n == 1 else f"{n} escalas")


def pretty_key(key: str) -> str:
    """'GRU-SSA:2027-01-20:OW' -> '20/01 · só ida'; '...:2027-01-20:2027-01-27' -> '20/01 → 27/01'."""
    try:
        _, dep, ret = key.split(":")
        d = date.fromisoformat(dep)
        if ret == "OW":
            return f"{d:%d/%m} · só ida"
        return f"{d:%d/%m} → {date.fromisoformat(ret):%d/%m}"
    except ValueError:
        return key


def friendly_error(exc: Exception) -> str:
    msg = str(exc)
    low = msg.lower()
    name = type(exc).__name__
    if "invalid api key" in low or "api key" in low and "invalid" in low:
        return "A SerpApi recusou a chave. Confira o SERPAPI_KEY no arquivo .env (tem que ser a 'Private API Key')."
    if "run out of searches" in low or "out of searches" in low or "plan limit" in low:
        return "A cota de buscas da SerpApi acabou por este mês."
    if name in ("ConnectionError", "Timeout", "ConnectTimeout", "ReadTimeout") or "connection" in low:
        return "Não consegui falar com a SerpApi. Verifique sua internet e tente de novo."
    if name == "HTTPError":
        return f"A SerpApi respondeu com erro ({msg[:80]}). Tente de novo em instantes."
    return msg or name


class App:
    def __init__(self, provider: Provider, store: Store, cfg: Config, ui: Prompter,
                 console: Console | None = None, open_url=webbrowser.open, tmpdir: str | None = None,
                 env_path: Path | None = None, telegram_factory=TelegramApi, sleep=time.sleep,
                 provider_factory=make_provider):
        self.provider, self.store, self.cfg, self.ui = provider, store, cfg, ui
        self.out = console or Console()
        self.open_url = open_url
        self.env_path = env_path or Path(__file__).resolve().parent.parent / ".env"
        self.telegram_factory, self.sleep = telegram_factory, sleep
        self.provider_factory = provider_factory
        self.promos_session = None
        self._cur = cfg.currency
        self.tmpdir = Path(tmpdir or tempfile.gettempdir()) / "flight_alerts"
        self._sellers_cache: dict[tuple[str, str], list[Seller]] = {}

    # ---- cabeçalho e utilidades de saída ----------------------------------
    def m(self, x) -> str:
        return money(x, self._cur)

    def remaining(self) -> int | None:
        if not getattr(self.provider, "metered", True):
            return None
        return self.cfg.monthly_quota - self.store.searches_this_month()

    def status_text(self) -> Text:
        rem = self.remaining()
        if rem is None:
            if self.provider.name == "mock":
                return Text("modo teste · preços falsos, nada é gasto", style="yellow")
            return Text.assemble((f"{self.provider.label.split(' (')[0]}  ", DIM), ("sem limite de buscas", ACCENT))
        total = self.cfg.monthly_quota
        return Text.assemble(
            (f"{self.provider.label.split(' (')[0]}  ", DIM), (bar(rem, total), ACCENT), (f"  {rem}/{total} buscas restantes este mês", DIM)
        )

    def banner(self) -> None:
        title = Text.assemble(("✻ ", f"bold {ACCENT}"), ("Caçador de passagens", "bold"))
        sub = Text("Ache o voo mais barato e descubra onde comprar.", style=DIM)
        self.out.print()
        self.out.print(Panel(Group(title, sub, Text(), self.status_text()),
                             box=box.ROUNDED, border_style=ACCENT, padding=(1, 3), expand=False))

    def done(self, label: str, value: str, extra: str = "") -> None:
        self.out.print(Text.assemble((f"  ✓ {label:<8}", DIM), (value, "bold"), (f"  {extra}" if extra else "", DIM)))

    def info(self, msg: str, style: str = DIM) -> None:
        self.out.print(Text(msg, style=style))

    def spinner(self, msg: str):
        return self.out.status(msg, spinner="dots", spinner_style=ACCENT)

    # ---- perguntas padronizadas ---------------------------------------------
    def ask_place(self, question: str, label: str) -> str:
        codes = self.ui.place(question)
        name = place_label(codes)
        self.done(label, name, short_place(codes) if name != short_place(codes) else "")
        return codes

    def ask_date(self, question: str, label: str, min_date: date | None = None) -> date:
        d = self.ui.date(question, min_date=min_date)
        self.done(label, fmt_date(d))
        return d

    def ask_trip(self, depart: date) -> date | None:
        kind = self.ui.select("Tipo de viagem", [
            Choice("Só ida", "oneway"), Choice("Ida e volta", "round"),
        ])
        if kind == "oneway":
            self.done("Volta", "só ida")
            return None
        ret = self.ui.date("Quando você volta?", min_date=depart)
        self.done("Volta", fmt_date(ret))
        return ret

    def ask_int(self, question: str, default: int, lo: int, hi: int, hint: str = "") -> int:
        def check(t: str) -> str | None:
            return None if t.isdigit() and lo <= int(t) <= hi else f"digite um número entre {lo} e {hi}"

        return int(self.ui.text(question, str(default), check, hint))

    # ---- cota ----------------------------------------------------------------
    def can_spend(self, n: int) -> bool:
        rem = self.remaining()
        if rem is not None and n > rem:
            self.info(f"Isso usa {n} busca(s) e só restam {rem} na cota deste mês.", BAD)
            return False
        return True

    def _count(self) -> None:
        if getattr(self.provider, "metered", True):
            self.store.count_search()

    def do_search(self, q: Query) -> list[Offer]:
        offers = self.provider.search(q)
        self._count()
        if offers:
            best = offers[0]
            self._cur = best.currency or self._cur
            self.store.save_price(f"{q.origin}-{q.destination}", q.key, best.price,
                                  best.currency, best.airline, best.stops)
        return offers

    # ---- loop principal ---------------------------------------------------------
    def run(self) -> None:
        self.banner()
        screens = {"search": self.screen_search, "flex": self.screen_flex,
                   "alerts": self.screen_alerts, "history": self.screen_history,
                   "telegram": self.screen_telegram, "dest": self.screen_destinations,
                   "promos": self.screen_promos,
                   "source": self.screen_source}
        while True:
            n = len(self.store.list_watches())
            tg = ("conectado" if all(self.telegram_creds())
                  else "receba os alertas no celular")
            try:
                choice = self.ui.select("O que você quer fazer?", [
                    Choice("Pesquisar voo", "search", "preços e onde comprar"),
                    Choice("Achar o dia mais barato", "flex", "varre uma janela de datas"),
                    Choice("Meus alertas", "alerts", f"{n} ativo(s)" if n else "nenhum ainda"),
                    *([Choice("Destinos baratos", "dest", "para onde for mais barato")]
                      if getattr(self.provider, "supports_destinations", False) else []),
                    Choice("Histórico de preços", "history", "como o preço variou"),
                    Choice("Telegram", "telegram", tg),
                    Choice("Promoções do Passagens Imperdíveis", "promos", "ofertas novas do site"),
                    Choice("Fonte de preços", "source", self.provider.label.split(" (")[0]),
                    Choice("Sair", BACK),
                ])
            except Back:
                self.info("\nAté a próxima. Bons voos!")
                return
            try:
                screens[choice]()
            except Back:
                pass
            except KeyboardInterrupt:
                self.info("\ncancelado")
            except Exception as e:  # erro de rede/API não derruba o programa
                self.out.print(Panel(Text(friendly_error(e), style=BAD), title="Algo deu errado",
                                     border_style=BAD, box=box.ROUNDED, expand=False))
            self.out.print()
            self.out.print(self.status_text())

    # ---- 1) pesquisa simples ------------------------------------------------------
    def screen_search(self) -> None:
        self.out.print()
        origin = self.ask_place("De onde você sai?", "Origem")
        dest = self.ask_place("Para onde vai?", "Destino")
        self.search_flow(origin, dest)

    def search_flow(self, origin: str, dest: str) -> None:
        depart = self.ask_date("Quando você vai?", "Ida")
        ret = self.ask_trip(depart)
        if not self.can_spend(1):
            return
        q = Query(origin, dest, depart, ret)
        with self.spinner("Buscando voos…"):
            offers = self.do_search(q)
        if not offers:
            self.info("Não encontrei voos para essa busca. Tente outra data.", "yellow")
            return
        self.show_offers(offers)
        self.offer_actions(offers, q)

    def show_offers(self, offers: list[Offer], limit: int = 8) -> None:
        q = offers[0].query
        when = fmt_date(q.depart) + (f"  →  volta {fmt_date(q.return_date)}" if q.return_date else "  ·  só ida")
        t = Table(box=box.SIMPLE_HEAD, header_style=DIM, pad_edge=False, expand=False,
                  title=Text.assemble((f"{place_label(q.origin)} → {place_label(q.destination)}", "bold")),
                  caption=Text(when, style=DIM), title_justify="left", caption_justify="left")
        t.add_column("", style=DIM, justify="right")
        t.add_column("Preço", justify="right")
        for col in ("Companhia", "Escalas", "Horário", "Duração"):
            t.add_column(col)
        for i, o in enumerate(offers[:limit], 1):
            first = i == 1
            t.add_row(
                str(i), Text(self.m(o.price), style=f"bold {GOOD}" if first else "bold"),
                Text.assemble((o.airline or "?", ""), (" ⚠ cidade escondida" if o.hidden_city else "", "yellow")),
                stops_label(o.stops), f"{hhmm(o.depart_time)} → {hhmm(o.arrive_time)}", dur(o.duration_min),
            )
        self.out.print()
        self.out.print(t)
        if any(o.hidden_city for o in offers[:limit]):
            self.info("⚠ Cidade escondida: você desce na conexão e não pega o último trecho. Só ida, só bagagem "
                      "de mão, e a companhia proíbe isso (pode cancelar a volta e o milhas).", "yellow")
        ins = getattr(self.provider, "insights", None)
        if ins and ins.get("typical_price_range"):
            lo, hi = ins["typical_price_range"][:2]
            level = ins.get("price_level", "")
            word, color = {"low": ("baixo", GOOD), "typical": ("normal", "yellow"), "high": ("alto", BAD)}.get(level, (level, DIM))
            self.out.print(Text.assemble(("Google: ", DIM), (f"preço {word}", color),
                                         (f" para essa rota (faixa comum {self.m(lo)} a {self.m(hi)})", DIM)))
        self.out.print()

    def offer_actions(self, offers: list[Offer], q: Query) -> None:
        best = offers[0]
        while True:
            try:
                act = self.ui.select("O que fazer agora?", [
                    Choice("Ver onde comprar a mais barata", "buy", f"{self.m(best.price)} · {best.airline or '?'}"),
                    Choice("Escolher outra opção", "pick", f"entre as {min(len(offers), 8)} da lista"),
                    Choice("Criar alerta de preço", "alert", "avisa quando baixar"),
                    Choice(f"Abrir no {self.site_name()}", "gf"),
                    Choice("Voltar ao menu", BACK),
                ])
            except Back:
                return
            try:
                if act == "buy":
                    self.show_sellers(best)
                elif act == "pick":
                    self.pick_and_show(offers[:8])
                elif act == "alert":
                    nights = (q.return_date - q.depart).days if q.return_date else 0
                    self.create_alert(q.origin, q.destination, q.depart, q.depart, 7,
                                      [nights] if nights else [], best.price)
                else:
                    self.open_offer(best)
            except Back:
                continue

    def pick_and_show(self, offers: list[Offer]) -> None:
        items = [
            Choice(f"{self.m(o.price):>9}  {o.airline or '?'}", i,
                   f"{stops_label(o.stops)} · {hhmm(o.depart_time)} → {hhmm(o.arrive_time)}")
            for i, o in enumerate(offers)
        ] + [Choice("Voltar", BACK)]
        idx = self.ui.select("Qual opção?", items)
        self.show_sellers(offers[idx])

    # ---- onde comprar ------------------------------------------------------------------
    def site_name(self) -> str:
        return "Skiplagged" if self.provider.name == "skiplagged" else "Google Flights"

    def fallback_url(self, q: Query) -> str:
        fn = getattr(self.provider, "fallback_url", None)
        return fn(q) if fn else GF_URL

    def open_offer(self, offer: Offer) -> None:
        """Abre a página de compra da oferta (fontes sem lista de vendedores)."""
        if offer.hidden_city and not self.ui.confirm(
                "Cidade escondida: só ida, só bagagem de mão, e a companhia proíbe. Abrir mesmo assim?", False):
            return
        url = offer.link or self.fallback_url(offer.query)
        self.open_url(url)
        self.info(f"Abri a página de compra ({self.site_name()}). Confira o preço final antes de pagar.")

    def resolve(self, offer: Offer) -> Offer | None:
        """Oferta de calendário (só preço do dia) -> busca a data para achar o voo de verdade."""
        if not offer.partial:
            return offer
        if not self.can_spend(1):
            return None
        with self.spinner("Buscando o voo desse dia…"):
            offers = self.do_search(offer.query)
        if not offers:
            self.info("Não achei voo para esse dia agora. Tente outra data.", "yellow")
            return None
        return offers[0]

    def get_sellers(self, offer: Offer) -> list[Seller] | None:
        key = (offer.query.key, offer.booking_token)
        if key in self._sellers_cache:
            return self._sellers_cache[key]
        if not self.can_spend(1):
            return None
        with self.spinner("Procurando onde comprar…"):
            sellers = self.provider.sellers(offer)
            self._count()
        self._sellers_cache[key] = sellers
        return sellers

    def show_sellers(self, offer: Offer) -> None:
        offer = self.resolve(offer)
        if offer is None:
            return
        if not offer.booking_token:
            self.open_offer(offer)
            return
        sellers = self.get_sellers(offer)
        if sellers is None:
            return
        if not sellers:
            self.info("Nenhum vendedor listado para essa opção. Use o Google Flights.", "yellow")
            return
        width = max(len(s.name) for s in sellers)
        cheapest = min((s.price for s in sellers if s.price is not None), default=None)
        while True:
            items = []
            for i, s in enumerate(sellers):
                if s.price is not None and cheapest is not None:
                    diff = s.price - cheapest
                    hint = f"{self.m(s.price)}" + ("  · menor preço" if diff == 0 else f"  · +{self.m(diff)}")
                else:
                    hint = ""
                items.append(Choice(s.name.ljust(width), i, hint))
            items.append(Choice("Voltar", BACK))
            try:
                idx = self.ui.select(f"Onde comprar · {self.m(offer.price)} {offer.airline or ''}".rstrip(), items)
            except Back:
                return
            self.open_seller(sellers[idx], offer)

    def open_seller(self, s: Seller, offer: Offer) -> None:
        """A SerpApi devolve um POST para o checkout; monta uma página local que o envia."""
        fields = parse_qsl(s.post_data) if s.post_data else []
        if s.url and fields:
            self.tmpdir.mkdir(parents=True, exist_ok=True)
            inputs = "".join(
                f'<input type="hidden" name="{html.escape(k)}" value="{html.escape(v)}">' for k, v in fields
            )
            page = (
                "<!doctype html><meta charset=utf-8><title>Abrindo...</title>"
                f'<body onload="document.f.submit()"><p>Abrindo {html.escape(s.name)}...</p>'
                f'<form name=f method=post action="{html.escape(s.url)}">{inputs}'
                "<noscript><button>Continuar</button></noscript></form>"
            )
            path = self.tmpdir / f"abrir_{abs(hash((s.name, s.url))) % 10**8}.html"
            path.write_text(page, encoding="utf-8")
            self.open_url(path.as_uri())
        elif s.url:
            self.open_url(s.url)
        else:
            self.info(f"{s.name} não tem link direto; abri o Google Flights.", "yellow")
            self.open_url(offer.link or GF_URL)
            return
        self.info(f"Abri {s.name} no navegador. Confira o preço final antes de pagar.")

    # ---- 2) dia mais barato ------------------------------------------------------------------
    def screen_flex(self) -> None:
        self.out.print()
        origin = self.ask_place("De onde você sai?", "Origem")
        dest = self.ask_place("Para onde vai?", "Destino")
        start = self.ask_date("A partir de que dia você pode ir?", "De")
        end = self.ask_date("E até que dia?", "Até", min_date=start)
        span = (end - start).days
        kind = self.ui.select("Tipo de viagem", [Choice("Só ida", "oneway"), Choice("Ida e volta", "round")])
        trip = self.ask_int("Quantas noites de viagem?", 7, 1, 90, "ex.: 7") if kind == "round" else 0
        self.done("Viagem", f"{trip} noites" if trip else "só ida")
        if getattr(self.provider, "supports_calendar", False) and not trip:
            return self.flex_calendar(origin, dest, start, end)
        steps = [s for s in (1, 2, 3, 5, 7, 14) if s == 1 or s <= span]
        step = self.ui.select("De quantos em quantos dias testar?", [
            Choice(f"A cada {s} dia(s)" if s > 1 else "Todos os dias", s, f"{span // s + 1} busca(s)") for s in steps
        ])
        self.done("Passo", f"a cada {step} dia(s)" if step > 1 else "todos os dias")

        queries: list[Query] = []
        d = start
        while d <= end:
            queries.append(Query(origin, dest, d, d + timedelta(days=trip) if trip else None))
            d += timedelta(days=step)
        if len(queries) > MAX_FLEX_SEARCHES:
            self.info(f"{len(queries)} buscas é demais de uma vez (máx. {MAX_FLEX_SEARCHES}). "
                      "Escolha um passo maior ou uma janela menor.", BAD)
            return
        if not self.can_spend(len(queries)):
            return
        rem = self.remaining()
        extra = "" if rem is None else f" Restam {rem} na cota."
        if not self.ui.confirm(f"Vou fazer {len(queries)} busca(s).{extra} Continuar?", True):
            return

        results: list[tuple[Query, Offer]] = []
        with self.spinner("Buscando datas…") as status:
            for i, q in enumerate(queries, 1):
                status.update(f"Buscando {fmt_date(q.depart, False)}  ({i}/{len(queries)})")
                try:
                    offers = self.do_search(q)
                except Exception as e:
                    self.info(f"{fmt_date(q.depart, False)}: {friendly_error(e)}", BAD)
                    continue
                if offers:
                    results.append((q, offers[0]))
        if not results:
            self.info("Nenhum resultado para essa janela.", "yellow")
            return
        self.show_flex(results, origin, dest)
        self.flex_actions(results, origin, dest, start, end, step, trip)

    def flex_calendar(self, origin: str, dest: str, start: date, end: date) -> None:
        """Uma chamada devolve o menor preço de cada dia (só ida)."""
        if (end - start).days > 60:
            self.info("O calendário cobre até 60 dias por vez. Escolha uma janela menor.", BAD)
            return
        with self.spinner("Lendo o calendário de preços…"):
            offers = self.provider.calendar(origin, dest, start, end)
        if not offers:
            self.info("O calendário não trouxe preços para essa janela.", "yellow")
            return
        self._cur = offers[0].currency or self._cur
        for o in offers:
            self.store.save_price(f"{origin}-{dest}", o.query.key, o.price, o.currency, o.airline, o.stops)
        results = [(o.query, o) for o in offers]
        self.show_flex(results, origin, dest)
        self.info("Preços de calendário são o 'a partir de' do dia; ao escolher um dia eu busco o voo exato.")
        self.flex_actions(results, origin, dest, start, end, 1, 0)

    def show_flex(self, results: list[tuple[Query, Offer]], origin: str, dest: str) -> None:
        prices = [o.price for _, o in results]
        lo, hi = min(prices), max(prices)
        t = Table(box=box.SIMPLE_HEAD, header_style=DIM, pad_edge=False, expand=False,
                  title=Text(f"{place_label(origin)} → {place_label(dest)}", style="bold"), title_justify="left")
        t.add_column("Ida")
        t.add_column("Volta")
        t.add_column("Preço", justify="right")
        t.add_column("Companhia")
        t.add_column("Escalas")
        t.add_column("")
        for q, o in sorted(results, key=lambda r: r[0].depart):
            best = o.price == lo
            width = 1 if hi == lo else 1 + int((o.price - lo) / (hi - lo) * 17)
            color = GOOD if best else (BAD if o.price == hi and hi != lo else ACCENT)
            t.add_row(
                Text(fmt_date(q.depart, False), style="bold" if best else ""),
                fmt_date(q.return_date, False) if q.return_date else "—",
                Text(self.m(o.price), style=f"bold {GOOD}" if best else "bold"),
                o.airline or "?", stops_label(o.stops),
                Text("█" * width + ("  menor preço" if best else ""), style=color),
            )
        self.out.print()
        self.out.print(t)
        self.info(f"Mediana da janela: {self.m(statistics.median(prices))}  ·  "
                  f"economia do mais barato vs. mais caro: {self.m(hi - lo)}")
        self.out.print()

    def flex_actions(self, results, origin, dest, start, end, step, trip) -> None:
        cheapest_q, cheapest_o = min(results, key=lambda r: r[1].price)
        by_date = sorted(results, key=lambda r: r[0].depart)
        while True:
            try:
                act = self.ui.select("O que fazer agora?", [
                    Choice("Ver onde comprar o mais barato", "buy",
                           f"{fmt_date(cheapest_q.depart, False)} · {self.m(cheapest_o.price)}"),
                    Choice("Escolher outra data", "pick"),
                    Choice("Criar alerta para essa janela", "alert", "avisa quando algum dia baixar"),
                    Choice("Voltar ao menu", BACK),
                ])
            except Back:
                return
            try:
                if act == "buy":
                    self.show_sellers(cheapest_o)
                elif act == "pick":
                    idx = self.ui.select("Qual data?", [
                        Choice(fmt_date(q.depart), i, f"{self.m(o.price)} · {o.airline or '?'}")
                        for i, (q, o) in enumerate(by_date)
                    ] + [Choice("Voltar", BACK)])
                    self.show_sellers(by_date[idx][1])
                else:
                    self.create_alert(origin, dest, start, end, step, [trip] if trip else [], cheapest_o.price)
            except Back:
                continue

    # ---- destinos baratos ---------------------------------------------------------------------
    def screen_destinations(self) -> None:
        self.out.print()
        origin = self.ask_place("De onde você sai?", "Origem")
        start = self.ask_date("A partir de que dia você pode ir?", "De")
        end = self.ask_date("E até que dia?", "Até", min_date=start)
        with self.spinner("Procurando destinos baratos…"):
            places = self.provider.destinations(origin, start, end)
        if not places:
            self.info("Não encontrei destinos para essa janela.", "yellow")
            return
        self._cur = places[0].currency or self._cur
        top = places[:15]
        t = Table(box=box.SIMPLE_HEAD, header_style=DIM, pad_edge=False, expand=False,
                  title=Text(f"Saindo de {place_label(origin)}", style="bold"), title_justify="left")
        t.add_column("", style=DIM, justify="right")
        t.add_column("Destino")
        t.add_column("A partir de", justify="right")
        for i, p in enumerate(top, 1):
            t.add_row(str(i), f"{p.name} ({p.code})" if p.code else p.name,
                      Text(self.m(p.price), style=f"bold {GOOD}" if i == 1 else "bold"))
        self.out.print(t)
        self.info("Preço 'a partir de': confirme ao pesquisar o voo.")
        idx = self.ui.select("Pesquisar voos para qual destino?", [
            Choice(p.name, i, self.m(p.price)) for i, p in enumerate(top)] + [Choice("Voltar", BACK)])
        p = top[idx]
        self.done("Destino", p.name, p.code)
        self.search_flow(origin, p.code or p.name)

    # ---- promoções (Passagens Imperdíveis) --------------------------------------------------
    def screen_promos(self) -> None:
        from . import deals as dl

        self.out.print()
        with self.spinner("Lendo as promoções do site…"):
            try:
                deals = dl.fetch(self.promos_session)
            except dl.DealsError as exc:
                self.info(f"Não consegui ler as promoções: {exc}.", BAD)
                self.info("Abra https://passagensimperdiveis.com.br no navegador.", DIM)
                return
        if not deals:
            self.info("Não achei promoções na página.", "yellow")
            return
        term = self.ui.text("Filtrar por cidade ou palavra (enter = todas):", "", lambda t: None, "ex.: Salvador")
        keys = [k.strip() for k in term.split(",") if k.strip()]
        shown = [d for d in deals if dl.matches(d, keys)][:15]
        if not shown:
            self.info("Nenhuma promoção recente com esse termo.", "yellow")
            return
        new = {d.link for d in deals if not self.store.deal_seen(d.link)}
        for d in deals:
            self.store.save_deal(d.link, d.title, d.price, d.published)
        t = Table(box=box.SIMPLE_HEAD, header_style=DIM, pad_edge=False, expand=False,
                  title=Text("Passagens Imperdíveis", style="bold"), title_justify="left")
        t.add_column("", style=DIM, justify="right")
        t.add_column("Promoção")
        t.add_column("Preço", justify="right")
        t.add_column("")
        for i, d in enumerate(shown, 1):
            t.add_row(str(i), d.title[:80], Text(f"R$ {d.price:,.0f}".replace(",", ".") if d.price else "—",
                                                 style=f"bold {GOOD}" if d.price else DIM),
                      Text("novo" if d.link in new else "", style=ACCENT))
        self.out.print(t)
        while True:
            try:
                idx = self.ui.select("Abrir qual promoção?", [
                    Choice(d.title[:60], i, f"R$ {d.price:,.0f}".replace(",", ".") if d.price else "")
                    for i, d in enumerate(shown)] + [Choice("Voltar", BACK)])
            except Back:
                return
            self.open_url(shown[idx].link)
            self.info("Abri a promoção no navegador. Confira datas e preço final antes de comprar.")

    # ---- fonte de preços ----------------------------------------------------------------------
    def screen_source(self) -> None:
        self.out.print()
        items = [Choice(label, key, "← em uso" if key == self.provider.name else "")
                 for key, label in SOURCES.items()] + [Choice("Voltar", BACK)]
        key = self.ui.select("De onde vêm os preços?", items)
        if key == self.provider.name:
            return
        if key == "serpapi" and not os.environ.get("SERPAPI_KEY"):
            val = self.ui.text("Cole sua chave da SerpApi (serpapi.com):", "",
                               lambda t: None if len(t.strip()) >= 20 else "a chave tem 64 caracteres", "")
            write_key(self.env_path, "SERPAPI_KEY", val.strip())
            os.environ["SERPAPI_KEY"] = val.strip()
        self.provider = self.provider_factory(key, self.cfg.currency)
        self._sellers_cache.clear()
        self._cur = self.cfg.currency
        if key != "mock":
            write_key(self.env_path, "FLIGHT_SOURCE", key)
            os.environ["FLIGHT_SOURCE"] = key
        self.done("Fonte", self.provider.label)
        self.out.print()
        self.out.print(self.status_text())

    # ---- 3) alertas ---------------------------------------------------------------------------
    def create_alert(self, origin, dest, d_from: date, d_to: date, step: int,
                     trip_days: list[int], best_price: float) -> None:
        suggest = int(best_price * 0.95 // 10 * 10) or int(best_price)
        self.out.print()
        sym = SYMBOLS.get(self._cur, self._cur)
        teto = self.ask_int(f"Avisar quando o preço ficar até ({sym}):", suggest, 1, 1_000_000,
                            f"hoje o melhor é {self.m(best_price)}")
        stops = self.ui.select("Aceita escalas?", [
            Choice("Qualquer", "any"), Choice("Só voos diretos", "direct"), Choice("No máximo 1 escala", "one"),
        ])
        max_stops = {"any": None, "direct": 0, "one": 1}[stops]
        hidden = False
        if getattr(self.provider, "supports_hidden", False) and not trip_days:
            hidden = self.ui.select("Aceita 'cidade escondida'?", [
                Choice("Não, só voos normais", False),
                Choice("Sim, pode ser mais barato", True, "só ida, só bagagem de mão; a companhia proíbe"),
            ])
        wid = self.store.add_watch(origin, dest, d_from, d_to, step, trip_days, float(teto), max_stops,
                                   allow_hidden=hidden)
        n = 0
        d = d_from
        while d <= d_to:
            n += 1
            d += timedelta(days=step)
        n *= max(len(trip_days), 1)
        if hidden:
            self.info("Esse alerta inclui cidade escondida.", "yellow")
        body = Text.assemble(
            ("Alerta criado  ", f"bold {GOOD}"), (f"#{wid}\n", DIM),
            (f"{place_label(origin)} → {place_label(dest)} · até {self.m(teto)} · {n} data(s)\n\n", ""),
            ("Os alertas disparam quando o monitor roda:  ", DIM), ("python3 run.py monitor", "bold"),
        )
        self.out.print(Panel(body, box=box.ROUNDED, border_style=GOOD, expand=False, padding=(0, 2)))

    def screen_alerts(self) -> None:
        while True:
            watches = self.store.list_watches()
            self.out.print()
            for r in self.cfg.routes:
                self.info(f"config.yaml · {place_label(r.origin)} → {place_label(r.destination)} · "
                          f"{r.depart_from:%d/%m} a {r.depart_to:%d/%m}")
            if not watches:
                self.info("Você ainda não criou alertas por aqui. Faça uma pesquisa e escolha 'Criar alerta de preço'.")
                return
            items = [
                Choice(f"#{w['id']}  {place_label(w['origin'])} → {place_label(w['destination'])}", w["id"],
                       f"até {self.m(w['max_price'])} · {w['depart_from'][5:].replace('-', '/')}"
                       + (f" a {w['depart_to'][5:].replace('-', '/')}" if w["depart_to"] != w["depart_from"] else "")
                       + (f" · {w['trip_days']} noites" if w["trip_days"] else " · só ida"))
                for w in watches
            ] + [Choice("Voltar", BACK)]
            wid = self.ui.select("Seus alertas (escolha um para remover)", items)
            if self.ui.confirm(f"Remover o alerta #{wid}?", False):
                self.store.delete_watch(int(wid))
                self.info(f"Alerta #{wid} removido.", GOOD)

    # ---- Telegram ---------------------------------------------------------------------------------
    def telegram_creds(self) -> tuple[str, str]:
        return os.environ.get("TELEGRAM_BOT_TOKEN", ""), os.environ.get("TELEGRAM_CHAT_ID", "")

    def screen_telegram(self) -> None:
        token, chat = self.telegram_creds()
        self.out.print()
        if token and chat:
            self.info(f"Telegram conectado  ·  bot {mask(token)}  ·  chat {chat}")
            act = self.ui.select("O que você quer fazer?", [
                Choice("Enviar uma mensagem de teste", "test"),
                Choice("Conectar outro bot ou chat", "redo"),
                Choice("Voltar", BACK),
            ])
            if act == "test":
                self.telegram_test(self.telegram_factory(token), chat)
                return
            token = ""  # 'conectar outro': pede um token novo
        elif token and valid_token_format(token):
            self.info("Achei o token do bot no .env. Falta só descobrir o seu chat.")
        else:
            token = ""
        self.telegram_connect(token)

    def telegram_test(self, api, chat_id: str) -> bool:
        try:
            with self.spinner("Enviando mensagem de teste…"):
                api.send(chat_id, "✻ Caçador de passagens\nTelegram conectado. Os alertas de passagem barata vão chegar aqui.")
        except TelegramError as e:
            self.out.print(Panel(Text(str(e), style=BAD), title="Não consegui enviar", border_style=BAD,
                                 box=box.ROUNDED, expand=False))
            return False
        self.info("Mensagem de teste enviada. Confira no seu Telegram.", GOOD)
        return True

    def telegram_connect(self, token: str = "") -> None:
        steps = Text.assemble(
            ("Conectar o Telegram\n\n", "bold"),
            ("1  ", f"bold {ACCENT}"), ("Abra o Telegram e procure por  ", ""), ("@BotFather\n", "bold"),
            ("2  ", f"bold {ACCENT}"), ("Envie  ", ""), ("/newbot", "bold"),
            ("  e escolha um nome e um usuário (termina em 'bot')\n", ""),
            ("3  ", f"bold {ACCENT}"), ("Copie o token que ele devolve e cole abaixo\n", ""),
        )
        if not token:
            self.out.print(Panel(steps, box=box.ROUNDED, border_style=ACCENT, expand=False, padding=(1, 2)))
        while True:
            if not token:
                token = self.ui.text(
                    "Cole o token do bot:", "",
                    lambda t: None if valid_token_format(t) else
                    "o token tem o formato 123456789:AAH... (copie inteiro do BotFather)",
                    "só você precisa ter esse token")
            api = self.telegram_factory(token)
            try:
                with self.spinner("Conferindo o token…"):
                    bot = api.me()
                break
            except TelegramError as e:
                self.info(f"✗ {e}", BAD)
                token = ""  # pede um token novo em vez de repetir o mesmo
        username = bot.get("username", "seu bot")
        self.done("Bot", f"@{username}")

        self.out.print()
        self.info(f"Agora abra o chat com o bot (t.me/{username}) e envie qualquer mensagem, por exemplo: oi")
        self.open_url(f"https://t.me/{username}")
        chat = None
        with self.spinner("Esperando sua mensagem no Telegram…"):
            for _ in range(45):  # ~90 s
                try:
                    chat = api.latest_chat()
                except TelegramError as e:
                    self.info(f"✗ {e}", BAD)
                    return
                if chat:
                    break
                self.sleep(2)
        if not chat:
            self.info("Não recebi nenhuma mensagem. Mande um 'oi' para o bot e tente de novo.", "yellow")
            return
        self.done("Chat", chat["name"], f"id {chat['id']}")

        write_key(self.env_path, "TELEGRAM_BOT_TOKEN", token)
        write_key(self.env_path, "TELEGRAM_CHAT_ID", str(chat["id"]))
        os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"] = token, str(chat["id"])
        if self.telegram_test(api, str(chat["id"])):
            self.out.print(Panel(
                Text.assemble(("Telegram conectado\n", f"bold {GOOD}"),
                              ("Token e chat salvos no .env. Os alertas vão chegar aqui quando o monitor rodar:  ", DIM),
                              ("python3 run.py monitor", "bold")),
                box=box.ROUNDED, border_style=GOOD, expand=False, padding=(0, 2)))

    # ---- 4) histórico ------------------------------------------------------------------------------
    def screen_history(self) -> None:
        routes = self.store.routes_with_history()
        self.out.print()
        if not routes:
            self.info("Ainda não há histórico. Faça uma pesquisa primeiro: cada busca entra aqui.", "yellow")
            return
        items = []
        for rid, n, mn in routes:
            a, b = rid.rsplit("-", 1)
            items.append(Choice(f"{place_label(a)} → {place_label(b)}", rid, f"{n} preço(s) · menor {self.m(mn)}"))
        items.append(Choice("Voltar", BACK))
        rid = self.ui.select("De qual rota?", items)
        series = self.store.route_series(rid, 30)
        prices = [p for _, _, p, _ in series]
        med, last = statistics.median(prices), prices[-1]
        a, b = rid.rsplit("-", 1)
        trend = ""
        if med:
            pct = (1 - last / med) * 100
            trend = (f"  ↓ {pct:.0f}% abaixo da mediana" if pct >= 1 else
                     f"  ↑ {-pct:.0f}% acima da mediana" if pct <= -1 else "  na mediana")
        stats = Text.assemble(
            (f"{place_label(a)} → {place_label(b)}\n", "bold"),
            (f"menor {self.m(min(prices))}  ·  mediana {self.m(med)}  ·  maior {self.m(max(prices))}\n", DIM),
            ("último ", DIM), (self.m(last), "bold"), (trend + "\n\n", GOOD if "↓" in trend else DIM),
            (sparkline(prices), ACCENT), (f"   {len(prices)} coleta(s)", DIM),
        )
        self.out.print(Panel(stats, box=box.ROUNDED, border_style=DIM, expand=False, padding=(1, 2)))
        t = Table(box=box.SIMPLE_HEAD, header_style=DIM, pad_edge=False, title="Últimas coletas", title_justify="left")
        for col in ("Quando (UTC)", "Busca", "Preço", "Companhia"):
            t.add_column(col)
        for seen, key, price, airline in series[-8:]:
            t.add_row(seen.replace("T", " ")[:16], pretty_key(key), self.m(price), airline or "?")
        self.out.print(t)


# --------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description="Pesquisa interativa de passagens")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--mock", action="store_true", help="preços falsos, não gasta cota")
    ap.add_argument("--source", choices=sorted(SOURCES), help="fonte de preços (padrão: skiplagged)")
    ap.add_argument("--plain", action="store_true", help="interface simples (sem setas)")
    args = ap.parse_args()
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    cfg = load(args.config) if os.path.exists(args.config) else Config(routes=[], channels=["console"])
    store = Store(cfg.db_path)
    name = "mock" if args.mock else (args.source or current_source())
    try:
        provider: Provider = make_provider(name, cfg.currency)
    except RuntimeError as e:
        raise SystemExit(str(e))

    interactive = sys.stdin.isatty() and sys.stdout.isatty() and not args.plain
    ui: Prompter = FancyPrompter() if interactive else PlainPrompter()
    try:
        App(provider, store, cfg, ui).run()
    except (KeyboardInterrupt, EOFError, Back):
        print()


if __name__ == "__main__":
    main()
