"""Bronze ingestion: raw Inside Airbnb CSV snapshots -> contract-checked Parquet.

Incremental: a (dataset, snapshot) pair is skipped when the manifest already holds the same
file hash, so reruns do nothing and a re-delivered file replaces its partition.
Every file lands in bronze (raw fidelity); a contract failure marks it `quarantined`, and
downstream models only read snapshots whose three datasets are all `accepted`.

Usage: python -m ingestion.bronze [--raw data/raw] [--bronze data/bronze]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from ingestion import contract as ct

SNAPSHOT_DIR = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def lit(value) -> str:
    """SQL string literal (DuckDB cannot bind parameters inside CREATE VIEW / COPY)."""
    return "'" + str(value).replace("'", "''") + "'"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_manifest(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def write_manifest(path: Path, entries: list[dict]) -> None:
    entries = sorted(entries, key=lambda e: (e["dataset"], e["snapshot_date"]))
    tmp = path.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(e) + "\n" for e in entries))
    tmp.replace(path)  # atomic: a crash never leaves a half-written manifest


def previous_accepted_rows(entries: list[dict], dataset: str, snapshot: str) -> int | None:
    prior = [
        e for e in entries if e["dataset"] == dataset and e["status"] == "accepted" and e["snapshot_date"] < snapshot
    ]
    return max(prior, key=lambda e: e["snapshot_date"])["rows"] if prior else None


def ingest_file(
    con, contract: ct.Contract, src: Path, snapshot: str, digest: str, entries: list[dict], bronze: Path
) -> dict:
    con.execute(f"create or replace temp view src as select * from read_csv({lit(src)}, all_varchar=true, header=true)")
    columns = [r[0] for r in con.execute("describe src").fetchall()]
    violations = ct.schema_violations(contract, columns)
    rows = con.execute("select count(*) from src").fetchone()[0]
    if not violations:
        results = dict(con.execute(ct.check_sql(contract, "src")).fetchall())
        violations = ct.evaluate(contract, results, previous_accepted_rows(entries, contract.dataset, snapshot))

    keep = ", ".join('"' + c + '"' for c in columns if c not in contract.drop_columns)
    out_dir = bronze / contract.dataset / f"snapshot_date={snapshot}"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    con.execute(
        f"""copy (select {keep}, {lit(snapshot)}::date as _snapshot_date, {lit(src)} as _source_file,
                         {lit(digest)} as _file_sha256, current_timestamp as _ingested_at from src)
            to {lit(out_dir / "part-0.parquet")} (format parquet, compression zstd)"""
    )
    return {
        "dataset": contract.dataset,
        "snapshot_date": snapshot,
        "file_sha256": digest,
        "rows": rows,
        "status": "quarantined" if violations else "accepted",
        "violations": violations,
        "ingested_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }


def publish_views(bronze: Path, warehouse: Path) -> None:
    """Expose bronze as a `bronze` schema of views in the local warehouse (tables on Snowflake)."""
    con = duckdb.connect(str(warehouse))
    con.execute("create schema if not exists bronze")
    for dataset in ct.DATASETS:
        if any((bronze / dataset).glob("*/*.parquet")):
            con.execute(
                f"create or replace view bronze.{dataset} as select * from read_parquet("
                f"{lit(bronze / dataset / '*' / '*.parquet')}, hive_partitioning = true, union_by_name = true)"
            )
    con.execute(
        "create or replace view bronze.manifest as select * from read_json("
        f"{lit(bronze / '_manifest.jsonl')}, format = 'newline_delimited')"
    )
    con.close()


def run(raw: Path, bronze: Path, warehouse: Path) -> list[dict]:
    bronze.mkdir(parents=True, exist_ok=True)
    manifest_path = bronze / "_manifest.jsonl"
    entries = read_manifest(manifest_path)
    con = duckdb.connect()
    changed = []
    snapshots = sorted(p.name for p in raw.iterdir() if p.is_dir() and SNAPSHOT_DIR.match(p.name))
    for dataset in ct.DATASETS:
        contract = ct.load(dataset)
        contract_hash = sha256(ct.CONTRACT_DIR / f"{dataset}.yml")[:12]
        for snapshot in snapshots:  # date order, so volume checks compare to the prior snapshot
            src = raw / snapshot / f"{dataset}.csv.gz"
            if not src.exists():
                continue
            digest = sha256(src)
            known = next((e for e in entries if e["dataset"] == dataset and e["snapshot_date"] == snapshot), None)
            # A changed file or a changed contract both force re-validation.
            if known and known["file_sha256"] == digest and known.get("contract_sha256") == contract_hash:
                continue
            entry = ingest_file(con, contract, src, snapshot, digest, entries, bronze)
            entry["contract_sha256"] = contract_hash
            entries = [e for e in entries if e is not known] + [entry]
            write_manifest(manifest_path, entries)  # after each file: progress survives a crash
            changed.append(entry)
            print(
                f"{entry['status']:>11}  {dataset:<8} {snapshot}  {entry['rows']:>10,} rows"
                + (f"  {entry['violations']}" if entry["violations"] else "")
            )
    if not changed:
        print("bronze: up to date, nothing to ingest")
    publish_views(bronze, warehouse)
    return changed


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--raw", type=Path, default=Path("data/raw"))
    p.add_argument("--bronze", type=Path, default=Path("data/bronze"))
    p.add_argument("--warehouse", type=Path, default=Path("data/warehouse.duckdb"))
    a = p.parse_args()
    run(a.raw.resolve(), a.bronze.resolve(), a.warehouse.resolve())


if __name__ == "__main__":
    main()
