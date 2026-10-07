from datetime import date, timedelta

from flight_alerts.config import Config, Route
from flight_alerts.db import Store
from flight_alerts.main import run_once
from flight_alerts.models import Offer
from flight_alerts.providers.base import Provider
from flight_alerts.rules import RuleConfig


class Fixed(Provider):
    name = "fixed"

    def __init__(self, price):
        self.price = price

    def cheapest(self, q, **kw):
        return Offer(q, self.price, "BRL", "GOL", 0)


class Spy:
    name = "spy"

    def __init__(self):
        self.msgs = []

    def send(self, t):
        self.msgs.append(t)


def make_cfg(quota=250):
    d = date.today() + timedelta(days=60)
    r = Route("GRU", "SSA", d, d + timedelta(days=7), step_days=7, rules=RuleConfig(max_price=900))  # 2 datas
    return Config(routes=[r], channels=["console"], monthly_quota=quota, max_searches_per_run=2)


def test_alerta_e_dedup(tmp_path):
    store, spy, cfg = Store(str(tmp_path / "t.db")), Spy(), make_cfg()
    assert run_once(cfg, Fixed(500), store, [spy]) == 2
    assert len(spy.msgs) == 2
    # segunda rodada, mesmo preço: nada de novo
    assert run_once(cfg, Fixed(500), store, [spy]) == 0


def test_cota_respeitada(tmp_path):
    store, spy = Store(str(tmp_path / "t.db")), Spy()
    cfg = make_cfg(quota=1)
    run_once(cfg, Fixed(2000), store, [spy])
    assert store.searches_this_month() == 1  # só 1 busca apesar de max_per_run=2
    assert run_once(cfg, Fixed(2000), store, [spy]) == 0
    assert store.searches_this_month() == 1


def test_dry_run_nao_registra_alerta(tmp_path):
    store, spy = Store(str(tmp_path / "t.db")), Spy()
    run_once(make_cfg(), Fixed(500), store, [spy], dry_run=True)
    assert spy.msgs == []
    assert store.last_alert_price(make_cfg().routes[0].queries()[0].key) is None
