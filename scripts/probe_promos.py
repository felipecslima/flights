"""Descobre como ler as promoções do Passagens Imperdíveis (poucas consultas, 1 por endereço).

Uso: python3 scripts/probe_promos.py
Gera promos_probe.json (resumo) e promos_home.html (página inicial). Me mande os dois.
"""
import json
import re
import sys
from pathlib import Path
from urllib.parse import urljoin

import requests

BASE = "https://passagensimperdiveis.com.br/"
HEAD = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", "Accept-Language": "pt-BR,pt;q=0.9"}
CANDIDATES = ["feed", "feed/", "rss", "rss.xml", "feed.xml", "index.xml", "atom.xml", "sitemap.xml", "wp-json/wp/v2/posts?per_page=3"]


def get(url):
    try:
        r = requests.get(url, headers=HEAD, timeout=20)
        return r
    except requests.RequestException as e:
        print("  erro:", e.__class__.__name__)
        return None


def main():
    report = {}
    print("página inicial…")
    r = get(BASE)
    if r is None:
        return
    print("  HTTP", r.status_code, len(r.text), "bytes")
    report["home"] = {"status": r.status_code, "bytes": len(r.text), "server": r.headers.get("Server"),
                      "content_type": r.headers.get("Content-Type")}
    Path("promos_home.html").write_text(r.text[:1_500_000], encoding="utf-8")
    links = re.findall(r'<link[^>]+type="application/(?:rss|atom)\+xml"[^>]*>', r.text, re.I)
    report["autodiscovery"] = links
    print("  feeds anunciados na página:", links or "nenhum")
    report["scripts_hint"] = sorted(set(re.findall(r'(?:wp-content|_next|nuxt|gatsby|__NEXT_DATA__)', r.text)))[:5]
    report["candidates"] = {}
    for c in CANDIDATES:
        url = urljoin(BASE, c)
        rr = get(url)
        if rr is None:
            continue
        ct = rr.headers.get("Content-Type", "")
        print(f"  {c:<34} HTTP {rr.status_code} {ct[:40]}")
        report["candidates"][c] = {"status": rr.status_code, "type": ct, "head": rr.text[:300] if rr.ok else ""}
    Path("promos_probe.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print("\nSalvei promos_probe.json e promos_home.html. Me mande os dois.")


if __name__ == "__main__":
    sys.exit(main())
