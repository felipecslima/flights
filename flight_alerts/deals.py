"""Promoções do Passagens Imperdíveis, lidas dos cartões da página pública (/promocoes-recentes/ e início).

O site não tem feed RSS (confirmado: /feed/ e similares dão 404). Os cartões de promoção já vêm
no HTML da página, então basta ler esse HTML: uma consulta por página, com identificação clara,
respeitando o robots.txt, e sem entrar em cada promoção. Roda só no seu computador.
Se o layout do site mudar, rode scripts/probe_promos.py e me mande o resultado.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass

import requests

BASE = "https://passagensimperdiveis.com.br"
PAGES = ["/promocoes-recentes/", "/"]
UA = "flight-alerts/1.0 (leitor pessoal; 1 consulta por execução)"
PRICE_RE = re.compile(r"R\$\s?(\d{1,3}(?:\.\d{3})*(?:,\d{2})?|\d+(?:,\d{2})?)")
CARD_RE = re.compile(r'<a href="(/[^"#?]+/?)"[^>]*>\s*<div class="[^"]*cardPublicacao_container_card_(.*?)</a>', re.S)


class DealsError(RuntimeError):
    pass


@dataclass
class Deal:
    title: str
    link: str
    published: str = ""     # não exposto pelo site
    summary: str = ""       # subtítulo do cartão
    price: float | None = None   # "a partir de" do cartão
    kind: str = ""          # Ida e Volta, Só Ida, Múltiplos Destinos...
    note: str = ""          # ex.: "ida e volta, taxas incluídas"

    def line(self) -> str:
        return self.title + (f"  (a partir de R$ {self.price:,.0f})".replace(",", ".") if self.price else "")


def _clean(t: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", t or ""))).strip()


def find_price(text: str) -> float | None:
    vals = []
    for m in PRICE_RE.finditer(text):
        try:
            vals.append(float(m.group(1).replace(".", "").replace(",", ".")))
        except ValueError:
            pass
    vals = [v for v in vals if v >= 50]  # ignora "R$ 5" de taxa etc.
    return min(vals) if vals else None


def _field(body: str, part: str) -> str:
    m = re.search(r'class="[^"]*' + part + r'[^"]*"[^>]*>([^<]*)', body)
    return _clean(m.group(1)) if m else ""


def parse_cards(page_html: str, base: str = BASE) -> list[Deal]:
    out, seen = [], set()
    for href, body in CARD_RE.findall(page_html):
        title = _field(body, "conteudo_titulos_titulo")
        price = find_price(_field(body, "preco_valor"))
        if not title or price is None or not re.search(r"promo|passagens", href):
            continue  # ignora banners/propagandas sem preço de passagem
        link = base + href
        if link in seen:
            continue
        seen.add(link)
        out.append(Deal(title, link, "", _field(body, "conteudo_titulos_subtitulo"), price,
                        _field(body, "tag_label"), _field(body, "preco_posValor")))
    return out


ROW_RE = re.compile(
    r'aeroportos_span_city[^"]*"[^>]*>([^<]+)</span>\s*<span[^>]*aeroportos_span_city[^"]*"[^>]*>(?:<i[^>]*></i>)?([^<]+)</span>'
    r'.*?aeroportos_span_valor[^"]*"[^>]*>([^<]*)', re.S)


def _slug(t: str) -> str:
    import unicodedata
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-")


def parse_routes(page_html: str, page_url: str) -> list[Deal]:
    """Páginas de listas por rota (ex.: finais de semana): 'Origem → Destino, a partir de R$ X'.

    O site só entrega no HTML a primeira página (as mais baratas); o resto vem de uma API que o
    robots.txt proíbe, então não é lida.
    """
    out, seen = [], set()
    for a, b, price_txt in ROW_RE.findall(page_html):
        a, b, price = _clean(a), _clean(b), find_price(price_txt)
        if not a or not b or price is None:
            continue
        link = f"{page_url}#{_slug(a)}-{_slug(b)}-{int(price)}"
        if link in seen:
            continue
        seen.add(link)
        out.append(Deal(f"{a} → {b}", link, "", "Passagem de ida e volta, preço de referência da página", price, "Rota", "a partir de"))
    return out


def _allowed(http, path: str, timeout: int) -> bool:
    """Respeita o robots.txt; se não der para ler, segue (é só uma consulta por página)."""
    from urllib.robotparser import RobotFileParser

    try:
        r = http.get(BASE + "/robots.txt", headers={"User-Agent": UA}, timeout=timeout)
        if r.status_code != 200:
            return True
        rp = RobotFileParser()
        rp.parse(r.text.splitlines())
        return rp.can_fetch(UA, BASE + path)
    except Exception:
        return True


def fetch(session=None, pages: list[str] | None = None, timeout: int = 20) -> list[Deal]:
    http = session or requests
    got: dict[str, Deal] = {}
    errors = []
    for path in pages or PAGES:
        if not _allowed(http, path, timeout):
            errors.append(f"{path}: bloqueado pelo robots.txt")
            continue
        try:
            r = http.get(BASE + path, headers={"User-Agent": UA, "Accept-Language": "pt-BR,pt;q=0.9"}, timeout=timeout)
        except requests.RequestException as exc:
            raise DealsError(f"sem conexão com o site ({exc.__class__.__name__})") from exc
        if r.status_code in (401, 403, 429):
            raise DealsError(f"o site recusou a consulta (HTTP {r.status_code}); não vou insistir")
        if r.status_code >= 400:
            errors.append(f"{path}: HTTP {r.status_code}")
            continue
        for d in parse_cards(r.text) + parse_routes(r.text, BASE + path):
            got.setdefault(d.link, d)
    if not got and errors:
        raise DealsError("; ".join(errors))
    return list(got.values())


def matches(deal: Deal, keywords: list[str], max_price: float | None = None) -> bool:
    low = f"{deal.title} {deal.summary}".lower()
    ok = (not keywords) or any(k.lower() in low for k in keywords)
    if ok and max_price is not None and deal.price is not None:
        ok = deal.price <= max_price
    return ok


def check(store, session=None, keywords: list[str] | None = None, max_price: float | None = None,
          first_run_silent: bool = True, pages: list[str] | None = None) -> tuple[list[Deal], list[Deal]]:
    """Lê as promoções e devolve (todas, novas que casam com o filtro). Marca tudo como visto.

    Na primeira execução (nada visto ainda) não avisa nada, para não inundar você com o arquivo antigo.
    """
    deals = fetch(session, pages=PAGES + [p for p in (pages or []) if p not in PAGES])
    first = not store.deals_seen_any()
    fresh = [d for d in deals if not store.deal_seen(d.link)]
    for d in deals:
        store.save_deal(d.link, d.title, d.price, d.published)
    if first and first_run_silent:
        return deals, []
    return deals, [d for d in fresh if matches(d, keywords or [], max_price)]
