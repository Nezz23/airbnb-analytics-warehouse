"""Dashboard backend selection and Snowflake result handling, offline (a fake connection stands in)."""

import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "dashboard"))
import backends  # noqa: E402


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(backends, "_snowflake_con", None)
    for var in ("AIRBNB_WAREHOUSE_BACKEND", *backends.SNOWFLAKE_REQUIRED):
        monkeypatch.delenv(var, raising=False)


class FakeCursor:
    def __init__(self, con):
        self.con = con
        self.description = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params):
        self.con.calls.append((sql, params))
        if self.con.fail_with:
            err, self.con.fail_with = self.con.fail_with, None
            raise err
        self.description = [("SNAPSHOT_DATE",), ("MEDIAN_PRICE_USD",), ("LISTINGS",)]

    def fetchall(self):
        return [(date(2026, 11, 1), Decimal("187.50"), 12), (date(2026, 10, 4), None, 7)]


class FakeConnection:
    def __init__(self, fail_with=None):
        self.calls, self.fail_with, self.closed = [], fail_with, False

    def cursor(self):
        return FakeCursor(self)

    def is_closed(self):
        return self.closed


def test_duckdb_is_the_default():
    assert backends.backend_name() == "duckdb"


def test_unknown_backend_is_rejected(monkeypatch):
    monkeypatch.setenv("AIRBNB_WAREHOUSE_BACKEND", "bigquery")
    with pytest.raises(ValueError, match="bigquery"):
        backends.backend_name()


def test_snowflake_settings_name_missing_variables_without_connecting():
    with pytest.raises(RuntimeError, match="SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER, SNOWFLAKE_PRIVATE_KEY_PATH"):
        backends.snowflake_settings()


def test_snowflake_settings_are_cost_conscious_and_read_only_by_default(monkeypatch):
    for var in backends.SNOWFLAKE_REQUIRED:
        monkeypatch.setenv(var, "x")
    s = backends.snowflake_settings()
    assert s["role"] == "REPORTER"
    assert s["client_session_keep_alive"] is False
    assert s["session_parameters"]["STATEMENT_TIMEOUT_IN_SECONDS"] <= 60
    assert s["session_parameters"]["QUERY_TAG"] == "airbnb_dashboard"


def test_snowflake_results_match_the_duckdb_shape():
    con = FakeConnection()
    df = backends.run_snowflake("select 1 where x = ?", ["Harlem"], connect=lambda: con)
    assert list(df.columns) == ["snapshot_date", "median_price_usd", "listings"]
    assert df.median_price_usd.dtype == float and df.median_price_usd.iloc[0] == 187.5
    assert con.calls == [("select 1 where x = ?", ["Harlem"])]


def test_snowflake_connection_is_reused_and_reopened_after_session_expiry():
    expired = Exception("Authentication token has expired")
    expired.errno = 390114
    first, second = FakeConnection(), FakeConnection()
    pool = iter([first, second])
    backends.run_snowflake("q1", [], connect=lambda: next(pool))
    first.fail_with = expired
    backends.run_snowflake("q2", [], connect=lambda: next(pool))
    assert [c[0] for c in first.calls] == ["q1", "q2"] and [c[0] for c in second.calls] == ["q2"]


def test_snowflake_errors_other_than_expiry_propagate():
    con = FakeConnection(fail_with=RuntimeError("SQL compilation error"))
    with pytest.raises(RuntimeError, match="compilation"):
        backends.run_snowflake("bad", [], connect=lambda: con)
