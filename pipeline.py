"""End-to-end incremental run: bronze -> silver -> SCD2 replay -> gold -> tests.

    uv run python pipeline.py                 # incremental: only new snapshots do work
    uv run python pipeline.py --full-refresh  # rebuild silver/gold and replay SCD2 from the start

Each test run's results are appended to data/quality/test_history.jsonl for the DQ dashboard.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from ingestion import bronze

ROOT = Path(__file__).parent
DBT_DIR = ROOT / "transform"
DATA = Path(os.environ.setdefault("AIRBNB_DATA_DIR", str(ROOT / "data"))).resolve()
os.environ["AIRBNB_DATA_DIR"] = str(DATA)


def dbt(*args: str, capture: bool = False) -> str:
    cmd = ["dbt", *args, "--project-dir", str(DBT_DIR), "--profiles-dir", str(DBT_DIR)]
    print("$", " ".join(cmd[:6]), flush=True)
    proc = subprocess.run(cmd, cwd=DBT_DIR, text=True, capture_output=capture)
    if proc.returncode != 0:
        if capture:
            print(proc.stdout, proc.stderr)
        sys.exit(f"dbt {args[0]} failed (exit {proc.returncode})")
    return proc.stdout if capture else ""


def pending_snapshot_dates() -> list[str]:
    out = dbt("run-operation", "pending_snapshot_dates", capture=True)
    line = next(ln for ln in out.splitlines() if "PENDING_DATES=" in ln)
    value = line.split("PENDING_DATES=", 1)[1].strip()
    return [d for d in value.split(",") if d]


def record_test_results() -> None:
    results = json.loads((DBT_DIR / "target" / "run_results.json").read_text())
    run_at = results["metadata"]["generated_at"]
    out = DATA / "quality" / "test_history.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a") as f:
        for r in results["results"]:
            if r["unique_id"].startswith(("test.", "unit_test.")):
                f.write(json.dumps({
                    "run_at": run_at,
                    "test": r["unique_id"],
                    "status": r["status"],
                    "failures": r.get("failures"),
                    "seconds": round(r["execution_time"], 3),
                }) + "\n")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--full-refresh", action="store_true")
    a = p.parse_args()
    started = datetime.now(UTC)

    bronze.run(DATA / "raw", DATA / "bronze", DATA / "warehouse.duckdb")
    refresh = ["--full-refresh"] if a.full_refresh else []
    dbt("deps", "--quiet")
    dbt("build", "--select", "silver", "--indirect-selection", "cautious", *refresh)
    if a.full_refresh:
        dbt("run-operation", "drop_listings_scd2")

    # SCD2 history is only correct if snapshots are replayed one at a time, oldest first.
    pending = pending_snapshot_dates()
    print(f"SCD2 replay: {len(pending)} snapshot(s) pending {pending}")
    for snapshot_date in pending:
        dbt("snapshot", "--vars", json.dumps({"snapshot_date": snapshot_date}))

    dbt("build", "--exclude", "resource_type:snapshot", *refresh)
    record_test_results()
    print(f"pipeline finished in {(datetime.now(UTC) - started).total_seconds():.1f}s")


if __name__ == "__main__":
    main()
