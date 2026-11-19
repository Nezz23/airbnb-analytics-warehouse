"""Data contracts: load a YAML contract and turn it into SQL checks.

Pure logic only: nothing here reads files or touches a database. `bronze.py` runs the SQL.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

CONTRACT_DIR = Path(__file__).parent / "contracts"
DATASETS = ("listings", "calendar", "reviews")

# How a contract type is checked against a text value. `money` is Inside Airbnb's "$1,234.00".
_PARSE = {
    "bigint": "try_cast({c} as bigint)",
    "integer": "try_cast({c} as integer)",
    "double": "try_cast({c} as double)",
    "date": "try_cast({c} as date)",
    "boolean": "case when {c} in ('t','f') then true end",
    "money": "try_cast(regexp_replace({c}, '[$,]', '', 'g') as double)",
    "varchar": "{c}",
}


@dataclass(frozen=True)
class Contract:
    dataset: str
    primary_key: list[str]
    required_columns: dict[str, str]
    not_null: list[str]
    accepted_values: dict[str, list[str]]
    drop_columns: list[str]
    min_rows: int
    max_drop_vs_previous: float


def load(dataset: str) -> Contract:
    spec = yaml.safe_load((CONTRACT_DIR / f"{dataset}.yml").read_text())
    unknown = set(spec["required_columns"].values()) - set(_PARSE)
    if unknown:
        raise ValueError(f"{dataset}: unknown contract types {unknown}")
    return Contract(
        dataset=spec["dataset"],
        primary_key=spec["primary_key"],
        required_columns=spec["required_columns"],
        not_null=spec["not_null"],
        accepted_values=spec.get("accepted_values") or {},
        drop_columns=spec.get("drop_columns") or [],
        min_rows=spec["volume"]["min_rows"],
        max_drop_vs_previous=spec["volume"]["max_drop_vs_previous"],
    )


def _q(col: str) -> str:
    return '"' + col.replace('"', '""') + '"'


def schema_violations(contract: Contract, columns: list[str]) -> list[str]:
    missing = [c for c in contract.required_columns if c not in columns]
    return [f"missing required column: {c}" for c in missing]


def check_sql(contract: Contract, relation: str) -> str:
    """One query returning (check_name, failing_rows) for every row-level rule."""
    checks: list[tuple[str, str]] = [("row_count", "count(*)")]
    for col, typ in contract.required_columns.items():
        parsed = _PARSE[typ].format(c=_q(col))
        checks.append((f"type:{col}:{typ}", f"count(*) filter (where {_q(col)} is not null and {parsed} is null)"))
    for col in contract.not_null:
        checks.append((f"not_null:{col}", f"count(*) filter (where {_q(col)} is null)"))
    for col, values in contract.accepted_values.items():
        allowed = ", ".join("'" + v.replace("'", "''") + "'" for v in values)
        checks.append((f"accepted_values:{col}", f"count(*) filter (where {_q(col)} not in ({allowed}))"))
    pk = ", ".join(_q(c) for c in contract.primary_key)
    checks.append(("unique:" + "+".join(contract.primary_key), f"count(*) - count(distinct ({pk}))"))
    selects = " union all ".join(f"select '{name}' as check_name, {expr} as failures from {relation}" for name, expr in checks)
    return selects


def evaluate(contract: Contract, results: dict[str, int], previous_rows: int | None) -> list[str]:
    """Turn check results plus the last accepted snapshot's volume into violation messages."""
    rows = results.pop("row_count")
    violations = [f"{name}: {n} rows" for name, n in results.items() if n]
    if rows < contract.min_rows:
        violations.append(f"volume: {rows} rows < min_rows {contract.min_rows}")
    if previous_rows and rows < previous_rows * (1 - contract.max_drop_vs_previous):
        violations.append(
            f"volume: {rows} rows is a {1 - rows / previous_rows:.0%} drop vs last accepted "
            f"({previous_rows}); limit {contract.max_drop_vs_previous:.0%}"
        )
    return violations
