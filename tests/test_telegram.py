import io

import pytest
import requests
from rich.console import Console

from flight_alerts.cli import App
from flight_alerts.config import Config
from flight_alerts.db import Store
from flight_alerts.envfile import read_key, write_key
from flight_alerts.providers import MockProvider
from flight_alerts.telegram import TelegramApi, TelegramError, mask, valid_token_format
from flight_alerts.ui import PlainPrompter

TOKEN = "123456789:" + "A" * 35


def test_formato_e_mascara():
    assert valid_token_format(TOKEN)
    assert not valid_token_format("123:abc")
    assert not valid_token_format("")
    assert TOKEN not in mask(TOKEN) and mask(TOKEN).startswith("123456789:AAA")


class FakeResp:
    def __init__(self, data):
        self._d = data

    def json(self):
        return self._d


class FakeSession:
    """Responde por nome do método da API (último pedaço da URL)."""

    def __init__(self, responses):
        self.responses, self.calls = responses, []

    def post(self, url, json=None, timeout=None):
        method = url.rsplit("/", 1)[1]
        self.calls.append((method, json))
        r = self.responses[method]
        if isinstance(r, Exception):
            raise r
        return FakeResp(r)


def test_me_e_erros():
    ok = TelegramApi(TOKEN, FakeSession({"getMe": {"ok": True, "result": {"username": "meubot"}}}))
    assert ok.me()["username"] == "meubot"
    bad = TelegramApi(TOKEN, FakeSession({"getMe": {"ok": False, "error_code": 401, "description": "Unauthorized"}}))
    with pytest.raises(TelegramError, match="token recusado"):
        bad.me()
    net = TelegramApi(TOKEN, FakeSession({"getMe": requests.ConnectionError("x")}))
    with pytest.raises(TelegramError, match="sem conexão"):
        net.me()
    nochat = TelegramApi(TOKEN, FakeSession({"sendMessage": {"ok": False, "error_code": 400,
                                                              "description": "Bad Request: chat not found"}}))
    with pytest.raises(TelegramError, match="chat não encontrado"):
        nochat.send("1", "oi")


def test_latest_chat_pega_o_privado_mais_recente():
    updates = [
        {"message": {"chat": {"id": 1, "type": "private", "first_name": "Antigo"}}},
        {"message": {"chat": {"id": -99, "type": "group", "title": "Grupo"}}},
        {"message": {"chat": {"id": 2, "type": "private", "first_name": "Joel"}}},
        {"edited_message": {"chat": {"id": 3, "type": "private"}}},   # ignorado
    ]
    api = TelegramApi(TOKEN, FakeSession({"getUpdates": {"ok": True, "result": updates}}))
    assert api.latest_chat() == {"id": 2, "name": "Joel"}
    vazio = TelegramApi(TOKEN, FakeSession({"getUpdates": {"ok": True, "result": []}}))
    assert vazio.latest_chat() is None


def test_envfile(tmp_path):
    p = tmp_path / ".env"
    assert read_key(p, "A") == ""
    write_key(p, "A", "1")
    write_key(p, "B", "2")
    write_key(p, "A", "3")
    assert read_key(p, "A") == "3" and read_key(p, "B") == "2"
    assert p.read_text().count("A=") == 1


# --------------------------------------------------------------------------


class FakeTelegram:
    instances: list = []

    def __init__(self, token):
        self.token, self.sent, self.polls = token, [], 0
        self.fail_me = FakeTelegram.fail_me_times > 0
        FakeTelegram.fail_me_times -= 1
        FakeTelegram.instances.append(self)

    fail_me_times = 0

    def me(self):
        if self.fail_me:
            raise TelegramError("token recusado pelo Telegram (confira se copiou inteiro)")
        return {"username": "meubot"}

    def latest_chat(self):
        self.polls += 1
        return None if self.polls < 3 else {"id": 777, "name": "Joel"}

    def send(self, chat_id, text):
        self.sent.append((chat_id, text))


def make_app(tmp_path, answers):
    it = iter(answers)
    buf = io.StringIO()
    console = Console(file=buf, width=140, force_terminal=False)
    ui = PlainPrompter(ask=lambda _p: next(it), console=console)
    opened: list[str] = []
    FakeTelegram.instances = []
    app = App(MockProvider(), Store(str(tmp_path / "t.db")), Config(routes=[], channels=["console"]), ui,
              console=console, open_url=opened.append, tmpdir=str(tmp_path),
              env_path=tmp_path / ".env", telegram_factory=FakeTelegram, sleep=lambda s: None)
    return app, opened, buf


def test_conectar_telegram_fluxo_completo(tmp_path, monkeypatch):
    for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        monkeypatch.delenv(k, raising=False)
    FakeTelegram.fail_me_times = 0
    app, opened, buf = make_app(tmp_path, ["5", "abc", TOKEN, "0"])   # 'abc' é recusado antes de chamar a API
    app.run()

    assert read_key(tmp_path / ".env", "TELEGRAM_BOT_TOKEN") == TOKEN
    assert read_key(tmp_path / ".env", "TELEGRAM_CHAT_ID") == "777"
    assert opened == ["https://t.me/meubot"]
    bot = FakeTelegram.instances[-1]
    assert bot.polls == 3 and len(bot.sent) == 1 and bot.sent[0][0] == "777"
    out = buf.getvalue()
    assert "@meubot" in out and "Telegram conectado" in out
    assert TOKEN not in out                       # o token nunca é impresso
    import os
    assert os.environ["TELEGRAM_CHAT_ID"] == "777"
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN"); monkeypatch.delenv("TELEGRAM_CHAT_ID")


def test_token_recusado_pergunta_de_novo(tmp_path, monkeypatch):
    for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        monkeypatch.delenv(k, raising=False)
    FakeTelegram.fail_me_times = 1
    app, _, buf = make_app(tmp_path, ["5", TOKEN, TOKEN, "0"])
    app.run()
    assert "token recusado" in buf.getvalue()
    assert read_key(tmp_path / ".env", "TELEGRAM_CHAT_ID") == "777"
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN"); monkeypatch.delenv("TELEGRAM_CHAT_ID")


def test_ja_conectado_envia_teste(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    FakeTelegram.fail_me_times = 0
    app, _, buf = make_app(tmp_path, ["5", "1", "0"])   # Telegram -> enviar teste -> sair
    app.run()
    assert FakeTelegram.instances[-1].sent[0][0] == "42"
    assert "conectado" in buf.getvalue() and TOKEN not in buf.getvalue()
