"""Trava de segurança do git: bloqueia o commit se houver chave/token ou arquivo secreto.

Instalado como .git/hooks/pre-commit pelo run.py. Para rodar à mão: python3 scripts/git_guard.py
Em caso de falso positivo e só nesse caso: git commit --no-verify
"""
from __future__ import annotations

import fnmatch
import re
import subprocess
import sys

BLOCKED_FILES = [".env", ".env.*", "*.db", "config.yaml", "config.yaml.bak", "*.log", "*_probe.json", "*_probe.html"]
ALLOWED_FILES = [".env.example"]
SECRET_PATTERNS = {
    "token de bot do Telegram": re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b"),
    "chave de 64 hex (SerpApi)": re.compile(r"\b[0-9a-f]{64}\b"),
    "token de 32 hex (Travelpayouts)": re.compile(r"(?i)travelpayouts[_a-z]*\s*[=:]\s*[\"']?[0-9a-f]{32}\b"),
    "webhook do Slack": re.compile(r"hooks\.slack\.com/services/\w+/\w+/\w+"),
    "variável secreta preenchida": re.compile(
        r"(?m)^\+?\s*(SERPAPI_KEY|TELEGRAM_BOT_TOKEN|TRAVELPAYOUTS_TOKEN|SLACK_WEBHOOK_URL|SMTP_PASSWORD)\s*=\s*[^\s#]+"),
    "cookie de sessão do Cloudflare": re.compile(r"cf_clearance=[\w.\-]{20,}"),
}


def git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=False).stdout


def problems(names: list[str], diff: str) -> list[str]:
    out = []
    for n in names:
        base = n.rsplit("/", 1)[-1]
        if any(fnmatch.fnmatch(base, a) for a in ALLOWED_FILES):
            continue
        if any(fnmatch.fnmatch(base, b) for b in BLOCKED_FILES):
            out.append(f"arquivo proibido no git: {n}")
    for line in diff.splitlines():
        if not line.startswith("+") or line.startswith("+++"):
            continue
        for label, rx in SECRET_PATTERNS.items():
            if rx.search(line):
                out.append(f"{label}: {line[1:60].strip()}…")
    return out


def main() -> int:
    names = [n for n in git("diff", "--cached", "--name-only", "--diff-filter=ACM").splitlines() if n]
    found = problems(names, git("diff", "--cached", "-U0", "--no-color"))
    if not found:
        return 0
    print("\nCommit bloqueado: achei algo que parece segredo.\n", file=sys.stderr)
    for f in dict.fromkeys(found):
        print(f"  - {f}", file=sys.stderr)
    print("\nTire do stage (git restore --staged <arquivo>) e guarde o valor só no .env.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
