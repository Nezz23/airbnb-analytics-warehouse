"""End-to-end: synthetic snapshots -> pipeline.py -> assertions on the warehouse it builds."""


def one(con, sql):
    return con.execute(sql).fetchone()


def test_partial_snapshot_is_quarantined_and_excluded(warehouse):
    statuses = dict(
        warehouse.execute(
            "select dataset, status from silver.stg_bronze_manifest where snapshot_date = '2026-02-08'"
        ).fetchall()
    )
    assert statuses == {"listings": "quarantined", "calendar": "quarantined", "reviews": "quarantined"}
    assert one(warehouse, "select count(*) from gold.fct_listing_snapshot where snapshot_date = '2026-02-08'") == (0,)


def test_scd2_records_attribute_change(warehouse):
    rows = warehouse.execute(
        "select room_type, valid_from, valid_to from gold.dim_listing where listing_id = 1 order by valid_from"
    ).fetchall()
    assert [(str(f), str(t)) for _, f, t in rows] == [("2026-01-01", "2026-02-01"), ("2026-02-01", "None")]
    assert rows[0][0] != rows[1][0]


def test_scd2_closes_and_reopens_a_missing_listing(warehouse):
    rows = warehouse.execute(
        "select valid_from, valid_to from gold.dim_listing where listing_id = 3 order by valid_from"
    ).fetchall()
    assert [(str(f), str(t)) for f, t in rows] == [("2026-01-01", "2026-02-01"), ("2026-03-01", "None")]


def test_scd2_unchanged_listing_has_one_version(warehouse):
    assert one(warehouse, "select count(*) from gold.dim_listing where listing_id = 10") == (1,)


def test_license_normalised_and_ll18_transition(warehouse):
    assert one(warehouse, "select license, ll18_status from gold.dim_listing where listing_id = 2 and is_current") == (
        "OSE-STRREG-0000002",
        "registered",
    )
    assert one(
        warehouse,
        """select listings from gold.mart_ll18_transitions
        where to_snapshot_date = '2026-03-01' and from_status = 'long_term_30_plus'
          and to_status = 'unregistered_short_term'""",
    ) == (1,)


def test_lifecycle_counts(warehouse):
    sql = "select snapshot_date, new_listings, relisted_listings, delisted_listings from gold.mart_listing_lifecycle"
    rows = {str(d): (n, r, x) for d, n, r, x in warehouse.execute(sql).fetchall()}
    assert rows["2026-02-01"] == (0, 0, 1)
    assert rows["2026-03-01"] == (0, 1, 0)


def test_reviews_deduplicated_across_cumulative_snapshots(warehouse):
    assert one(warehouse, "select count(*) = count(distinct review_id) from silver.stg_reviews") == (True,)
