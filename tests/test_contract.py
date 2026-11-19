"""Unit tests for the data-contract rules (pure logic, no files)."""

import duckdb
import pytest

from ingestion import contract as ct


@pytest.fixture
def listings():
    return ct.load("listings")


def run_checks(c: ct.Contract, rows_sql: str) -> dict[str, int]:
    con = duckdb.connect()
    con.execute(f"create view src as {rows_sql}")
    return dict(con.execute(ct.check_sql(c, "src")).fetchall())


def test_every_contract_loads():
    for name in ct.DATASETS:
        assert ct.load(name).primary_key


def test_missing_required_column_is_reported(listings):
    cols = [c for c in listings.required_columns if c != "price"]
    assert ct.schema_violations(listings, cols) == ["missing required column: price"]


def test_type_null_domain_and_key_checks():
    c = ct.load("calendar")
    results = run_checks(
        c,
        """
        select * from (values
            ('1', '2026-01-01', 't', '2', '30'),
            ('1', '2026-01-01', 'f', '2', '30'),     -- duplicate key
            ('x', '2026-01-02', 'maybe', '2', '30')  -- bad id type, bad boolean
        ) t(listing_id, date, available, minimum_nights, maximum_nights)""",
    )
    violations = ct.evaluate(c, results, previous_rows=None)
    assert "type:listing_id:bigint: 1 rows" in violations
    assert "accepted_values:available: 1 rows" in violations
    assert "unique:listing_id+date: 1 rows" in violations
    assert any(v.startswith("volume:") for v in violations)  # 3 rows < min_rows


def test_volume_drop_against_previous_accepted(listings):
    ok = ct.evaluate(listings, {"row_count": 30_000}, previous_rows=35_000)
    partial = ct.evaluate(listings, {"row_count": 4_506}, previous_rows=28_994)
    assert ok == []
    assert partial and "84% drop" in partial[0]


def test_money_values_parse(listings):
    results = run_checks(
        listings,
        """
        select '1' id, '2026-01-01' last_scraped, '9' host_id, 'Harlem' neighbourhood_cleansed,
               'Manhattan' neighbourhood_group_cleansed, '40.8' latitude, '-73.9' longitude,
               'Private room' room_type, '2' accommodates, '$1,250.00' price, '2' minimum_nights,
               '3' availability_30, '30' availability_365, '0' number_of_reviews, null license""",
    )
    assert results["type:price:money"] == 0
