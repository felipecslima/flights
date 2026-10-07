"""Ler e gravar chaves no arquivo .env (sem dependências)."""
from __future__ import annotations

import os
import re
from pathlib import Path


def read_key(path: Path, name: str) -> str:
    if not path.exists():
        return ""
    m = re.search(rf"^{re.escape(name)}=(.*)$", path.read_text(encoding="utf-8"), flags=re.M)
    return m.group(1).split("#", 1)[0].strip() if m else ""


def write_key(path: Path, name: str, value: str) -> None:
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    line = f"{name}={value}"
    if re.search(rf"^{re.escape(name)}=.*$", text, flags=re.M):
        text = re.sub(rf"^{re.escape(name)}=.*$", lambda _m: line, text, flags=re.M)
    else:
        text = text.rstrip("\n") + ("\n" if text else "") + line + "\n"
    path.write_text(text, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
