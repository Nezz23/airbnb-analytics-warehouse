{{ config(materialized='incremental', unique_key=['listing_id', 'snapshot_date'], incremental_strategy='delete+insert') }}

-- Periodic snapshot fact: one row per listing per accepted snapshot, joined point-in-time
-- to the SCD2 version that was valid on that date.
select
    s.listing_id,
    s.snapshot_date,
    d.listing_sk,
    s.host_id,
    s.neighbourhood,
    s.borough,
    s.room_type,
    s.ll18_status,
    s.license,
    s.price_usd,
    s.quote_nights,
    s.quote_checkin_date,
    s.minimum_nights,
    s.availability_30,
    s.availability_90,
    s.availability_365,
    s.number_of_reviews,
    s.number_of_reviews_ltm,
    s.review_scores_rating,
    s.estimated_occupancy_l365d
from {{ ref('stg_listings') }} as s
left join {{ ref('dim_listing') }} as d
    on s.listing_id = d.listing_id
   and s.snapshot_date >= d.valid_from
   and (d.valid_to is null or s.snapshot_date < d.valid_to)
{% if is_incremental() %}
where s.snapshot_date not in (select distinct snapshot_date from {{ this }})
{% endif %}
