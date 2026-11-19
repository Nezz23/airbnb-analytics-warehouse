"""Dashboard smoke test: every tab renders against the synthetic warehouse without errors."""

import ast
import inspect
import json
import sys
from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = Path(__file__).parent.parent / "dashboard" / "app.py"


def test_dashboard_renders_and_maps_every_ll18_group(data_dir, monkeypatch):
    monkeypatch.setenv("AIRBNB_DATA_DIR", str(data_dir))
    monkeypatch.syspath_prepend(str(APP.parent))
    sys.modules.pop("queries", None)  # resolve the warehouse path from this test's env
    at = AppTest.from_file(str(APP), default_timeout=120).run()
    assert not at.exception, [e.value for e in at.exception]
    assert len(at.metric) >= 5
    maps = [
        json.loads(c.proto.spec)
        for c in at.get("plotly_chart")
        if json.loads(c.proto.spec)["data"][0].get("type") == "scattermap"
    ]
    names = {t["name"] for t in maps[0]["data"]}
    assert names == {"Unregistered short-term", "Registered or exempt"}  # the two default layers


def test_every_query_the_app_calls_exists_with_matching_arity(monkeypatch):
    """Static check: each q.<name>(...) in app.py is a queries.py function taking that many arguments."""
    monkeypatch.syspath_prepend(str(APP.parent))
    sys.modules.pop("queries", None)
    import queries

    defined = {n: f for n, f in vars(queries).items() if inspect.isfunction(f) and f.__module__ == "queries"}
    for node in ast.walk(ast.parse(APP.read_text())):
        if not isinstance(node, ast.Call):
            continue
        func = (
            node.func.args[0] if isinstance(node.func, ast.Call) and node.func.args else node.func
        )  # cached(q.f)(...)
        if isinstance(func, ast.Attribute) and getattr(func.value, "id", None) == "q":
            assert func.attr in defined, f"app.py line {node.lineno}: queries has no function {func.attr}"
            params = len(inspect.signature(defined[func.attr]).parameters)
            assert len(node.args) == params, f"app.py line {node.lineno}: {func.attr} takes {params} args"
