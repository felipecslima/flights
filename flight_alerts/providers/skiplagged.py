"""Skiplagged via servidor MCP público (https://mcp.skiplagged.com/mcp, sem chave).

O protocolo MCP é JSON-RPC sobre HTTP: initialize -> notifications/initialized
-> tools/list -> tools/call. A resposta pode vir como JSON puro ou como SSE.

O formato exato dos resultados não é documentado, então o código é defensivo:
os argumentos são montados a partir do `inputSchema` devolvido por tools/list e
os resultados são lidos por varredura (qualquer dicionário com preço + cara de voo).
Rode `python3 scripts/probe_skiplagged.py` para ver o formato real.
"""
from __future__ import annotations

import json
import re
import time
from datetime import date, timedelta

import requests

from ..models import Offer, Place, Query
from .base import Provider

URL = "https://mcp.skiplagged.com/mcp"
PROTOCOL = "2025-03-26"

T_SEARCH = "sk_flights_search"
T_CALENDAR = "sk_flex_departure_calendar"
T_DEST = "sk_destinations_anywhere"

# nome canônico -> apelidos aceitos no schema da ferramenta
ALIASES = {
    "origin": ["origin", "from", "departure", "departurecity", "departureairport", "source", "fromairport", "fromcity"],
    "destination": ["destination", "to", "arrival", "arrivalcity", "arrivalairport", "dest", "toairport", "tocity"],
    "depart": ["departuredate", "departdate", "outbounddate", "departing", "date", "startdate", "depart", "traveldate", "departure_date"],
    "return": ["returndate", "inbounddate", "returning", "return"],
    "start": ["startdate", "from_date", "datefrom", "earliestdeparture", "start", "mindate", "departuredatestart"],
    "end": ["enddate", "to_date", "dateto", "latestdeparture", "end", "maxdate", "departuredateend"],
    "adults": ["adults", "passengers", "passengercount", "numadults", "travelers", "adultcount"],
    "currency": ["currency", "currencycode"],
    "hidden": ["hiddencity", "includehiddencity", "allowhiddencity", "hiddencityfares", "skiplagging"],
    "sort": ["sort", "sortby", "order"],
}

PRICE_KEYS = ("price", "totalprice", "total", "fare", "cost", "amount", "lowestprice", "minprice", "pricetotal", "farefrom")
CURRENCY_SYMBOLS = {"R$": "BRL", "US$": "USD", "$": "USD", "€": "EUR", "£": "GBP"}


def _norm(k: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(k).lower())


class McpError(RuntimeError):
    pass


class McpClient:
    def __init__(self, url: str = URL, session=None, timeout: int = 40, min_interval: float = 1.0,
                 sleep=time.sleep, clock=time.monotonic):
        self.url, self.timeout = url, timeout
        self.http = session or requests.Session()
        self.session_id: str | None = None
        self.ready = False
        self.tools: dict[str, dict] = {}
        self.min_interval, self._sleep, self._clock, self._last = min_interval, sleep, clock, 0.0
        self._id = 0

    # --- transporte -------------------------------------------------------
    def _headers(self) -> dict:
        h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
             "User-Agent": "flight-alerts/1.0 (uso pessoal)", "MCP-Protocol-Version": PROTOCOL}
        if self.session_id:
            h["Mcp-Session-Id"] = self.session_id
        return h

    @staticmethod
    def _parse_body(resp) -> dict | None:
        text = (getattr(resp, "text", "") or "").strip()
        if not text:
            return None
        ctype = (resp.headers.get("Content-Type", "") if getattr(resp, "headers", None) else "").lower()
        if "event-stream" in ctype or text.startswith(("event:", "data:", ":")):
            last = None
            for block in re.split(r"\r?\n\r?\n", text):
                data = "\n".join(l[5:].strip() for l in block.splitlines() if l.startswith("data:"))
                if not data:
                    continue
                try:
                    msg = json.loads(data)
                except ValueError:
                    continue
                if isinstance(msg, dict) and ("result" in msg or "error" in msg):
                    last = msg
            return last
        try:
            msg = resp.json()
        except ValueError:
            return None
        if isinstance(msg, list):  # batch
            msg = next((m for m in msg if isinstance(m, dict) and ("result" in m or "error" in m)), None)
        return msg

    def _post(self, payload: dict, expect_reply: bool = True) -> dict | None:
        wait = self.min_interval - (self._clock() - self._last)
        if wait > 0:
            self._sleep(wait)
        try:
            resp = self.http.post(self.url, json=payload, headers=self._headers(), timeout=self.timeout)
        except requests.RequestException as exc:
            raise McpError(f"sem conexão com o Skiplagged ({exc.__class__.__name__})") from exc
        finally:
            self._last = self._clock()
        sid = resp.headers.get("Mcp-Session-Id") if getattr(resp, "headers", None) else None
        if sid:
            self.session_id = sid
        if resp.status_code == 429:
            raise McpError("Skiplagged pediu para ir mais devagar (429). Tente de novo em alguns minutos.")
        if resp.status_code >= 400:
            raise McpError(f"Skiplagged respondeu HTTP {resp.status_code}")
        if not expect_reply:
            return None
        msg = self._parse_body(resp)
        if msg is None:
            raise McpError("resposta vazia ou ilegível do Skiplagged")
        if "error" in msg:
            err = msg["error"]
            raise McpError(f"Skiplagged: {err.get('message', err) if isinstance(err, dict) else err}")
        return msg.get("result", {})

    def _rpc(self, method: str, params: dict | None = None):
        self._id += 1
        return self._post({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or {}})

    # --- protocolo ----------------------------------------------------------
    def start(self) -> None:
        if self.ready:
            return
        self._rpc("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                 "clientInfo": {"name": "flight-alerts", "version": "1.0"}})
        try:
            self._post({"jsonrpc": "2.0", "method": "notifications/initialized"}, expect_reply=False)
        except McpError:
            pass
        try:
            res = self._rpc("tools/list") or {}
            self.tools = {t["name"]: t for t in res.get("tools", []) if isinstance(t, dict) and "name" in t}
        except McpError:
            self.tools = {}  # segue sem schema: usa nomes canônicos
        self.ready = True

    def schema(self, tool: str) -> dict:
        return (self.tools.get(tool) or {}).get("inputSchema") or {}

    def call(self, tool: str, args: dict):
        self.start()
        res = self._rpc("tools/call", {"name": tool, "arguments": args}) or {}
        if res.get("isError"):
            raise McpError("Skiplagged: " + (_text_of(res) or "erro na ferramenta")[:200])
        return unwrap(res)


def _text_of(res: dict) -> str:
    return "\n".join(c.get("text", "") for c in res.get("content", []) if isinstance(c, dict) and c.get("type") == "text")


def unwrap(res: dict):
    """Resultado de tools/call -> objeto Python (JSON) ou texto."""
    if res.get("structuredContent") not in (None, {}):
        return res["structuredContent"]
    text = _text_of(res).strip()
    if not text:
        return res
    try:
        return json.loads(text)
    except ValueError:
        pass
    m = re.search(r"(\{.*\}|\[.*\])", text, re.S)  # JSON embutido em texto
    if m:
        try:
            return json.loads(m.group(1))
        except ValueError:
            pass
    return text


# --- montagem de argumentos ----------------------------------------------------


def build_args(schema: dict, values: dict) -> dict:
    """Mapeia valores canônicos para as chaves do schema (por apelido). Sem schema, usa nomes padrão."""
    props = (schema or {}).get("properties") or {}
    if not props:
        defaults = {"origin": "origin", "destination": "destination", "depart": "departureDate",
                    "return": "returnDate", "start": "startDate", "end": "endDate", "adults": "adults",
                    "currency": "currency"}
        return {defaults[k]: v for k, v in values.items() if k in defaults and v not in (None, "")}
    by_norm = {_norm(k): k for k in props}
    out: dict = {}
    for canon, v in values.items():
        if v in (None, ""):
            continue
        for alias in ALIASES.get(canon, [canon]):
            key = by_norm.get(_norm(alias))
            if key and key not in out:
                out[key] = _coerce(props[key], v)
                break
    return out


def _coerce(prop: dict, v):
    t = prop.get("type")
    if t == "array" and not isinstance(v, list):
        return [v]
    if t == "integer":
        return int(v)
    if t == "string" and not isinstance(v, str):
        return str(v)
    return v


# --- leitura de resultados -------------------------------------------------------


def parse_money(v) -> tuple[float | None, str | None]:
    """Número ou texto ('$1,234', 'R$ 890,50', 'USD 120') -> (valor, moeda ou None)."""
    if isinstance(v, bool) or v is None:
        return None, None
    if isinstance(v, (int, float)):
        return float(v), None
    s = str(v).strip()
    cur = None
    for sym in ("R$", "US$", "€", "£", "$"):
        if sym in s:
            cur = CURRENCY_SYMBOLS[sym]
            break
    m = re.search(r"\b(BRL|USD|EUR|GBP)\b", s.upper())
    if m:
        cur = m.group(1)
    num = re.search(r"\d[\d.,]*", s)
    if not num:
        return None, cur
    t = num.group(0).rstrip(".,")
    if "," in t and "." in t:
        t = t.replace(".", "").replace(",", ".") if t.rfind(",") > t.rfind(".") else t.replace(",", "")
    elif "," in t:
        t = t.replace(",", ".") if re.search(r",\d{1,2}$", t) else t.replace(",", "")
    try:
        return float(t), cur
    except ValueError:
        return None, cur


def _walk(obj):
    """Todos os dicionários aninhados (inclui o próprio)."""
    stack = [obj]
    while stack:
        o = stack.pop()
        if isinstance(o, dict):
            yield o
            stack.extend(o.values())
        elif isinstance(o, list):
            stack.extend(o)


def _get(d: dict, names: tuple, default=None):
    low = {_norm(k): v for k, v in d.items()}
    for n in names:
        if _norm(n) in low and low[_norm(n)] not in (None, ""):
            return low[_norm(n)]
    return default


def _price_of(d: dict):
    low = {_norm(k): v for k, v in d.items()}
    for k in PRICE_KEYS:
        if k in low:
            val, cur = parse_money(low[k] if not isinstance(low[k], dict) else _get(low[k], ("amount", "value", "total")))
            if val is not None and val > 0:
                if cur is None and isinstance(low[k], dict):
                    cur = _get(low[k], ("currency",))
                return val, cur or _get(d, ("currency", "currencyCode"))
    return None, None


FLIGHTY = ("airline", "carrier", "segments", "legs", "flights", "flightnumber", "stops", "duration", "airlines", "itinerary")


def _airline(d: dict) -> str:
    a = _get(d, ("airline", "airlineName", "carrier", "carrierName", "airlines", "marketingCarrier"))
    if isinstance(a, list):
        a = ", ".join(str(x.get("name", x) if isinstance(x, dict) else x) for x in a)
    elif isinstance(a, dict):
        a = a.get("name") or a.get("code") or ""
    if not a:
        segs = _get(d, ("segments", "legs", "flights")) or []
        names: list[str] = []
        for s in segs if isinstance(segs, list) else []:
            if isinstance(s, dict):
                n = _airline(s) if not _get(s, ("airline", "carrier")) else str(_get(s, ("airline", "carrier")))
                if n and n not in names:
                    names.append(n)
        a = ", ".join(names)
    return str(a or "")


def _stops(d: dict) -> int:
    s = _get(d, ("stops", "numStops", "stopCount", "numberOfStops", "layovers"))
    if isinstance(s, list):
        return len(s)
    if isinstance(s, (int, float)):
        return int(s)
    if isinstance(s, str) and s.strip().isdigit():
        return int(s)
    if isinstance(s, str) and s.lower() in ("nonstop", "non-stop", "direto"):
        return 0
    segs = _get(d, ("segments", "legs", "flights"))
    if isinstance(segs, list) and segs:
        return max(len(segs) - 1, 0)
    return 0


def _duration(d: dict) -> int:
    v = _get(d, ("durationMinutes", "duration", "totalDuration", "travelTime"))
    if isinstance(v, (int, float)):
        return int(v if v < 3000 else v / 60)  # >3000 provavelmente segundos
    if isinstance(v, str):
        m = re.search(r"(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?", v)
        if m and (m.group(1) or m.group(2)):
            return int(m.group(1) or 0) * 60 + int(m.group(2) or 0)
    return 0


def _is_hidden(d: dict) -> bool:
    for k, v in d.items():
        nk = _norm(k)
        if nk in ("hiddencity", "ishiddencity", "hiddencityfare", "skiplagged", "isskiplagged", "hiddendestination"):
            return bool(v) and str(v).lower() not in ("false", "0", "no")
    blob = json.dumps(d, default=str).lower()
    return "hidden city" in blob or "hidden-city" in blob or "hiddencity\": true" in blob


class SkiplaggedProvider(Provider):
    name = "skiplagged"
    label = "Skiplagged (inclui cidade escondida)"
    metered = False
    supports_calendar = True
    supports_destinations = True
    supports_hidden = True

    def __init__(self, client: McpClient | None = None, currency: str = "BRL", fx: dict | None = None,
                 adults: int = 1):
        self.client = client or McpClient()
        self.currency = currency
        self.fx = {k.upper(): float(v) for k, v in (fx or {}).items()}  # ex.: {"USD": 5.4} = 1 USD em BRL
        self.adults = adults
        self.insights = None
        self.last_raw = None  # última resposta crua (diagnóstico)

    # --- helpers -------------------------------------------------------------
    def _money(self, val: float, cur: str | None) -> tuple[float, str]:
        cur = (cur or "USD").upper()
        if cur != self.currency.upper() and cur in self.fx:
            return round(val * self.fx[cur], 2), self.currency.upper()
        return val, cur

    def _call(self, tool: str, values: dict):
        schema = self.client.schema(tool) if self.client.ready else {}
        if not self.client.ready:
            self.client.start()
            schema = self.client.schema(tool)
        values = {**values, "currency": self.currency}
        args = build_args(schema, values)
        data = self.client.call(tool, args)
        self.last_raw = data
        return data

    # --- busca -----------------------------------------------------------------
    @staticmethod
    def _combos(origin: str, dest: str, cap: int = 6) -> list[tuple[str, str]]:
        os_ = [c.strip() for c in origin.split(",") if c.strip()]
        ds = [c.strip() for c in dest.split(",") if c.strip()]
        return [(a, b) for a in os_ for b in ds][:cap]

    def fallback_url(self, q: Query) -> str:
        a, b = self._combos(q.origin, q.destination)[0]
        url = f"https://skiplagged.com/flights/{a}/{b}/{q.depart.isoformat()}"
        return url + (f"/{q.return_date.isoformat()}" if q.return_date else "")

    def search(self, query: Query) -> list[Offer]:
        offers: list[Offer] = []
        for a, b in self._combos(query.origin, query.destination):
            data = self._call(T_SEARCH, {
                "origin": a, "destination": b,
                "depart": query.depart.isoformat(),
                "return": query.return_date.isoformat() if query.return_date else None,
                "adults": self.adults, "hidden": True,
            })
            offers += self.parse_offers(data, query)
        offers.sort(key=lambda o: o.price)
        return offers

    def parse_offers(self, data, query: Query) -> list[Offer]:
        out: list[Offer] = []
        seen: set = set()
        for d in _walk(data):
            price, cur = _price_of(d)
            if price is None or not any(_norm(k) in FLIGHTY for k in d):
                continue
            # evita contar o mesmo itinerário pai e filho (pai com 'segments' também tem preço)
            link = _get(d, ("bookingUrl", "booking_url", "url", "link", "deeplink", "deepLink", "bookingLink"), "") or ""
            val, cur = self._money(price, cur)
            sig = (round(val, 2), _airline(d), str(_get(d, ("departureTime", "departure", "departTime", "departureDateTime"), "")), link)
            if sig in seen:
                continue
            seen.add(sig)
            out.append(Offer(
                query=query, price=val, currency=cur, airline=_airline(d), stops=_stops(d),
                duration_min=_duration(d) or None, link=str(link) if isinstance(link, str) else "",
                depart_time=_short_time(_get(d, ("departureTime", "departure", "departTime", "departureDateTime", "departsAt"))),
                arrive_time=_short_time(_get(d, ("arrivalTime", "arrival", "arriveTime", "arrivalDateTime", "arrivesAt"))),
                hidden_city=_is_hidden(d),
            ))
        if not out and isinstance(data, str):
            out = self._parse_text(data, query)
        return out

    def _parse_text(self, text: str, query: Query) -> list[Offer]:
        """Plano B: resposta em texto/markdown, uma oferta por linha com preço."""
        out = []
        for line in text.splitlines():
            if not re.search(r"(R\$|US\$|\$|€|£)\s?\d", line):
                continue
            val, cur = parse_money(line)
            if val is None:
                continue
            val, cur = self._money(val, cur)
            link = re.search(r"https?://[^\s)\]]+", line)
            out.append(Offer(query=query, price=val, currency=cur, airline=re.sub(r"[*_`|#>-]+", " ", line).strip()[:60],
                             stops=0, link=link.group(0) if link else "", hidden_city="hidden" in line.lower()))
        return out

    # --- calendário ----------------------------------------------------------------
    def calendar(self, origin: str, destination: str, start: date, end: date) -> list[Offer]:
        best: dict[date, Offer] = {}
        for a, b in self._combos(origin, destination):
            for o in self._calendar_one(a, b, start, end):
                o.query = Query(origin, destination, o.query.depart)
                if o.query.depart not in best or o.price < best[o.query.depart].price:
                    best[o.query.depart] = o
        return [best[k] for k in sorted(best)]

    def _calendar_one(self, origin: str, destination: str, start: date, end: date) -> list[Offer]:
        """O servidor devolve ~±30 dias em torno de `departureDate`; centraliza e junta as janelas."""
        span = (end - start).days
        centers = [start + timedelta(days=span // 2)] if span <= 30 else [
            start + timedelta(days=30 + 60 * i) for i in range(span // 60 + 1)]
        merged: dict[date, Offer] = {}
        for c in centers:
            data = self._call(T_CALENDAR, {"origin": origin, "destination": destination, "depart": c.isoformat(),
                                           "adults": self.adults})
            for o in self._parse_calendar(data, origin, destination, start, end):
                dt = o.query.depart
                if dt not in merged or o.price < merged[dt].price:
                    merged[dt] = o
        return [merged[k] for k in sorted(merged)]

    def _parse_calendar(self, data, origin: str, destination: str, start: date, end: date) -> list[Offer]:
        out: dict[date, Offer] = {}
        for d in _walk(data):
            day = _get(d, ("date", "departure", "departureDate", "departDate", "day"))
            price, cur = _price_of(d)
            if not day or price is None:
                continue
            try:
                dt = date.fromisoformat(str(day)[:10])
            except ValueError:
                continue
            if not (start <= dt <= end):
                continue
            val, cur = self._money(price, cur)
            if dt not in out or val < out[dt].price:
                out[dt] = Offer(Query(origin, destination, dt), val, cur, "", 0, partial=True)
        # formato {"2027-01-17": 123} também é comum
        if not out:
            for d in _walk(data):
                for k, v in d.items():
                    try:
                        dt = date.fromisoformat(str(k)[:10])
                    except ValueError:
                        continue
                    price, cur = parse_money(v if not isinstance(v, dict) else _get(v, ("price", "total", "amount")))
                    if price and start <= dt <= end:
                        val, cur = self._money(price, cur)
                        out[dt] = Offer(Query(origin, destination, dt), val, cur, "", 0, partial=True)
        return [out[k] for k in sorted(out)]

    # --- destinos -------------------------------------------------------------------
    def destinations(self, origin: str, start: date | None = None, end: date | None = None) -> list[Place]:
        start = start or date.today() + timedelta(days=30)
        end = end or start + timedelta(days=30)
        data = self._call(T_DEST, {"origin": origin.split(",")[0], "depart": start.isoformat(),
                                   "start": start.isoformat(), "end": end.isoformat(), "adults": self.adults})
        out: dict[str, Place] = {}
        for d in _walk(data):
            price, cur = _price_of(d)
            if price is None:
                continue
            code = _get(d, ("destination", "iata", "airport", "airportCode", "code", "destinationCode", "to"))
            name = _get(d, ("city", "cityName", "destinationName", "name", "destinationCity"))
            if isinstance(code, dict):  # {"city": "Salvador", "airport": "SSA"}
                name = name or _get(code, ("city", "name"))
                code = _get(code, ("airport", "code", "iata", "id"))
            if isinstance(code, str) and len(code) > 3 and not name:
                name, code = code, ""
            if not (code or name):
                continue
            val, cur = self._money(price, cur)
            key = str(code or name).upper()
            if key not in out or val < out[key].price:
                out[key] = Place(str(name or code), str(code or "").upper()[:3], val, cur,
                                 str(_get(d, ("deepLink", "url", "link", "bookingUrl"), "") or ""))
        return sorted(out.values(), key=lambda p: p.price)


def _short_time(v) -> str:
    """'2027-01-17T08:30:00' -> '08:30'; já curto, devolve igual."""
    if not v:
        return ""
    s = str(v)
    m = re.search(r"(\d{1,2}:\d{2})", s)
    return m.group(1) if m else s[:16]
