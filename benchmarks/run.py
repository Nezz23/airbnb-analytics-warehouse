"""Query-performance benchmarks: the same questions answered from each medallion layer.

    uv run python benchmarks/run.py            # writes benchmarks/results.json and docs/benchmarks.md

Each query runs once to warm the OS file cache, then REPEATS times on a fresh DuckDB connection;
we report the median and min wall-clock time. Results depend on the machine, recorded alongside.
"""

from __future__ import annotations

import json
import os
import platform
import statistics
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

import duckdb

ROOT = Path(__file__).parent.parent
DATA = ROOT / "data"
WAREHOUSE = DATA / "warehouse.duckdb"
REPEATS = 5

RAW = f"read_csv('{DATA}/raw/*/listings.csv.gz', all_varchar = true, filename = true)"
BRONZE = f"read_parquet('{DATA}/bronze/listings/*/*.parquet', hive_partitioning = true)"
RAW_SNAP = "cast(regexp_extract(filename, '(\\d{4}-\\d{2}-\\d{2})', 1) as date)"
MONEY = "try_cast(regexp_replace(price, '[$,]', '', 'g') as double)"

QUESTIONS = {
    "Median price by borough per snapshot": {
        "raw_csv": f"select {RAW_SNAP} s, neighbourhood_group_cleansed b, median({MONEY}) from {RAW} group by all",
        "bronze_parquet": f"select snapshot_date, neighbourhood_group_cleansed, median({MONEY}) "
        f"from {BRONZE} group by all",
        "gold_mart": "select snapshot_date, borough, median(median_price_usd) "
        "from gold.mart_neighbourhood_snapshot group by all",
    },
    "Unregistered short-term listings per snapshot (LL18)": {
        "raw_csv": f"select {RAW_SNAP}, count(*) from {RAW} where license is null "
        "and try_cast(minimum_nights as int) < 30 group by all",
        "bronze_parquet": f"select snapshot_date, count(*) from {BRONZE} where license is null "
        "and try_cast(minimum_nights as int) < 30 group by all",
        "gold_mart": "select snapshot_date, sum(listings) from gold.mart_ll18_status "
        "where ll18_status = 'unregistered_short_term' group by all",
    },
    "Point-in-time: every listing's attributes as of 2026-07-01": {
        # Without SCD2: find each listing's latest snapshot on or before the date, then read it.
        # Same attribute columns on both sides.
        "bronze_parquet": f"select id, name, host_id, neighbourhood_cleansed, neighbourhood_group_cleansed, "
        "property_type, room_type, accommodates, minimum_nights, maximum_nights, instant_bookable, license "
        f"from {BRONZE} where snapshot_date <= date '2026-07-01' "
        "qualify row_number() over (partition by id order by snapshot_date desc) = 1",
        "gold_scd2": "select listing_id, listing_name, host_id, neighbourhood, borough, property_type, room_type, "
        "accommodates, minimum_nights, maximum_nights, instant_bookable, license from gold.dim_listing "
        "where valid_from <= date '2026-07-01' "
        "and (valid_to is null or valid_to > date '2026-07-01')",
    },
}


def time_query(sql: str) -> list[float]:
    def once() -> float:
        con = duckdb.connect(str(WAREHOUSE), read_only=True)
        start = time.perf_counter()
        con.execute(sql).fetchall()
        elapsed = time.perf_counter() - start
        con.close()
        return elapsed

    once()  # warm-up
    return [once() for _ in range(REPEATS)]


def dir_size(path: Path, pattern: str) -> int:
    return sum(p.stat().st_size for p in path.glob(pattern))


def machine() -> dict:
    chip = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True).stdout.strip()
    return {
        "platform": platform.platform(),
        "cpu": chip or platform.processor(),
        "cores": os.cpu_count(),
        "duckdb": duckdb.__version__,
    }


def main() -> None:
    results = {"run_at": datetime.now(UTC).isoformat(timespec="seconds"), "machine": machine(), "queries": []}
    for question, variants in QUESTIONS.items():
        for layer, sql in variants.items():
            runs = time_query(sql)
            results["queries"].append(
                {"question": question, "layer": layer, "median_s": statistics.median(runs), "min_s": min(runs)}
            )
            print(f"{question[:45]:<45} {layer:<15} median {statistics.median(runs):8.4f}s")
    results["storage_bytes"] = {
        **{f"raw_{d}": dir_size(DATA / "raw", f"*/{d}.csv.gz") for d in ("listings", "calendar", "reviews")},
        **{f"bronze_{d}": dir_size(DATA / "bronze" / d, "*/*.parquet") for d in ("listings", "calendar", "reviews")},
        "warehouse_duckdb": WAREHOUSE.stat().st_size,
    }
    (ROOT / "benchmarks" / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    write_markdown(results)


def write_markdown(r: dict) -> None:
    m = r["machine"]
    lines = [
        "# Query-performance benchmarks",
        "",
        f"Measured {r['run_at']} on {m['cpu']} ({m['cores']} cores), {m['platform']}, DuckDB {m['duckdb']}.",
        f"Each query: one warm-up, then {REPEATS} timed runs on a fresh connection. "
        "Regenerate with `uv run python benchmarks/run.py`.",
        "",
        "| Question | Layer | Median (s) | Min (s) | Speed-up vs slowest |",
        "|---|---|---:|---:|---:|",
    ]
    for question in dict.fromkeys(q["question"] for q in r["queries"]):
        rows = [q for q in r["queries"] if q["question"] == question]
        slowest = max(q["median_s"] for q in rows)
        for q in rows:
            lines.append(
                f"| {question} | {q['layer']} | {q['median_s']:.4f} | {q['min_s']:.4f} | "
                f"{slowest / q['median_s']:.0f}x |"
            )
    s = r["storage_bytes"]
    lines += [
        "",
        "## Storage",
        "",
        "All 10 snapshots. Reviews shrink mostly because bronze drops reviewer names and review text "
        "(personal data, unused); listings and calendar keep every column.",
        "",
        "| Dataset | Raw CSV gzip (MB) | Bronze Parquet zstd (MB) |",
        "|---|---:|---:|",
        *[
            f"| {d} | {s['raw_' + d] / 1e6:,.0f} | {s['bronze_' + d] / 1e6:,.0f} |"
            for d in ("listings", "calendar", "reviews")
        ],
        "",
        f"DuckDB warehouse (silver + gold, incl. SCD2): {s['warehouse_duckdb'] / 1e6:,.0f} MB.",
        "",
        "Notes: raw and bronze queries include the quarantined 2026-06-08 partial snapshot; gold excludes it, "
        "so answers differ slightly for that date. Gold marts are pre-aggregated, so their speed-up includes "
        "work done once at build time (full pipeline rebuild: see README).",
        "",
    ]
    (ROOT / "docs" / "benchmarks.md").write_text("\n".join(lines))


if __name__ == "__main__":
    main()
