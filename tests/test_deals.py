import io

from rich.console import Console

from flight_alerts import deals as dl
from flight_alerts.cli import App
from flight_alerts.config import Config
from flight_alerts.db import Store
from flight_alerts.main import run_deals
from flight_alerts.providers import MockProvider
from flight_alerts.ui import PlainPrompter

def card(slug, title, sub, price):
    return (
        '<a href="/%s/"><div class="MuiPaper-root cardPublicacao_container_card__fyItU x">'
        '<span class="tag_label__1">Ida e Volta</span>'
        '<span class="cardPublicacao_container_card_conteudo_titulos_titulo__z">%s</span>'
        '<span class="cardPublicacao_container_card_conteudo_titulos_subtitulo__L">%s</span>'
        '<p class="cardPublicacao_container_card_conteudo_valores_preco_valor__9">%s</p>'
        '<p class="cardPublicacao_container_card_conteudo_valores_preco_posValor__M">ida e volta, taxas incluídas</p>'
        '</div></a>' % (slug, title, sub, price))


AD = ('<a href="/album-de-fotos/"><div class="MuiPaper-root cardPublicacao_container_card__x">'
      '<span class="cardPublicacao_container_card_conteudo_titulos_titulo__z">App</span>'
      '<p class="cardPublicacao_container_card_conteudo_valores_preco_valor__9">R$ 207</p></div></a>')
C1 = card("promocao-de-passagens-para-salvador-2026", "Oportunidade! Salvador", "Praias e Pelourinho", "R$ 389")
C2 = card("promocao-de-passagens-para-lisboa-2026", "Lisboa ou Porto", "Verão europeu", "R$ 3.299")
C3 = card("promocao-de-passagens-para-salvador-2026-2", "Salvador de novo", "Carnaval", "R$ 299")
PAGE = "<html>" + AD + C1 + C2 + C1 + "</html>"          # duplicado e propaganda ignorados
PAGE_NEW = "<html>" + AD + C1 + C2 + C3 + "</html>"


class Resp:
    def __init__(self, text="", code=200):
        self.text, self.status_code = text, code


class Sess:
    """Responde por caminho; robots.txt vazio libera tudo."""

    def __init__(self, pages=None, code=200, robots="User-agent: *\nAllow: /"):
        self.pages, self.code, self.robots, self.urls = pages if pages is not None else {}, code, robots, []

    def get(self, url, headers=None, timeout=None):
        self.urls.append(url)
        if url.endswith("/robots.txt"):
            return Resp(self.robots)
        if self.code != 200:
            return Resp("", self.code)
        path = "/" + url.split(".br/", 1)[1]
        return Resp(self.pages.get(path, self.pages.get("*", "")))


def test_parse_cartoes():
    d = dl.parse_cards(PAGE)
    assert [x.title for x in d] == ["Oportunidade! Salvador", "Lisboa ou Porto"]
    assert d[0].price == 389.0 and d[1].price == 3299.0 and d[0].kind == "Ida e Volta"
    assert d[0].link == "https://passagensimperdiveis.com.br/promocao-de-passagens-para-salvador-2026/"
    assert dl.matches(d[0], ["salvador"]) and not dl.matches(d[1], ["salvador"])
    assert not dl.matches(d[1], [], max_price=1500)


def test_bloqueio_robots_e_layout():
    import pytest
    with pytest.raises(dl.DealsError, match="403"):
        dl.fetch(Sess(code=403))
    with pytest.raises(dl.DealsError, match="robots"):
        dl.fetch(Sess({"*": PAGE}, robots="User-agent: *\nDisallow: /"))
    assert dl.fetch(Sess({"*": "<html>layout novo</html>"})) == []


def test_junta_paginas_sem_duplicar():
    s = Sess({"/promocoes-recentes/": PAGE, "/": PAGE_NEW})
    got = dl.fetch(s)
    assert len(got) == 3 and len([u for u in s.urls if not u.endswith("robots.txt")]) == 2


def test_primeira_vez_silenciosa_depois_so_novas(tmp_path):
    store = Store(str(tmp_path / "d.db"))
    _, fresh = dl.check(store, Sess({"*": PAGE}), ["salvador"])
    assert fresh == []                      # primeira rodada: marca tudo como visto
    _, fresh = dl.check(store, Sess({"*": PAGE}), ["salvador"])
    assert fresh == []
    _, fresh = dl.check(store, Sess({"*": PAGE_NEW}), ["salvador"])
    assert [d.title for d in fresh] == ["Salvador de novo"]


def test_monitor_avisa(tmp_path):
    store = Store(str(tmp_path / "d.db"))
    cfg = Config(routes=[], channels=[], deals={"enabled": True, "keywords": ["salvador"]})
    sent = []

    class N:
        name = "n"

        def send(self, t):
            sent.append(t)

    run_deals(cfg, store, [N()], session=Sess({"*": PAGE}))
    assert run_deals(cfg, store, [N()], session=Sess({"*": PAGE_NEW})) == 1
    assert "Salvador de novo" in sent[0] and "salvador-2026-2" in sent[0]
    assert run_deals(Config(routes=[], channels=[]), store, [N()]) == 0   # desligado


def test_menu_promos(tmp_path):
    it = iter(["6", "", "1", "0", "0", "0"])   # menu promos (mock: posição 6)
    buf = io.StringIO()
    con = Console(file=buf, width=140, force_terminal=False)
    opened = []
    app = App(MockProvider(), Store(str(tmp_path / "t.db")), Config(routes=[], channels=[]),
              PlainPrompter(ask=lambda p: next(it), console=con), console=con, open_url=opened.append,
              tmpdir=str(tmp_path), env_path=tmp_path / ".env")
    app.promos_session = Sess({"*": PAGE})
    app.run()
    assert opened == ["https://passagensimperdiveis.com.br/promocao-de-passagens-para-salvador-2026/"]
    assert "Passagens Imperdíveis" in buf.getvalue()


def row(a, b, price, sold_out=None):
    extra = ('<h6 class="x_aeroportos_span_esgotada_valor__6q">R$ %d +</h6>' % sold_out) if sold_out else ""
    return ('<div class="x_aeroportos__k"><span class="x_aeroportos_span_city__7">%s</span>'
            '<span class="x_aeroportos_span_city__7"><i class="icon-para_esquerda"></i>%s</span></div>'
            '<span class="x_aeroportos_span__2"> A partir de </span>%s'
            '<h6 class="x_aeroportos_span_valor__2X">R$ %d<!-- --> +</h6>') % (a, b, extra, price)


def test_rotas_da_pagina_de_finais_de_semana():
    page = "<html>" + row("Curitiba", "São Paulo", 309) + row("Belo Horizonte", "São Paulo", 339, sold_out=277) + "</html>"
    d = dl.parse_routes(page, dl.BASE + "/passagens-finais-de-semana-no-brasil/")
    assert [(x.title, x.price) for x in d] == [("Curitiba → São Paulo", 309.0), ("Belo Horizonte → São Paulo", 339.0)]
    assert d[0].link.endswith("/passagens-finais-de-semana-no-brasil/#curitiba-sao-paulo-309")
    assert dl.matches(d[0], ["curitiba"]) and dl.matches(d[0], ["são paulo"], max_price=400)
    got = dl.fetch(Sess({"/passagens-finais-de-semana-no-brasil/": page}), pages=["/passagens-finais-de-semana-no-brasil/"])
    assert len(got) == 2
