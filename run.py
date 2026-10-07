#!/usr/bin/env python3
"""Um comando só: prepara o que faltar e abre o programa.

    python3 run.py              # pesquisa interativa (faz o setup sozinho se precisar)
    python3 run.py --mock       # idem, com preços falsos (não gasta cota)
    python3 run.py monitor      # roda o monitor de alertas (o que o cron executa)
    python3 run.py test         # roda os testes
    python3 run.py --reset      # recria o ambiente do zero

Só usa a biblioteca padrão, então roda antes de qualquer instalação.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
ENV_FILE = ROOT / ".env"
ENV_EXAMPLE = ROOT / ".env.example"
MIN_PY = (3, 9)


def say(msg: str) -> None:
    print(f"[setup] {msg}", flush=True)


def die(msg: str, code: int = 1) -> None:
    print(f"\n[erro] {msg}", file=sys.stderr)
    sys.exit(code)


# --------------------------------------------------------------------------
# ambiente virtual e dependências
# --------------------------------------------------------------------------


def venv_python() -> Path:
    sub = "Scripts/python.exe" if os.name == "nt" else "bin/python"
    return VENV / sub


def venv_ok() -> bool:
    py = venv_python()
    if not py.exists():
        return False
    try:
        return subprocess.run([str(py), "-c", "import sys"], capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def ensure_venv(reset: bool = False) -> None:
    if sys.version_info < MIN_PY:
        die(f"Preciso do Python {MIN_PY[0]}.{MIN_PY[1]}+ (você tem {sys.version.split()[0]}). "
            "Instale um mais novo em python.org ou com `brew install python`.")
    if reset and VENV.exists():
        say("Apagando o ambiente antigo...")
        shutil.rmtree(VENV)
    if venv_ok():
        return
    if VENV.exists():  # existe mas está quebrado (ex.: Python foi atualizado)
        say("Ambiente quebrado, recriando...")
        shutil.rmtree(VENV)
    say("Criando o ambiente (.venv)...")
    r = subprocess.run([sys.executable, "-m", "venv", str(VENV)])
    if r.returncode != 0 or not venv_ok():
        die("Não consegui criar o ambiente virtual. No Linux, instale o pacote python3-venv "
            "(ex.: `sudo apt install python3-venv`) e rode de novo.")


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""


def ensure_deps(req_name: str = "requirements.txt") -> None:
    """Instala só se o arquivo de requisitos mudou desde a última vez."""
    req = ROOT / req_name
    if not req.exists():
        die(f"{req_name} não encontrado em {ROOT}.")
    stamp = VENV / f".installed_{req_name}"
    if stamp.exists() and stamp.read_text().strip() == _hash(req):
        return
    say("Instalando dependências (só na primeira vez ou quando mudarem)...")
    r = subprocess.run(
        [str(venv_python()), "-m", "pip", "install", "-q", "--disable-pip-version-check", "-r", str(req)]
    )
    if r.returncode != 0:
        die("Falha ao instalar as dependências. Veja a mensagem do pip acima (internet ok?) "
            "e rode de novo; ele continua de onde parou.")
    stamp.write_text(_hash(req))


# --------------------------------------------------------------------------
# .env e chave da SerpApi
# --------------------------------------------------------------------------


def read_env_key(name: str, text: str | None = None) -> str:
    text = ENV_FILE.read_text(encoding="utf-8") if text is None and ENV_FILE.exists() else (text or "")
    m = re.search(rf"^{re.escape(name)}=(.*)$", text, flags=re.M)
    return m.group(1).split("#", 1)[0].strip() if m else ""


def write_env_key(name: str, value: str) -> None:
    text = ENV_FILE.read_text(encoding="utf-8") if ENV_FILE.exists() else ""
    line = f"{name}={value}"
    if re.search(rf"^{re.escape(name)}=.*$", text, flags=re.M):
        text = re.sub(rf"^{re.escape(name)}=.*$", lambda _m: line, text, flags=re.M)
    else:
        text = text.rstrip("\n") + ("\n" if text else "") + line + "\n"
    ENV_FILE.write_text(text, encoding="utf-8")
    try:
        os.chmod(ENV_FILE, 0o600)
    except OSError:
        pass


def ensure_env() -> None:
    if not ENV_FILE.exists():
        if ENV_EXAMPLE.exists():
            shutil.copy(ENV_EXAMPLE, ENV_FILE)
        else:
            ENV_FILE.write_text("SERPAPI_KEY=\n", encoding="utf-8")
        try:
            os.chmod(ENV_FILE, 0o600)
        except OSError:
            pass
        say("Criei o arquivo .env.")


def ensure_serpapi_key(interactive: bool) -> bool:
    """True se há chave. Pergunta uma vez se faltar; sem terminal, devolve False."""
    if read_env_key("SERPAPI_KEY"):
        return True
    if not interactive:
        return False
    print(
        "\nFalta a chave da SerpApi (fonte Google Flights, mostra onde comprar).\n"
        "  1. Crie a conta grátis em https://serpapi.com (250 buscas/mês)\n"
        "  2. Copie a 'Your Private API Key' no painel e cole aqui.\n"
        "  Enter sem colar nada = usar o Skiplagged (grátis, sem chave).\n"
    )
    try:
        key = input("SERPAPI_KEY: ").strip()
    except EOFError:
        return False
    if not key:
        return False
    write_env_key("SERPAPI_KEY", key)
    if len(key) != 64:
        say("Aviso: as chaves da SerpApi costumam ter 64 caracteres; confira se colou inteira.")
    say("Chave salva no .env.")
    return True


# --------------------------------------------------------------------------


GUARD_MARK = "# flight-alerts git guard"


def ensure_git_guard() -> None:
    """Se a pasta é um repositório git, instala a trava que impede commit de chaves."""
    hooks = ROOT / ".git" / "hooks"
    guard = ROOT / "scripts" / "git_guard.py"
    if not hooks.is_dir() or not guard.exists():
        return
    hook = hooks / "pre-commit"
    body = f'#!/bin/sh\n{GUARD_MARK}\nexec python3 "{guard}"\n'
    try:
        if hook.exists() and GUARD_MARK not in hook.read_text(encoding="utf-8"):
            say("Já existe um pre-commit seu; não mexi. Rode scripts/git_guard.py nele se quiser a trava.")
            return
        if not hook.exists() or hook.read_text(encoding="utf-8") != body:
            hook.write_text(body, encoding="utf-8")
            os.chmod(hook, 0o755)
            say("Instalei a trava do git: commits com chaves ou .env são bloqueados.")
    except OSError:
        pass


def needs_serpapi(args: list[str]) -> bool:
    """Só a fonte SerpApi exige chave (o padrão é Skiplagged, sem chave)."""
    if "--mock" in args:
        return False
    if "--source" in args:
        i = args.index("--source")
        return i + 1 < len(args) and args[i + 1] == "serpapi"
    return (read_env_key("FLIGHT_SOURCE") or "skiplagged").lower() == "serpapi"


def run(cmd: list[str]) -> int:
    try:
        return subprocess.call(cmd, cwd=str(ROOT))
    except KeyboardInterrupt:
        return 130


def main(argv: list[str]) -> int:
    args = list(argv)
    reset = "--reset" in args
    if reset:
        args.remove("--reset")
    sub = args[0] if args and not args[0].startswith("-") else ""
    interactive = sys.stdin.isatty() and sys.stdout.isatty()

    ensure_venv(reset=reset)

    if sub == "test":
        ensure_deps()
        ensure_deps("requirements-dev.txt")
        return run([str(venv_python()), "-m", "pytest", "-q", *args[1:]])

    ensure_deps()
    ensure_env()
    ensure_git_guard()
    py = str(venv_python())

    if sub == "monitor":
        if not (ROOT / "config.yaml").exists():
            if (ROOT / "config.example.yaml").exists():
                shutil.copy(ROOT / "config.example.yaml", ROOT / "config.yaml")
                say("Criei o config.yaml a partir do exemplo. Ajuste as rotas nele e rode de novo.")
                return 0
            die("Falta o config.yaml.")
        if needs_serpapi(args) and not read_env_key("SERPAPI_KEY"):
            ensure_serpapi_key(interactive)
        return run([py, "-m", "flight_alerts", *args[1:]])

    # modo padrão: pesquisa interativa
    if needs_serpapi(args) and not ensure_serpapi_key(interactive):
        say("Sem chave da SerpApi: usando o Skiplagged (grátis).")
        args += ["--source", "skiplagged"]
    return run([py, "-m", "flight_alerts.cli", *[a for a in args if a != "cli"]])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
