import io
import json
from datetime import date

import pytest
from rich.console import Console

from flight_alerts.cli import App
from flight_alerts.config import Config
from flight_alerts.db import Store
from flight_alerts.models import Offer, Query
from flight_alerts.providers.skiplagged import (McpClient, McpError, SkiplaggedProvider, build_args,
                                                parse_money, unwrap)
from flight_alerts.rules import RuleConfig, evaluate
from flight_alerts.sources import make_provider, parse_fx
from flight_alerts.ui import PlainPrompter

SEARCH_SCHEMA = {"type": "object", "properties": {
    "origin": {"type": "string"}, "destination": {"type": "string"},
    "departureDate": {"type": "string"}, "returnDate": {"type": "string"},
    "adults": {"type": "integer"}, "currency": {"type": "string"}}}
CAL_SCHEMA = {"properties": {"origin": {"type": "string"}, "destination": {"type": "string"},
                             "departureDate": {"type": "string"}, "returnDate": {"type": "string"}}}

ITINS = {"itineraries": [
    {"price": "$180", "airline": "GOL", "stops": 1, "duration": 190, "departureTime": "2027-02-10T08:30:00",
     "arrivalTime": "2027-02-10T11:40:00", "url": "https://skiplagged.com/book/abc"},
    {"price": 95, "currency": "USD", "carrier": "LATAM", "segments": [{"airline": "LATAM"}, {"airline": "LATAM"}],
     "hiddenCity": True, "bookingUrl": "https://skiplagged.com/book/hidden"},
]}
CAL = {"chart": {"type": "FlexChart", "points": [
    {"departure": "2027-02-10", "price": 200}, {"departure": "2027-02-11", "price": 150},
    {"departure": "2027-03-30", "price": 1}]}}
DEST = {"results": [
    {"type": "DealCard", "destination": {"city": "Salvador", "region": "Brazil", "airport": "SSA"},
     "price": {"amount": 120, "currency": "USD"}, "deepLink": "https://skiplagged.com/flights/GRU/SSA/2027-02-10"},
    {"type": "DealCard", "destination": {"city": "Recife", "region": "Brazil", "airport": "REC"},
     "price": {"amount": 90, "currency": "USD"}, "deepLink": "https://skiplagged.com/flights/GRU/REC/2027-02-10"}]}
REAL_SEARCH = {"searchUrl": "x", "flights": [
    {"type": "FlightCard", "airlines": "LATAM Airlines", "departure": {"airport": "GRU", "dateTime": "2027-02-10T20:20:00-03:00"},
     "arrival": {"airport": "SSA", "dateTime": "2027-02-11T08:40:00-03:00"}, "duration": "12h 20m", "layovers": 1,
     "price": {"amount": 116, "currency": "USD"}, "deepLink": "https://skiplagged.com/flights/GRU/SSA/2027-02-10#trip=A",
     "attributes": ["standard", "one-stop"]},
    {"type": "FlightCard", "airlines": "GOL", "departure": {"airport": "GRU", "dateTime": "2027-02-10T15:40:00-03:00"},
     "arrival": {"airport": "SSA", "dateTime": "2027-02-10T18:00:00-03:00"}, "duration": "2h 20m", "layovers": 0,
     "price": {"amount": 138, "currency": "USD"}, "deepLink": "https://skiplagged.com/flights/GRU/SSA/2027-02-10#trip=B",
     "attributes": ["hidden-city", "nonstop"]}]}


class Resp:
    def __init__(self, status=200, body=None, sse=False, headers=None):
        self.status_code = status
        self.headers = {"Content-Type": "text/event-stream" if sse else "application/json", **(headers or {})}
        self._body = body
        self.text = ("event: message\ndata: " + json.dumps(body) + "\n\n") if sse else json.dumps(body)

    def json(self):
        return self._body


class FakeMcp:
    def __init__(self, sse=False, tools_fail=False):
        self.calls, self.sse, self.tools_fail = [], sse, tools_fail

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append((json, headers))
        m = json.get("method")
        if "id" not in json:
            return Resp(202, None)
        if m == "initialize":
            return Resp(200, {"jsonrpc": "2.0", "id": json["id"], "result": {"protocolVersion": "x"}},
                        self.sse, {"Mcp-Session-Id": "sess1"})
        if m == "tools/list":
            if self.tools_fail:
                return Resp(200, {"jsonrpc": "2.0", "id": json["id"], "error": {"message": "nope"}}, self.sse)
            return Resp(200, {"jsonrpc": "2.0", "id": json["id"], "result": {"tools": [
                {"name": "sk_flights_search", "inputSchema": SEARCH_SCHEMA},
                {"name": "sk_flex_departure_calendar", "inputSchema": CAL_SCHEMA},
                {"name": "sk_destinations_anywhere", "inputSchema": {}}]}}, self.sse)
        name = json["params"]["name"]
        data = {"sk_flights_search": ITINS, "sk_flex_departure_calendar": CAL, "sk_destinations_anywhere": DEST}[name]
        return Resp(200, {"jsonrpc": "2.0", "id": json["id"],
                          "result": {"content": [{"type": "text", "text": "ok " + __import__("json").dumps(data)}]}},
                    self.sse)


def provider(sse=False, **kw):
    fake = FakeMcp(sse=sse, **kw)
    client = McpClient(session=fake, min_interval=0, sleep=lambda s: None)
    return SkiplaggedProvider(client=client, fx={"USD": 5.0}), fake


@pytest.mark.parametrize("sse", [False, True])
def test_busca_json_e_sse(sse):
    p, fake = provider(sse)
    offers = p.search(Query("GRU", "SSA", date(2027, 2, 10)))
    assert [o.price for o in offers] == [475.0, 900.0] or [o.price for o in offers] == sorted(o.price for o in offers)
    assert offers[0].hidden_city and offers[0].currency == "BRL" and offers[0].stops == 1
    assert offers[0].price == 475.0 and offers[0].airline == "LATAM"
    assert not offers[1].hidden_city and offers[1].airline == "GOL" and offers[1].depart_time == "08:30"
    # protocolo: initialize, initialized, tools/list, tools/call com argumentos do schema
    methods = [c[0].get("method") for c in fake.calls]
    assert methods == ["initialize", "notifications/initialized", "tools/list", "tools/call"]
    args = fake.calls[-1][0]["params"]["arguments"]
    assert args["origin"] == "GRU" and args["departureDate"] == "2027-02-10" and args["currency"] == "BRL"
    assert fake.calls[-1][1]["Mcp-Session-Id"] == "sess1"


def test_cheapest_filtra_cidade_escondida():
    p, _ = provider()
    q = Query("GRU", "SSA", date(2027, 2, 10))
    assert p.cheapest(q).hidden_city
    c = p.cheapest(q, allow_hidden=False)
    assert not c.hidden_city and c.price == 900.0


def test_regra_ignora_cidade_escondida():
    o = Offer(Query("A", "B", date(2027, 1, 1)), 50, "BRL", hidden_city=True)
    assert evaluate(o, RuleConfig(max_price=100), None, 0, None, None) is None
    assert evaluate(o, RuleConfig(max_price=100, allow_hidden_city=True), None, 0, None, None) is not None
    assert "cidade escondida" in evaluate(o, RuleConfig(max_price=100, allow_hidden_city=True), None, 0, None, None).render()


def test_calendario_e_destinos_sem_schema():
    p, _ = provider(tools_fail=True)       # tools/list falhou: usa nomes padrão
    cal = p.calendar("GRU", "SSA", date(2027, 2, 1), date(2027, 2, 28))
    assert [(o.query.depart.day, o.price, o.partial) for o in cal] == [(10, 1000.0, True), (11, 750.0, True)] \
        or [(o.query.depart.day, o.price) for o in cal] == [(10, 200), (11, 150)]
    dests = p.destinations("GRU", date(2027, 2, 1), date(2027, 2, 28))
    assert [d.code for d in dests][0] == "REC" and dests[0].price == 450.0 and dests[0].currency == "BRL"


def test_erros():
    class Bad:
        def post(self, *a, **k):
            return Resp(429, {})
    c = McpClient(session=Bad(), min_interval=0, sleep=lambda s: None)
    with pytest.raises(McpError, match="429"):
        c.start()


def test_helpers():
    assert parse_money("$1,234") == (1234.0, "USD")
    assert parse_money("R$ 890,50") == (890.5, "BRL")
    assert parse_money("1.234,56") == (1234.56, None)
    assert unwrap({"content": [{"type": "text", "text": "x {\"a\": 1}"}]}) == {"a": 1}
    assert build_args({}, {"origin": "GRU", "depart": "d"}) == {"origin": "GRU", "departureDate": "d"}
    assert build_args(CAL_SCHEMA, {"origin": "A", "destination": "B", "depart": "d"}) == \
        {"origin": "A", "destination": "B", "departureDate": "d"}
    assert build_args({"properties": {"from": {}, "depart": {}}}, {"origin": "A", "depart": "d"}) == {"from": "A", "depart": "d"}
    assert parse_fx("USD_BRL=5.4; EUR_BRL=6") == {"USD": 5.4, "EUR": 6.0}
    assert make_provider("skiplagged", env={}).name == "skiplagged"
    with pytest.raises(RuntimeError):
        make_provider("serpapi", env={})


# ---- CLI -----------------------------------------------------------------------


def make_app(tmp_path, answers, prov):
    it = iter(answers)
    buf = io.StringIO()
    console = Console(file=buf, width=140, force_terminal=False)
    ui = PlainPrompter(ask=lambda _p: next(it), console=console)
    opened = []
    store = Store(str(tmp_path / "t.db"))
    app = App(prov, store, Config(routes=[], channels=["console"]), ui, console=console,
              open_url=opened.append, tmpdir=str(tmp_path), env_path=tmp_path / ".env")
    return app, store, opened, buf


def fut(days):
    from datetime import timedelta
    return (date.today() + timedelta(days=days)).strftime("%d/%m/%Y")


def test_cli_pesquisa_cidade_escondida_abre_com_confirmacao(tmp_path):
    p, _ = provider()
    # menu: 1 pesquisar; datas; só ida; ver onde comprar (1) -> confirma 'n' -> compra (1) -> confirma 's'
    app, store, opened, buf = make_app(tmp_path, ["1", "gru", "ssa", fut(60), "1",
                                                  "1", "n", "1", "s", "0", "0"], p)
    app.run()
    out = buf.getvalue()
    assert "cidade escondida" in out and "sem limite de buscas" in out
    assert opened == ["https://skiplagged.com/book/hidden"]
    assert store.searches_this_month() == 0          # fonte sem cota
    assert "R$" in out


def test_cli_calendario_destinos_e_alerta_escondido(tmp_path):
    p, fake = provider()
    # flex: só ida -> usa calendário (sem pergunta de passo); depois cria alerta aceitando cidade escondida
    app, store, _, buf = make_app(tmp_path, [
        "2", "gru", "ssa", "10/02/2027", "28/02/2027", "1",
        "3", "", "1", "2",     # alerta: teto padrão, qualquer escala, aceita cidade escondida
        "0", "0"], p)
    app.run()
    assert "Lendo o calendário" in buf.getvalue() or "menor preço" in buf.getvalue()
    w = store.list_watches()[0]
    assert w["allow_hidden"] == 1 and w["step_days"] == 1


def test_cli_destinos(tmp_path):
    p, _ = provider()
    app, _, _, buf = make_app(tmp_path, ["4", "gru", "10/02/2027", "28/02/2027", "0", "0"], p)
    app.run()
    out = buf.getvalue()
    assert "Recife" in out and "Salvador" in out


def test_cli_troca_de_fonte(tmp_path):
    p, _ = provider()
    from flight_alerts.providers import MockProvider
    app, _, _, buf = make_app(tmp_path, ["8", "3", "0"], p)   # menu Fonte de preços -> Teste -> sai
    app.provider_factory = lambda name, cur: MockProvider()
    app.run()
    assert app.provider.name == "mock"


def test_varios_aeroportos_e_url_reserva():
    p, fake = provider()
    offers = p.search(Query("GRU,CGH", "SSA", date(2027, 2, 10)))
    calls = [c for c in fake.calls if c[0].get("method") == "tools/call"]
    assert len(calls) == 2 and {c[0]["params"]["arguments"]["origin"] for c in calls} == {"GRU", "CGH"}
    assert p.fallback_url(Query("GRU,CGH", "SSA", date(2027, 2, 10), date(2027, 2, 17))) == \
        "https://skiplagged.com/flights/GRU/SSA/2027-02-10/2027-02-17"


def test_formato_real_do_servidor():
    p, _ = provider()
    offers = p.parse_offers(REAL_SEARCH, Query("GRU", "SSA", date(2027, 2, 10)))
    a, b = sorted(offers, key=lambda o: o.price)
    assert (a.price, a.currency, a.airline, a.stops, a.duration_min) == (580.0, "BRL", "LATAM Airlines", 1, 740)
    assert a.depart_time == "20:20" and a.arrive_time == "08:40" and not a.hidden_city and a.link.endswith("#trip=A")
    assert b.hidden_city and b.stops == 0


def test_calendario_forma_real_filtra_janela():
    p, fake = provider()
    cal = p.calendar("GRU", "SSA", date(2027, 2, 1), date(2027, 2, 28))
    assert [(o.query.depart.day, o.price) for o in cal] == [(10, 1000.0), (11, 750.0)]
    call = [c for c in fake.calls if c[0].get("method") == "tools/call"][0][0]["params"]["arguments"]
    assert call["departureDate"] == "2027-02-14"      # centro da janela
    dests = p.destinations("GRU", date(2027, 2, 1), date(2027, 2, 28))
    assert [(d.name, d.code) for d in dests] == [("Recife", "REC"), ("Salvador", "SSA")] and dests[0].link
