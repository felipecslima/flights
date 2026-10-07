"""Testa o servidor MCP do Skiplagged e salva as respostas cruas.

Uso:  python3 scripts/probe_skiplagged.py [ORIGEM DESTINO AAAA-MM-DD]
Ex.:  python3 scripts/probe_skiplagged.py GRU SSA 2027-02-10
Gera skiplagged_probe.json — me mande esse arquivo (sem dados pessoais).
"""
from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flight_alerts.models import Query  # noqa: E402
from flight_alerts.providers.skiplagged import McpClient, SkiplaggedProvider, build_args  # noqa: E402


def main() -> None:
    args = sys.argv[1:4]
    o, d, day = args if len(args) == 3 else ("GRU", "SSA", str(date.today() + timedelta(days=60)))
    dep = date.fromisoformat(day)
    report: dict = {}
    client = McpClient()
    try:
        client.start()
    except Exception as exc:
        print("FALHOU ao iniciar:", exc)
        return
    print("sessão:", client.session_id or "(sem)")
    report["tools"] = client.tools
    for name, t in client.tools.items():
        props = list((t.get("inputSchema") or {}).get("properties", {}))
        print(f"- {name}: {props}")

    prov = SkiplaggedProvider(client=client)
    steps = {
        "search": lambda: prov.search(Query(o, d, dep)),
        "calendar": lambda: prov.calendar(o, d, dep, dep + timedelta(days=14)),
        "destinations": lambda: prov.destinations(o, dep, dep + timedelta(days=14)),
    }
    for step, fn in steps.items():
        print(f"\n== {step} {o}->{d} {dep}")
        try:
            res = fn()
            report[step] = {"parsed": [r.__dict__ if hasattr(r, "__dict__") else str(r) for r in res][:10],
                            "raw": prov.last_raw}
            print(f"{len(res)} resultado(s) lidos; primeiros:")
            for r in res[:3]:
                print("  ", r)
        except Exception as exc:
            report[step] = {"error": str(exc), "raw": prov.last_raw}
            print("erro:", exc)
    out = Path("skiplagged_probe.json")
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str)[:900_000])
    print(f"\nSalvei {out}. Me mande esse arquivo.")


if __name__ == "__main__":
    main()
