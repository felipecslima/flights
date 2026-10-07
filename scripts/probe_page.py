"""Baixa UMA página do Passagens Imperdíveis, mostra o que o leitor entende e salva o HTML.

Uso: python3 scripts/probe_page.py /passagens-finais-de-semana-no-brasil/
Gera page_probe.html. Me mande o resultado do terminal e o arquivo.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import requests  # noqa: E402

from flight_alerts import deals as dl  # noqa: E402


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else "/passagens-finais-de-semana-no-brasil/"
    if not path.startswith("/"):
        path = "/" + path.split(".com.br", 1)[-1]
    if not dl._allowed(requests, path, 20):
        raise SystemExit("O robots.txt do site não permite essa página.")
    r = requests.get(dl.BASE + path, headers={"User-Agent": dl.UA, "Accept-Language": "pt-BR,pt;q=0.9"}, timeout=20)
    print("HTTP", r.status_code, len(r.text), "bytes")
    if r.status_code != 200:
        return
    Path("page_probe.html").write_text(r.text[:1_500_000], encoding="utf-8")
    deals = dl.parse_cards(r.text)
    print(f"{len(deals)} cartão(ões) lidos:")
    for d in deals[:20]:
        print(f"- {d.line()} | {d.kind} | {d.summary[:60]}")
    print("\ntítulos da página:", re.findall(r"<h[12][^>]*>([^<]{3,80})<", r.text)[:8])
    print("Salvei page_probe.html")


if __name__ == "__main__":
    main()
