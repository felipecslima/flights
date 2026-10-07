"""Camada de interação com o usuário.

FancyPrompter: setas, autocompletar, validação inline (prompt_toolkit/questionary).
PlainPrompter: perguntas numeradas com input(); usado sem terminal interativo e nos testes.
Ambos levantam Back quando o usuário cancela (ctrl+c / esc / 0).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Callable

from .places import PLACES, _norm, parse_date, place_label, resolve_place, short_place

ACCENT = "#D97757"   # terracota
DIM = "#8b8b8b"
GOOD = "#6fbf73"
BAD = "#e06c75"


class Back(Exception):
    """O usuário quis voltar/cancelar."""


class _BackChoice:
    def __repr__(self) -> str:
        return "BACK"


BACK = _BackChoice()  # valor de uma opção 'Voltar'


@dataclass
class Choice:
    title: str
    value: Any
    hint: str = ""


Validate = Callable[[str], "str | None"]


def _date_validator(optional: bool, min_date: date | None) -> Validate:
    def check(text: str) -> str | None:
        if optional and not text.strip():
            return None
        try:
            d = parse_date(text)
        except ValueError as e:
            return str(e)
        if d < date.today():
            return "essa data já passou"
        if min_date and d < min_date:
            return f"precisa ser depois de {min_date:%d/%m/%Y}"
        return None

    return check


def _place_validator(text: str) -> str | None:
    if resolve_place(text):
        return None
    return "não conheço essa cidade — digite o nome de uma cidade da lista ou o código de 3 letras (ex.: SSA)"


class Prompter:
    def select(self, message: str, choices: list[Choice]) -> Any: raise NotImplementedError
    def text(self, message: str, default: str = "", validate: Validate | None = None, hint: str = "") -> str: raise NotImplementedError
    def confirm(self, message: str, default: bool = True) -> bool: raise NotImplementedError

    def place(self, message: str, default: str = "") -> str:
        text = self.text(message, default, _place_validator, "ex.: são paulo, rio, GRU")
        return resolve_place(text)  # type: ignore[return-value]

    def date(self, message: str, optional: bool = False, min_date: date | None = None, default: str = "") -> date | None:
        text = self.text(message, default, _date_validator(optional, min_date), "ex.: 17/01 ou 17/01/2027")
        return parse_date(text) if text.strip() else None


# --------------------------------------------------------------------------
# modo simples
# --------------------------------------------------------------------------


class PlainPrompter(Prompter):
    def __init__(self, ask=input, console=None):
        from rich.console import Console

        self._ask = ask
        self.out = console or Console()

    def _read(self, prompt: str) -> str:
        try:
            return self._ask(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            raise Back

    def select(self, message, choices):
        self.out.print(f"\n[bold]{message}[/bold]")
        for i, c in enumerate(choices, 1):
            hint = f"  [dim]{c.hint}[/dim]" if c.hint else ""
            self.out.print(f"  {i}) {c.title}{hint}")
        while True:
            ans = self._read("Escolha (0 = voltar): ")
            if ans in ("", "0"):
                raise Back
            if ans.isdigit() and 1 <= int(ans) <= len(choices):
                value = choices[int(ans) - 1].value
                if value is BACK:
                    raise Back
                return value
            self.out.print("[red]Opção inválida.[/red]")

    def text(self, message, default="", validate=None, hint=""):
        suffix = f" [{default}]" if default else ""
        extra = f" ({hint})" if hint else ""
        while True:
            ans = self._read(f"{message}{extra}{suffix}: ") or default
            err = validate(ans) if validate else None
            if not err:
                return ans
            self.out.print(f"[red]{err}[/red]")

    def confirm(self, message, default=True):
        tag = "S/n" if default else "s/N"
        ans = self._read(f"{message} ({tag}): ").lower()
        return default if not ans else ans in ("s", "sim", "y", "yes")


# --------------------------------------------------------------------------
# modo bonito
# --------------------------------------------------------------------------


class FancyPrompter(Prompter):
    def __init__(self):
        import questionary
        from prompt_toolkit import PromptSession
        from prompt_toolkit.completion import Completer, Completion
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.styles import Style

        self.q = questionary
        self.PromptSession = PromptSession

        self.qstyle = questionary.Style([
            ("qmark", f"fg:{ACCENT} bold"),
            ("question", "bold"),
            ("answer", f"fg:{ACCENT} bold"),
            ("pointer", f"fg:{ACCENT} bold"),
            ("highlighted", f"fg:{ACCENT} bold"),
            ("selected", f"fg:{ACCENT}"),
            ("instruction", f"fg:{DIM}"),
            ("text", ""),
            ("hint", f"fg:{DIM}"),
        ])
        self.ptstyle = Style.from_dict({
            "qmark": f"{ACCENT} bold",
            "question": "bold",
            "hint": DIM,
            "err": BAD,
            "rprompt": "noreverse",
            "completion-menu": "bg:#2b2b2b #e0e0e0",
            "completion-menu.completion.current": f"bg:{ACCENT} #ffffff bold",
            "completion-menu.meta.completion": f"bg:#2b2b2b {DIM}",
            "completion-menu.meta.completion.current": f"bg:{ACCENT} #ffffff",
            "validation-toolbar": f"bg:default {BAD}",
        })

        kb = KeyBindings()

        @kb.add("escape", eager=True)
        def _(event):  # esc = voltar
            event.app.exit(exception=KeyboardInterrupt)

        self.kb = kb

        class PlaceCompleter(Completer):
            def get_completions(self, document, complete_event):
                typed = document.text_before_cursor
                t = _norm(typed)
                if not t:
                    return
                for name, codes, aliases in PLACES:
                    if t in _norm(name) or any(t in _norm(a) for a in aliases) or t in codes.lower():
                        yield Completion(name, start_position=-len(typed), display_meta=short_place(codes))

        self.completer = PlaceCompleter()

    # -- helpers -----------------------------------------------------------
    def _input(self, message: str, default: str, validate: Validate | None, hint: str, completer=None) -> str:
        from prompt_toolkit import print_formatted_text
        from prompt_toolkit.formatted_text import FormattedText

        tip = f"{hint}  ·  esc volta" if hint else "esc volta"
        current = default
        while True:
            try:
                session = self.PromptSession(style=self.ptstyle, key_bindings=self.kb)
                text = session.prompt(
                    [("class:qmark", "› "), ("class:question", message + " ")],
                    default=current,
                    completer=completer,
                    complete_while_typing=completer is not None,
                    reserve_space_for_menu=6 if completer else 0,
                    rprompt=[("class:hint", tip + " ")],   # dica na mesma linha, some ao confirmar
                ).strip()
            except (KeyboardInterrupt, EOFError):
                raise Back
            err = validate(text) if validate else None
            if not err:
                return text
            # erro logo abaixo da linha digitada; pergunta de novo com o texto preenchido
            print_formatted_text(FormattedText([("class:err", f"  ✗ {err}")]), style=self.ptstyle)
            current = text

    # -- API -------------------------------------------------------------------
    def select(self, message, choices):
        items = []
        for c in choices:
            title = [("class:text", c.title)]
            if c.hint:
                title.append(("class:hint", "   " + c.hint))
            items.append(self.q.Choice(title=title, value=c.value))
        try:
            value = self.q.select(
                message, choices=items, qmark="›", pointer="›", style=self.qstyle,
                instruction="(↑↓ navegar · enter escolher · ctrl+c voltar)",
                use_indicator=False, use_jk_keys=False,
            ).unsafe_ask()
        except (KeyboardInterrupt, EOFError):
            raise Back
        if value is BACK:
            raise Back
        return value

    def text(self, message, default="", validate=None, hint=""):
        return self._input(message, default, validate, hint)

    def place(self, message, default=""):
        text = self._input(message, default, _place_validator,
                           "cidade ou código (GRU, SSA…)", self.completer)
        return resolve_place(text)  # type: ignore[return-value]

    def confirm(self, message, default=True):
        try:
            return bool(self.q.confirm(message, default=default, qmark="›", style=self.qstyle).unsafe_ask())
        except (KeyboardInterrupt, EOFError):
            raise Back
