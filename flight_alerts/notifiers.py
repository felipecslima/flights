from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage

import requests

log = logging.getLogger(__name__)


class Notifier:
    name = "base"

    def send(self, text: str) -> None:  # pragma: no cover
        raise NotImplementedError


class ConsoleNotifier(Notifier):
    name = "console"

    def send(self, text: str) -> None:
        print("\n--- ALERTA ---\n" + text + "\n--------------")


class TelegramNotifier(Notifier):
    name = "telegram"

    def __init__(self, token: str, chat_id: str):
        self.token, self.chat_id = token, chat_id

    def send(self, text: str) -> None:
        from .telegram import TelegramApi

        TelegramApi(self.token, timeout=20).send(self.chat_id, text)


class SlackNotifier(Notifier):
    name = "slack"

    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url

    def send(self, text: str) -> None:
        r = requests.post(self.webhook_url, json={"text": text}, timeout=20)
        r.raise_for_status()


class EmailNotifier(Notifier):
    name = "email"

    def __init__(self, host: str, port: int, user: str, password: str, to: str):
        self.host, self.port, self.user, self.password, self.to = host, port, user, password, to

    def send(self, text: str) -> None:
        msg = EmailMessage()
        msg["Subject"] = text.splitlines()[0]
        msg["From"] = self.user
        msg["To"] = self.to
        msg.set_content(text)
        with smtplib.SMTP(self.host, self.port, timeout=30) as s:
            s.starttls()
            s.login(self.user, self.password)
            s.send_message(msg)


def build_notifiers(names: list[str]) -> list[Notifier]:
    """Cria notificadores a partir de variáveis de ambiente. Canal sem credencial é ignorado com aviso."""
    out: list[Notifier] = []
    env = os.environ.get
    for n in names:
        if n == "console":
            out.append(ConsoleNotifier())
        elif n == "telegram":
            if env("TELEGRAM_BOT_TOKEN") and env("TELEGRAM_CHAT_ID"):
                out.append(TelegramNotifier(env("TELEGRAM_BOT_TOKEN"), env("TELEGRAM_CHAT_ID")))
            else:
                log.warning("telegram: faltam TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID")
        elif n == "slack":
            if env("SLACK_WEBHOOK_URL"):
                out.append(SlackNotifier(env("SLACK_WEBHOOK_URL")))
            else:
                log.warning("slack: falta SLACK_WEBHOOK_URL")
        elif n == "email":
            keys = ["SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "EMAIL_TO"]
            if all(env(k) for k in keys):
                out.append(
                    EmailNotifier(
                        env("SMTP_HOST"), int(env("SMTP_PORT", "587")),
                        env("SMTP_USER"), env("SMTP_PASSWORD"), env("EMAIL_TO"),
                    )
                )
            else:
                log.warning("email: faltam variáveis SMTP_* / EMAIL_TO")
        else:
            log.warning("canal desconhecido: %s", n)
    return out
