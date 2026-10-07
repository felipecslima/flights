"""Testa a lista "saindo de <cidade>" de uma página do Passagens Imperdíveis, sem cookies e sem disfarce.

Uso: python3 scripts/probe_origem.py /passagens-finais-de-semana-no-brasil/ GRU
Passos: baixa a página, acha os ids dela (idPublicacao/idSumario), chama a API pública da própria
página com a origem pedida e salva a resposta crua em origem_probe.json. Se o site recusar (403),
o script para: não tenta contornar.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import requests  # noqa: E402

from flight_alerts import deals as dl  # noqa: E402

API = "/api/publicacoes/filtrar-origem-destino/"
UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else "/passagens-finais-de-semana-no-brasil/"
    origem = (sys.argv[2] if len(sys.argv) > 2 else "GRU").upper()
    for p in (path, API):
        if not dl._allowed(requests, p, 20):
            raise SystemExit(f"O robots.txt do site não permite {p}.")
    h = {"User-Agent": dl.UA, "Accept-Language": "pt-BR,pt;q=0.9"}
    r = requests.get(dl.BASE + path, headers=h, timeout=20)
    print("página: HTTP", r.status_code)
    if r.status_code != 200:
        return
    ids = {}
    for key in ("idPublicacao", "idSumario"):
        m = re.search(key + r'\\?"?\s*[:=]\s*\\?"(' + UUID + ')', r.text)
        ids[key] = m.group(1) if m else None
    print("ids achados:", ids)
    if not all(ids.values()):
        Path("origem_page.html").write_text(r.text[:1_500_000], encoding="utf-8")
        print("Não achei os ids; salvei origem_page.html. Me mande.")
        return
    params = {**ids, "tipoOrdenacao": "menorValor", "ordenacao": "asc", "page": 0, "origem": origem}
    a = requests.get(dl.BASE + API, params=params, headers={**h, "Accept": "application/json", "Referer": dl.BASE + path}, timeout=20)
    print("api: HTTP", a.status_code, a.headers.get("Content-Type"))
    if a.status_code != 200:
        print("O site recusou a consulta sem cookies; paro por aqui.")
        return
    try:
        data = a.json()
    except ValueError:
        print("Resposta não é JSON:", a.text[:200])
        return
    Path("origem_probe.json").write_text(json.dumps(data, ensure_ascii=False, indent=2)[:900_000])
    print("Salvei origem_probe.json. Me mande esse arquivo.")


if __name__ == "__main__":
    main()
