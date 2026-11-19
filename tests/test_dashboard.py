"""Dashboard smoke test: every tab renders against the synthetic warehouse without errors."""

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
