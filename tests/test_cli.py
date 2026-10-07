import io
from datetime import date, timedelta

import pytest
from rich.console import Console

from flight_alerts.cli import App, friendly_error, pretty_key
from flight_alerts.config import Config, routes_with_watches
from flight_alerts.db import Store
from flight_alerts.places import (bar, fmt_date, parse_date, place_label,
                                  resolve_place, sparkline)
from flight_alerts.providers import MockProvider
from flight_alerts.ui import BACK, Back, Choice, PlainPrompter


def test_resolve_place():
    assert resolve_place("São Paulo") == "GRU,CGH,VCP"
    assert resolve_place("  RIO ") == "GIG,SDU"
    assert resolve_place("Floripa") == "FLN"
    assert resolve_place("ssa") == "SSA"
    assert resolve_place("gru ssa") == "GRU,SSA"
    assert resolve_place("cidade inexistente") is None
    assert resolve_place("") is None


def test_place_label():
    assert place_label("GRU,CGH,VCP") == "São Paulo"
    assert place_label("SSA") == "Salvador"
    assert place_label("XYZ") == "XYZ"
    assert place_label("AAA,BBB") == "AAA/BBB"


def test_parse_date_and_fmt():
    hoje = date(2026, 10, 6)
    assert parse_date("17/01/2027", hoje) == date(2027, 1, 17)
    assert parse_date("17/01/27", hoje) == date(2027, 1, 17)
    assert parse_date("2027-01-17", hoje) == date(2027, 1, 17)
    assert parse_date("17/01", hoje) == date(2027, 1, 17)   # próxima ocorrência
    assert parse_date("10/10", hoje) == date(2026, 10, 10)
    with pytest.raises(ValueError):
        parse_date("banana", hoje)
    assert fmt_date(date(2027, 1, 17)) == "dom, 17 jan 2027"
    assert fmt_date(date(2027, 1, 17), year=False) == "dom, 17 jan"


def test_sparkline_and_bar():
    assert sparkline([]) == ""
    assert sparkline([5, 5]) == "▁▁"
    s = sparkline([1, 5, 10])
    assert s[0] == "▁" and s[-1] == "█"
    assert bar(5, 10, 10) == "█████░░░░░"
    assert bar(0, 0) == "░" * 10


def test_friendly_error():
    assert "chave" in friendly_error(RuntimeError("SerpApi: Invalid API key. Your API key should be..."))
    assert "cota" in friendly_error(RuntimeError("SerpApi: You have run out of searches."))


# --------------------------------------------------------------------------


def make_plain(answers):
    it = iter(answers)
    buf = io.StringIO()
    console = Console(file=buf, width=140, force_terminal=False)
    return PlainPrompter(ask=lambda _p: next(it), console=console), console, buf


def test_plain_prompter_basico():
    ui, _, _ = make_plain(["banana frita", "salvador", "0"])
    assert ui.place("Destino") == "SSA"            # recusa texto inválido e pergunta de novo
    with pytest.raises(Back):
        ui.select("?", [Choice("a", 1)])             # 0 = voltar
    ui, _, _ = make_plain(["2"])
    assert ui.select("?", [Choice("a", 1), Choice("b", 2)]) == 2
    ui, _, _ = make_plain(["2"])
    with pytest.raises(Back):
        ui.select("?", [Choice("a", 1), Choice("Voltar", BACK)])
    ui, _, _ = make_plain(["", "n", "s"])
    assert ui.confirm("ok?", True) is True
    assert ui.confirm("ok?", True) is False
    assert ui.confirm("ok?", False) is True
    ui, _, _ = make_plain(["ontem", "01/01/2000", "17/01"])
    d = ui.date("quando")
    assert d is not None and d >= date.today()


def make_app(tmp_path, answers, provider=None, quota=250):
    ui, console, buf = make_plain(answers)
    opened: list[str] = []
    store = Store(str(tmp_path / "t.db"))
    cfg = Config(routes=[], channels=["console"], monthly_quota=quota)
    app = App(provider or MockProvider(), store, cfg, ui, console=console,
              open_url=opened.append, tmpdir=str(tmp_path))
    return app, store, opened, buf


class Real(MockProvider):
    name = "serpapi"  # conta cota como a SerpApi
    metered = True


def future(days):
    return (date.today() + timedelta(days=days)).isoformat()


def test_fluxo_pesquisa_vendedores_alerta_historico(tmp_path):
    app, store, opened, buf = make_app(tmp_path, [
        "1", "são paulo", "salvador", future(90), "1",   # pesquisa só ida
        "1", "1", "0",                                   # onde comprar #1 -> abrir vendedor 1 -> voltar
        "3", "", "1",                                    # alerta: teto padrão, qualquer escala
        "0",                                             # sai da tela de resultado
        "3", "0",                                        # lista alertas, volta
        "4", "1",                                        # histórico da rota
        "0",                                             # sair
    ])
    app.run()

    assert opened and opened[0].startswith("file://")      # POST montado em página local
    page = next((tmp_path / "flight_alerts").glob("abrir_*.html")).read_text()
    assert "method=post" in page and 'name="a" value="1"' in page

    w = store.list_watches()
    assert len(w) == 1 and w[0]["origin"] == "GRU,CGH,VCP" and w[0]["max_stops"] is None
    assert w[0]["max_price"] and len(store.routes_with_history()) == 1
    out = buf.getvalue()
    assert "Onde comprar" in out and "Alerta criado" in out and "mediana" in out
    assert "Até a próxima" in out


def test_ida_e_volta_guarda_datas(tmp_path):
    app, store, _, buf = make_app(tmp_path, [
        "1", "rio", "lisboa", future(60), "2", future(70),   # ida e volta
        "0", "0",
    ])
    app.run()
    key = store.route_series("GIG,SDU-LIS")[0][1]
    assert future(60) in key and future(70) in key


def test_datas_flexiveis_e_alerta(tmp_path):
    app, store, _, buf = make_app(tmp_path, [
        "2", "gru", "ssa", future(30), future(40),
        "1",           # só ida
        "4",           # passo: a cada 5 dias (3 buscas)
        "",            # confirma
        "3", "", "3",  # alerta: teto padrão, no máximo 1 escala
        "0",           # sai das ações
        "0",
    ])
    app.run()
    out = buf.getvalue()
    assert "menor preço" in out
    w = store.list_watches()[0]
    assert w["step_days"] == 5 and w["max_stops"] == 1 and w["trip_days"] == ""
    routes = routes_with_watches(Config(routes=[], channels=["console"]), store)
    assert len(routes) == 1 and len(routes[0].queries()) == 3


def test_cota_bloqueia_busca(tmp_path):
    app, store, _, buf = make_app(tmp_path, ["1", "gru", "ssa", future(60), "1", "0"],
                                  provider=Real(), quota=0)
    app.run()
    assert "só restam 0" in buf.getvalue()
    assert store.searches_this_month() == 0


def test_vendedores_em_cache_nao_gastam_cota_de_novo(tmp_path):
    app, store, opened, _ = make_app(tmp_path, [
        "1", "gru", "ssa", future(60), "1",
        "1", "0",        # onde comprar (1 busca extra) e volta
        "1", "0",        # de novo: vem do cache
        "0", "0",
    ], provider=Real())
    app.run()
    assert store.searches_this_month() == 2   # 1 pesquisa + 1 consulta de vendedores


def test_remover_alerta(tmp_path):
    app, store, _, _ = make_app(tmp_path, ["3", "1", "s", "0", "0"])
    wid = store.add_watch("GRU", "SSA", date.today() + timedelta(days=30),
                          date.today() + timedelta(days=30), 7, [], 900.0, None)
    app.run()
    assert store.list_watches() == [] and wid == 1


def test_pretty_key():
    assert pretty_key("GRU-SSA:2027-01-20:OW") == "20/01 · só ida"
    assert pretty_key("GRU-SSA:2027-01-20:2027-01-27") == "20/01 → 27/01"
    assert pretty_key("lixo") == "lixo"
