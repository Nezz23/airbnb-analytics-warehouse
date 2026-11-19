"""Offline Snowflake readiness: nothing here connects to Snowflake or needs credentials.

These guard the portability fixes found in the readiness audit (docs/snowflake.md): DuckDB-only SQL
outside adapter-dispatched macros, unquoted Snowflake reserved words, and a bronze load script that
overwrote every snapshot with the last one because all bronze files share the name part-0.parquet.
"""

import importlib.util
import json
import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
DBT_DIR = ROOT / "transform"
# Models, snapshots and singular tests must be portable as written; macros may dispatch per adapter.
PORTABLE_SQL = sorted(p for d in ("models", "snapshots", "tests") for p in (DBT_DIR / d).rglob("*.sql"))

DUCKDB_ONLY = {
    r"\bread_(parquet|csv|json)\w*\(": "DuckDB table function",
    r"::": "DuckDB/Postgres cast shorthand (use cast(... as ...))",
    r"\blist_\w+\(": "DuckDB list function",
    r"\b(strftime|strptime|epoch)\(": "DuckDB date function (use dbt.* cross-db macros)",
    r"\bstring_split\(": "DuckDB string function",
    r"\bregexp_replace\(": "flags differ by engine: use the parse_money-style dispatched macro",
    r"\blen\(": "DuckDB length function (use the array_length macro)",
    r"\b(arg_max|arg_min)\(": "DuckDB aggregate (Snowflake: max_by / min_by)",
}
# Snowflake reserved words that are plausible column names. Flag them where used as an identifier.
RESERVED_AS_IDENTIFIER = re.compile(
    r"(?:\.|\bas\s+|\bcast\(\s*)(rows|sample|start|increment|values|current|minus|regexp|column|row)\b", re.I
)


def _sql(path: Path) -> str:
    text = re.sub(r"\{#.*?#\}", "", path.read_text(), flags=re.S)  # Jinja comments
    return re.sub(r"--[^\n]*", "", text)  # SQL line comments


@pytest.mark.parametrize("path", PORTABLE_SQL, ids=lambda p: str(p.relative_to(DBT_DIR)))
def test_portable_sql_has_no_duckdb_only_constructs(path):
    sql = _sql(path)
    found = [f"{why} ({pat})" for pat, why in DUCKDB_ONLY.items() if re.search(pat, sql, re.I)]
    assert not found, f"{path.name}: {found}"


@pytest.mark.parametrize("path", PORTABLE_SQL, ids=lambda p: str(p.relative_to(DBT_DIR)))
def test_snowflake_reserved_words_are_not_bare_identifiers(path):
    hits = RESERVED_AS_IDENTIFIER.findall(_sql(path))
    assert not hits, f"{path.name}: quote with adapter.quote(): {hits}"


def test_project_parses_with_the_snowflake_adapter(tmp_path):
    pytest.importorskip("dbt.adapters.snowflake", reason="uv sync --extra snowflake")
    env = {**os.environ, "DBT_TARGET": "snowflake", "SNOWFLAKE_ACCOUNT": "offline-parse-only"}
    common = ["--project-dir", str(DBT_DIR), "--profiles-dir", str(DBT_DIR)]
    if not (DBT_DIR / "dbt_packages" / "dbt_utils").exists():
        subprocess.run(["dbt", "deps", *common], cwd=DBT_DIR, env=env, check=True)
    proc = subprocess.run(
        ["dbt", "parse", *common, "--target-path", str(tmp_path), "--no-partial-parse"],
        cwd=DBT_DIR,
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-3000:]
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["metadata"]["adapter_type"] == "snowflake"
    models = [n for n in manifest["nodes"].values() if n["resource_type"] == "model"]
    assert len(models) >= 15


def _render_load():
    spec = importlib.util.spec_from_file_location("render_load", ROOT / "deploy" / "snowflake" / "render_load.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def bronze(tmp_path):
    entries = [
        {"dataset": ds, "snapshot_date": d, "rows": 10, "status": "accepted", "file_sha256": "x"}
        for ds in ("listings", "calendar", "reviews")
        for d in ("2026-01-01", "2026-02-01", "2026-03-01")
    ]
    (tmp_path / "_manifest.jsonl").write_text("".join(json.dumps(e) + "\n" for e in entries))
    return tmp_path


def test_bronze_load_stages_each_snapshot_under_its_own_prefix(bronze):
    sql = _render_load().render(bronze)
    puts = re.findall(r"^put '\S+' (@\S+) ", sql, re.M)
    loads = [p for p in puts if "/manifest/" not in p]
    assert len(set(loads)) == 9, "one stage prefix per (dataset, snapshot): part-0.parquet names collide"
    assert sql.count("copy into airbnb.bronze_landing.listings") == 3


def test_bronze_load_is_idempotent_and_case_safe(bronze):
    sql = _render_load().render(bronze)
    for snapshot in ("2026-01-01", "2026-02-01", "2026-03-01"):
        assert f"delete from airbnb.bronze_landing.listings where _snapshot_date = '{snapshot}'::date" in sql
    assert sql.count("force = true") == 10  # 9 snapshot files + manifest: reruns reload, never append
    assert sql.count("ignore_case => true") == 3
    assert 'as "rows"' in sql


def test_bronze_load_since_still_stages_the_template_snapshot(bronze):
    sql = _render_load().render(bronze, since="2026-03-01")
    first_put = sql.index("snapshot_date=2026-01-01/part-0.parquet")
    assert first_put < sql.index("create table if not exists airbnb.bronze_landing.listings")
    assert sql.count("copy into airbnb.bronze_landing.listings") == 1
