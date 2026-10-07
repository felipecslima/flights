"""Lista as promoções do Passagens Imperdíveis (cartões da página).  Uso: python3 scripts/promos.py [palavra ...]"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from flight_alerts import deals as dl  # noqa: E402

try:
    items = dl.fetch()
except dl.DealsError as exc:
    raise SystemExit(f"Falhou: {exc}")
keys = sys.argv[1:]
shown = [d for d in items if dl.matches(d, keys)]
print(f"{len(shown)} promoção(ões){' com ' + ', '.join(keys) if keys else ''}:\n")
for d in shown[:25]:
    print(f"- {d.line()}\n  {d.link}")
