from datetime import date

from flight_alerts.models import Offer, Query
from flight_alerts.rules import RuleConfig, evaluate


def offer(price, stops=0):
    q = Query("GRU", "SSA", date(2027, 1, 10), date(2027, 1, 17))
    return Offer(q, price, "BRL", "LATAM", stops)


def test_teto_absoluto_dispara():
    a = evaluate(offer(800), RuleConfig(max_price=900), None, 0, None, None)
    assert a and "teto" in a.reasons[0]


def test_acima_do_teto_sem_historico_nao_dispara():
    assert evaluate(offer(1000), RuleConfig(max_price=900), None, 0, None, None) is None


def test_queda_vs_mediana_exige_amostras_minimas():
    cfg = RuleConfig(drop_pct=25, min_samples=8)
    assert evaluate(offer(600), cfg, 1000, 3, 900, None) is None
    a = evaluate(offer(600), cfg, 1000, 8, 900, None)
    assert a and any("mediana" in r for r in a.reasons)


def test_menor_preco_historico():
    cfg = RuleConfig(drop_pct=99, min_samples=8)
    a = evaluate(offer(500), cfg, 1000, 10, 520, None)
    assert a and any("menor preço" in r for r in a.reasons)


def test_antispam_mesma_busca():
    cfg = RuleConfig(max_price=900, realert_drop_pct=5)
    assert evaluate(offer(850), cfg, None, 0, None, 860) is None   # queda < 5%
    assert evaluate(offer(800), cfg, None, 0, None, 860) is not None  # queda > 5%


def test_filtro_de_paradas():
    cfg = RuleConfig(max_price=900, max_stops=0)
    assert evaluate(offer(500, stops=1), cfg, None, 0, None, None) is None
