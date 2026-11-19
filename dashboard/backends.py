"""Warehouse backends for the dashboard: local DuckDB (default) or Snowflake.

    AIRBNB_WAREHOUSE_BACKEND=duckdb      # default: data/warehouse.duckdb, read-only
    AIRBNB_WAREHOUSE_BACKEND=snowflake   # needs `uv sync --extra snowflake` and SNOWFLAKE_* (docs/snowflake.md)

queries.py writes SQL that both engines accept (qmark `?` parameters, median, count_if, group by all)
and always gets back a DataFrame with lowercase column names and float measures.

This module is separate from queries.py on purpose: app.py reloads queries on every Streamlit run,
which would otherwise drop and reopen the Snowflake connection on every interaction.
"""

from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path

import duckdb
import pandas as pd

BACKENDS = ("duckdb", "snowflake")
SNOWFLAKE_REQUIRED = ("SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER", "SNOWFLAKE_PRIVATE_KEY_PATH")


def backend_name() -> str:
    name = os.environ.get("AIRBNB_WAREHOUSE_BACKEND", "duckdb").strip().lower()
    if name not in BACKENDS:
        raise ValueError(f"AIRBNB_WAREHOUSE_BACKEND={name!r}: expected one of {BACKENDS}")
    return name


def duckdb_path() -> Path:
    return Path(os.environ.get("AIRBNB_DATA_DIR", Path(__file__).parent.parent / "data")) / "warehouse.duckdb"


def run_duckdb(sql: str, params: list) -> pd.DataFrame:
    # Open and close per call so the dashboard never holds the file lock while the pipeline writes.
    with duckdb.connect(str(duckdb_path()), read_only=True) as con:
        return con.execute(sql, params).df()


_snowflake_con = None


def snowflake_settings() -> dict:
    missing = [v for v in SNOWFLAKE_REQUIRED if not os.environ.get(v)]
    if missing:
        raise RuntimeError(f"Snowflake backend needs {', '.join(missing)} (see docs/snowflake.md)")
    return {
        "account": os.environ["SNOWFLAKE_ACCOUNT"],
        "user": os.environ["SNOWFLAKE_USER"],
        "authenticator": "SNOWFLAKE_JWT",
        "private_key_file": os.environ["SNOWFLAKE_PRIVATE_KEY_PATH"],
        "role": os.environ.get("SNOWFLAKE_ROLE", "REPORTER"),
        "warehouse": os.environ.get("SNOWFLAKE_WAREHOUSE", "TRANSFORMING"),
        "database": os.environ.get("SNOWFLAKE_DATABASE", "AIRBNB"),
        "client_session_keep_alive": False,
        "session_parameters": {
            "QUERY_TAG": "airbnb_dashboard",
            "STATEMENT_TIMEOUT_IN_SECONDS": 60,  # a dashboard query should never run for minutes
        },
    }


def _snowflake_connect():
    try:
        import snowflake.connector
    except ImportError as e:
        raise RuntimeError("Snowflake backend needs `uv sync --extra snowflake`") from e
    snowflake.connector.paramstyle = "qmark"  # same `?` placeholders as DuckDB; must be set before connect
    return snowflake.connector.connect(**snowflake_settings())


def _snowflake_execute(con, sql: str, params: list) -> pd.DataFrame:
    with con.cursor() as cur:
        cur.execute(sql, params)
        columns = [d[0].lower() for d in cur.description]  # Snowflake upper-cases unquoted names
        return pd.DataFrame(cur.fetchall(), columns=columns)


def run_snowflake(sql: str, params: list, connect=_snowflake_connect) -> pd.DataFrame:
    global _snowflake_con
    if _snowflake_con is None or _snowflake_con.is_closed():
        _snowflake_con = connect()
    try:
        df = _snowflake_execute(_snowflake_con, sql, params)
    except Exception as e:
        if not _is_session_expired(e):
            raise
        _snowflake_con = connect()  # sessions expire after idle hours; reconnect once
        df = _snowflake_execute(_snowflake_con, sql, params)
    # NUMBER(p, s>0) arrives as Decimal; DuckDB returns doubles. Keep the dashboard engine-agnostic.
    for col in df.columns:
        sample = df[col].dropna()
        if pd.api.types.is_object_dtype(df[col]) and len(sample) and isinstance(sample.iloc[0], Decimal):
            df[col] = df[col].astype(float)
    return df


def _is_session_expired(e: Exception) -> bool:
    # 390112/390114: session/authentication token expired.
    return getattr(e, "errno", None) in (390112, 390114)


def run(sql: str, params: list | None = None) -> pd.DataFrame:
    params = params or []
    return run_snowflake(sql, params) if backend_name() == "snowflake" else run_duckdb(sql, params)
