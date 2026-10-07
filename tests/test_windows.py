"""Pontos que costumam quebrar no Windows, simulados aqui e rodados de verdade pelo CI (windows-latest)."""
import importlib.util
import os
import sys
from pathlib import Path

import pytest

from flight_alerts.envfile import read_key, write_key

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def run_mod():
    spec = importlib.util.spec_from_file_location("run_mod", ROOT / "run.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_caminho_do_python_do_venv(run_mod, monkeypatch):
    monkeypatch.setattr(os, "name", "nt")
    assert run_mod.venv_python().as_posix().endswith(".venv/Scripts/python.exe")
    monkeypatch.setattr(os, "name", "posix")
    assert run_mod.venv_python().as_posix().endswith(".venv/bin/python")


def test_subprocesso_usa_utf8(run_mod, monkeypatch):
    seen = {}

    def fake_call(cmd, cwd=None, env=None):
        seen["env"] = env
        return 0

    monkeypatch.setattr(run_mod.subprocess, "call", fake_call)
    assert run_mod.run(["x"]) == 0
    assert seen["env"]["PYTHONUTF8"] == "1"


def test_trava_do_git_usa_o_python_real(run_mod, tmp_path, monkeypatch):
    (tmp_path / ".git" / "hooks").mkdir(parents=True)
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "git_guard.py").write_text("print()", encoding="utf-8")
    monkeypatch.setattr(run_mod, "ROOT", tmp_path)
    run_mod.ensure_git_guard()
    body = (tmp_path / ".git" / "hooks" / "pre-commit").read_text(encoding="utf-8")
    assert Path(sys.executable).as_posix() in body and "\\" not in body


def test_env_com_acentos_e_quebra_de_linha_windows(tmp_path):
    p = tmp_path / ".env"
    p.write_bytes("EMAIL_TO=joão@ex.com\r\nTELEGRAM_CHAT_ID=5257111050\r\n".encode("utf-8"))
    assert read_key(p, "EMAIL_TO") == "joão@ex.com"
    assert read_key(p, "TELEGRAM_CHAT_ID") == "5257111050"
    write_key(p, "TELEGRAM_CHAT_ID", "42")
    assert read_key(p, "TELEGRAM_CHAT_ID") == "42" and read_key(p, "EMAIL_TO") == "joão@ex.com"


def test_config_com_quebra_de_linha_windows(tmp_path):
    from flight_alerts.config import load

    p = tmp_path / "config.yaml"
    txt = ("channels: [console]\ncurrency: BRL\nroutes:\n  - origin: GRU\n    destination: SSA\n"
           "    depart_from: 2027-01-10\n    depart_to: 2027-01-20\n    max_price: 900\n"
           "deals:\n  enabled: true\n  keywords: [\"belém\"]\n")
    p.write_bytes(txt.replace("\n", "\r\n").encode("utf-8"))
    cfg = load(str(p))
    assert cfg.routes[0].origin == "GRU" and cfg.deals["keywords"] == ["belém"]


def test_pagina_de_compra_local_abre_como_uri(tmp_path):
    p = tmp_path / "abrir.html"
    p.write_text("x", encoding="utf-8")
    assert p.as_uri().startswith("file:///")
