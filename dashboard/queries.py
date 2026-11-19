"""Read-only access to the gold layer, on local DuckDB (default) or Snowflake: see backends.py.

Every query here is written in SQL both engines accept, so switching engines is configuration only."""

from __future__ import annotations

import pandas as pd

import backends

WAREHOUSE = backends.duckdb_path()


def backend_label() -> str:
    return {"duckdb": "DuckDB (local)", "snowflake": "Snowflake"}[backends.backend_name()]


def query(sql: str, params: list | None = None) -> pd.DataFrame:
    df = backends.run(sql, params)
    # Every warehouse date is a calendar date; show it without a midnight time part.
    for col in df.select_dtypes("datetime").columns:
        if (df[col].dropna().dt.normalize() == df[col].dropna()).all():
            df[col] = df[col].dt.date
    return df


def snapshots() -> list:
    return query("select distinct snapshot_date from gold.fct_listing_snapshot order by 1").snapshot_date.tolist()


def kpis(snapshot_date) -> pd.Series:
    return query(
        """
        select count(*)                                                    as active_listings,
               median(case when quote_nights = 30 then price_usd end)      as median_30_night_rate,
               count_if(minimum_nights < 30) / count(*)                    as share_short_term,
               count_if(ll18_status = 'unregistered_short_term')           as unregistered_short_term,
               avg(availability_30) / 30.0                                 as availability_30d
        from gold.fct_listing_snapshot where snapshot_date = ?""",
        [snapshot_date],
    ).iloc[0]


def borough_trend() -> pd.DataFrame:
    return query(
        """
        select snapshot_date, borough,
               sum(active_listings)                                         as active_listings,
               median(median_30_night_rate_usd)                             as median_30_night_rate_usd,
               sum(avg_availability_rate_30d * active_listings) / sum(active_listings) as availability_rate_30d
        from gold.mart_neighbourhood_snapshot group by all order by 1, 2""",
    )


def neighbourhoods(snapshot_date) -> pd.DataFrame:
    return query(
        """
        select borough, neighbourhood, active_listings, median_30_night_rate_usd,
               avg_availability_rate_30d, share_short_term, share_unregistered_short_term, avg_rating
        from gold.mart_neighbourhood_snapshot where snapshot_date = ? order by active_listings desc""",
        [snapshot_date],
    )


def neighbourhood_history(neighbourhood: str) -> pd.DataFrame:
    return query(
        """select * from gold.mart_neighbourhood_snapshot where neighbourhood = ? order by snapshot_date""",
        [neighbourhood],
    )


def review_activity(neighbourhood: str) -> pd.DataFrame:
    return query(
        "select review_month, reviews from gold.mart_review_activity where neighbourhood = ? order by 1",
        [neighbourhood],
    )


def ll18_status() -> pd.DataFrame:
    return query(
        "select snapshot_date, ll18_status, sum(listings) as listings "
        "from gold.mart_ll18_status group by all order by 1, 2"
    )


def ll18_transitions(to_snapshot) -> pd.DataFrame:
    return query(
        "select from_status, to_status, listings from gold.mart_ll18_transitions "
        "where to_snapshot_date = ? and from_status <> to_status order by listings desc",
        [to_snapshot],
    )


def ll18_hotspots(snapshot_date) -> pd.DataFrame:
    return query(
        """
        select borough, neighbourhood, active_listings,
               round(share_unregistered_short_term * active_listings) as unregistered_short_term,
               share_unregistered_short_term
        from gold.mart_neighbourhood_snapshot
        where snapshot_date = ? and share_unregistered_short_term > 0
        order by unregistered_short_term desc limit 15""",
        [snapshot_date],
    )


def shared_registrations(snapshot_date) -> pd.DataFrame:
    return query(
        """select registration_number, listings, hosts, neighbourhoods, boroughs, min_price_usd, max_price_usd
           from gold.mart_shared_registrations where snapshot_date = ? order by hosts desc, listings desc""",
        [snapshot_date],
    )


def map_points(snapshot_date) -> pd.DataFrame:
    return query(
        """select f.listing_id, s.latitude, s.longitude, f.ll18_status, f.room_type, f.price_usd, f.neighbourhood
           from gold.fct_listing_snapshot f
           join silver.stg_listings s using (listing_id, snapshot_date)
           where f.snapshot_date = ?""",
        [snapshot_date],
    )


def booking_pace(borough: str | None) -> pd.DataFrame:
    where = "where borough = ?" if borough else ""
    return query(
        f"""select snapshot_date, stay_month, sum(available_nights) / sum(calendar_nights) as open_night_rate
            from gold.mart_booking_pace {where} group by all order by 1, 2""",
        [borough] if borough else [],
    )


def lifecycle() -> pd.DataFrame:
    return query("select * from gold.mart_listing_lifecycle order by snapshot_date")


def price_changes() -> pd.DataFrame:
    return query(
        """select observed_on, borough,
                  count(*) as changes, count_if(change_pct > 0) as increases, count_if(change_pct < 0) as decreases,
                  median(change_pct) as median_change_pct
           from gold.mart_price_changes group by all order by 1"""
    )


def listing_history(listing_id: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    versions = query(
        """select version_number, valid_from, valid_to, room_type, minimum_nights, license, ll18_status,
                  host_id, accommodates, listing_name
           from gold.dim_listing where listing_id = ? order by valid_from""",
        [listing_id],
    )
    facts = query(
        """select snapshot_date, price_usd, quote_nights, availability_30, number_of_reviews, ll18_status
           from gold.fct_listing_snapshot where listing_id = ? order by snapshot_date""",
        [listing_id],
    )
    return versions, facts


def example_listing() -> int:
    """The listing with the most SCD2 versions (lowest id on ties), to pre-fill the lookup."""
    sql = "select listing_id from gold.dim_listing group by 1 order by count(*) desc, 1 limit 1"
    return int(query(sql).iloc[0, 0])


def data_quality() -> pd.DataFrame:
    return query("select * from gold.mart_data_quality order by dataset, snapshot_date")


def test_history() -> pd.DataFrame:
    # dbt test results are recorded locally by pipeline.py for either target.
    path = WAREHOUSE.parent / "quality" / "test_history.jsonl"
    if not path.exists():
        return pd.DataFrame()
    return pd.read_json(path, lines=True)
