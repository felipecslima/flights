import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("git_guard", Path(__file__).resolve().parent.parent / "scripts" / "git_guard.py")
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)


def test_arquivos_bloqueados_e_exemplo_liberado():
    assert g.problems([".env"], "")
    assert g.problems(["pasta/flights.db", "config.yaml", "run.log", "skiplagged_probe.json"], "")
    assert not g.problems([".env.example", "README.md", "config.example.yaml"], "")


def test_segredos_no_diff():
    tel = "+TOKEN = '123456789:" + "A" * 35 + "'"
    assert g.problems(["a.py"], tel)
    assert g.problems(["a.py"], "+key = '" + "ab12" * 16 + "'")
    assert g.problems(["a.py"], "+SERPAPI_KEY=abc123")
    assert g.problems(["a.py"], "+curl https://hooks.slack.com/services/T0/B0/xyz")
    assert g.problems(["a.py"], "+-b 'cf_clearance=" + "x" * 30 + "'")


def test_diff_limpo_passa():
    ok = "+SERPAPI_KEY=\n+TELEGRAM_BOT_TOKEN=\n+def f(): pass\n+hash 'abc'\n"
    assert not g.problems(["a.py", ".env.example"], ok)
    assert not g.problems(["a.py"], "-SERPAPI_KEY=segredo")   # linha removida não conta
