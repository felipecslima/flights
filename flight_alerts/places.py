"""Cidades/aeroportos, datas e formatação — tudo que é puro (sem terminal)."""
from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime

# (nome exibido, códigos IATA, apelidos). Cidades com vários aeroportos buscam em todos.
PLACES: list[tuple[str, str, list[str]]] = [
    ("São Paulo", "GRU,CGH,VCP", ["sp", "sampa"]),
    ("Guarulhos", "GRU", ["gru"]),
    ("Congonhas", "CGH", ["cgh"]),
    ("Viracopos (Campinas)", "VCP", ["viracopos", "campinas", "vcp"]),
    ("Rio de Janeiro", "GIG,SDU", ["rio"]),
    ("Galeão", "GIG", ["galeao", "gig"]),
    ("Santos Dumont", "SDU", ["sdu"]),
    ("Belo Horizonte", "CNF,PLU", ["bh"]),
    ("Brasília", "BSB", []),
    ("Salvador", "SSA", []),
    ("Belém", "BEL", []),
    ("Fortaleza", "FOR", []),
    ("Recife", "REC", []),
    ("Porto Alegre", "POA", []),
    ("Curitiba", "CWB", []),
    ("Florianópolis", "FLN", ["floripa"]),
    ("Manaus", "MAO", []),
    ("Natal", "NAT", []),
    ("Maceió", "MCZ", []),
    ("Goiânia", "GYN", []),
    ("Vitória", "VIX", []),
    ("São Luís", "SLZ", []),
    ("Teresina", "THE", []),
    ("Cuiabá", "CGB", []),
    ("Campo Grande", "CGR", []),
    ("João Pessoa", "JPA", []),
    ("Aracaju", "AJU", []),
    ("Porto Seguro", "BPS", []),
    ("Foz do Iguaçu", "IGU", []),
    ("Navegantes (Balneário Camboriú)", "NVT", ["navegantes", "balneario camboriu", "itajai"]),
    ("Joinville", "JOI", []),
    ("Londrina", "LDB", []),
    ("Lisboa", "LIS", []),
    ("Porto (Portugal)", "OPO", []),
    ("Madri", "MAD", ["madrid"]),
    ("Paris", "CDG,ORY", []),
    ("Londres", "LHR,LGW", ["london"]),
    ("Miami", "MIA", []),
    ("Orlando", "MCO", []),
    ("Nova York", "JFK,EWR,LGA", ["new york", "ny"]),
    ("Buenos Aires", "EZE,AEP", []),
    ("Santiago", "SCL", []),
    ("Montevidéu", "MVD", ["montevideu"]),
    ("Cancún", "CUN", ["cancun"]),
]


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFD", s)
    return "".join(c for c in s if unicodedata.category(c) != "Mn").lower().strip()


_LOOKUP: dict[str, str] = {}
_LABEL: dict[str, str] = {}
for _name, _codes, _aliases in PLACES:
    _LOOKUP.setdefault(_norm(_name), _codes)
    for _a in _aliases:
        _LOOKUP.setdefault(_norm(_a), _codes)
    _LABEL.setdefault(_codes, _name)


def resolve_place(text: str) -> str | None:
    """'são paulo' -> 'GRU,CGH,VCP'; 'gru ssa' -> 'GRU,SSA'; inválido -> None."""
    t = _norm(text)
    if not t:
        return None
    if t in _LOOKUP:
        return _LOOKUP[t]
    tokens = [x for x in re.split(r"[\s,;/]+", t) if x]
    if tokens and all(re.fullmatch(r"[a-z]{3}", x) for x in tokens):
        return ",".join(x.upper() for x in tokens)
    return None


def place_label(codes: str) -> str:
    """'GRU,CGH,VCP' -> 'São Paulo'; código desconhecido volta como está ('XYZ')."""
    return _LABEL.get(codes, codes.replace(",", "/"))


def short_place(codes: str) -> str:
    return codes.replace(",", "/")


def parse_date(text: str, today: date | None = None) -> date:
    """Aceita 17/01/2027, 17/01/27, 17/01 (próxima ocorrência), 2027-01-17, 17-01-2027."""
    today = today or date.today()
    t = text.strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(t, fmt).date()
        except ValueError:
            pass
    m = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})", t)
    if m:
        day, month = int(m.group(1)), int(m.group(2))
        for year in (today.year, today.year + 1):
            try:
                d = date(year, month, day)
            except ValueError:
                continue
            if d >= today:
                return d
    raise ValueError(f"não entendi a data '{text}' (ex.: 17/01 ou 17/01/2027)")


_DIAS = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]
_MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]


def fmt_date(d: date, year: bool = True) -> str:
    """sáb, 17 jan 2027"""
    base = f"{_DIAS[d.weekday()]}, {d.day} {_MESES[d.month - 1]}"
    return f"{base} {d.year}" if year else base


_SYMBOLS = {"BRL": "R$", "USD": "US$", "EUR": "€", "GBP": "£"}


def money(x: float | None, currency: str = "BRL") -> str:
    """1234.5, 'BRL' -> 'R$ 1.235'; 'USD' -> 'US$ 1.235'; moeda desconhecida mostra o código."""
    if x is None:
        return "—"
    return f"{_SYMBOLS.get(currency, currency)} " + f"{x:,.0f}".replace(",", ".")


def brl(x: float | None) -> str:
    return money(x, "BRL")


def hhmm(ts: str) -> str:
    return ts.split(" ")[-1] if ts else "?"


def dur(minutes: int | None) -> str:
    return "?" if not minutes else f"{minutes // 60}h{minutes % 60:02d}"


def sparkline(values: list[float]) -> str:
    if not values:
        return ""
    lo, hi = min(values), max(values)
    blocks = "▁▂▃▄▅▆▇█"
    if hi == lo:
        return blocks[0] * len(values)
    return "".join(blocks[int((v - lo) / (hi - lo) * (len(blocks) - 1))] for v in values)


def bar(used: int, total: int, width: int = 10) -> str:
    if total <= 0:
        return "░" * width
    filled = max(0, min(width, round(width * used / total)))
    return "█" * filled + "░" * (width - filled)
