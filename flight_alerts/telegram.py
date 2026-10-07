"""Cliente mínimo da API de bots do Telegram (só o que o projeto usa)."""
from __future__ import annotations

import re

import requests

TOKEN_RE = re.compile(r"^\d{6,}:[A-Za-z0-9_-]{30,}$")


class TelegramError(Exception):
    """Erro devolvido pelo Telegram ou de rede, já com mensagem legível."""


def valid_token_format(token: str) -> bool:
    return bool(TOKEN_RE.match(token.strip()))


def mask(token: str) -> str:
    """123456789:AAH...xyz -> 123456789:AAH…xyz (para mostrar sem expor)."""
    return token if len(token) < 14 else f"{token[:13]}…{token[-3:]}"


class TelegramApi:
    def __init__(self, token: str, session=None, timeout: int = 15):
        self.token = token.strip()
        self.session = session or requests
        self.timeout = timeout

    def _call(self, method: str, **payload) -> dict | list:
        url = f"https://api.telegram.org/bot{self.token}/{method}"
        try:
            resp = self.session.post(url, json=payload, timeout=self.timeout)
            data = resp.json()
        except requests.RequestException:
            raise TelegramError("sem conexão com o Telegram; verifique a internet")
        except ValueError:
            raise TelegramError("resposta inválida do Telegram")
        if not data.get("ok"):
            code = data.get("error_code")
            desc = data.get("description", "erro desconhecido")
            if code == 401:
                raise TelegramError("token recusado pelo Telegram (confira se copiou inteiro)")
            if code == 400 and "chat not found" in desc.lower():
                raise TelegramError("chat não encontrado; mande uma mensagem ao bot e conecte de novo")
            if code == 403:
                raise TelegramError("o bot foi bloqueado ou removido desse chat")
            raise TelegramError(desc)
        return data.get("result", {})

    def me(self) -> dict:
        """Dados do bot (id, username...). Serve para validar o token."""
        return self._call("getMe")  # type: ignore[return-value]

    def latest_chat(self) -> dict | None:
        """Chat privado de quem mais recentemente escreveu ao bot, ou None."""
        updates = self._call("getUpdates", timeout=0, allowed_updates=["message"])
        for u in reversed(updates):  # type: ignore[arg-type]
            msg = u.get("message") or {}
            chat = msg.get("chat") or {}
            if chat.get("type") == "private" and "id" in chat:
                name = chat.get("first_name") or chat.get("username") or str(chat["id"])
                return {"id": chat["id"], "name": name}
        return None

    def send(self, chat_id: str | int, text: str) -> None:
        self._call("sendMessage", chat_id=chat_id, text=text, disable_web_page_preview=False)
