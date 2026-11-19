{% snapshot listings_scd2 %}
{#-
  SCD Type 2 history of listing attributes, built by replaying each accepted snapshot in date order:
      dbt snapshot --vars '{snapshot_date: 2026-03-16}'   (the pipeline does this for you)
  Tracks descriptive attributes only; price and availability are measures in fct_listing_snapshot.
  bedrooms/beds are carried but not tracked: the source fills them inconsistently between scrapes
  (e.g. beds null for 31k listings on 2026-04-14 only), which would create false versions.
  Boundaries land on real snapshot dates (see macros/snapshot_as_of.sql). A listing missing from a
  snapshot is closed on that date; if it returns, it opens a new version.
  tests/assert_scd2_reconciles_with_silver.sql proves the history matches every silver snapshot.
-#}
{{ config(
    unique_key='listing_id',
    strategy='check',
    check_cols=[
        'listing_name', 'host_id', 'neighbourhood', 'borough', 'property_type', 'room_type',
        'accommodates', 'minimum_nights', 'maximum_nights',
        'instant_bookable', 'license', 'll18_status'
    ],
    updated_at='snapshot_ts',
    hard_deletes='invalidate',
    target_schema='silver',
) }}

{%- set as_of = var('snapshot_date') -%}
{%- if execute and not as_of and flags.WHICH in ('snapshot', 'build') -%}
    {{ exceptions.raise_compiler_error(
        "listings_scd2 replays one snapshot at a time: run `python -m pipeline` "
        "or pass --vars '{snapshot_date: YYYY-MM-DD}'") }}
{%- endif %}

select
    listing_id, listing_name, host_id, neighbourhood, borough, property_type, room_type,
    accommodates, bedrooms, beds, minimum_nights, maximum_nights, instant_bookable,
    license, ll18_status,
    cast(snapshot_date as timestamp) as snapshot_ts
from {{ ref('stg_listings') }}
where snapshot_date = cast('{{ as_of or "1900-01-01" }}' as date)

{% endsnapshot %}
