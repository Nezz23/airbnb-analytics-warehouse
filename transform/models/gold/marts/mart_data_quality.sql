-- Data-quality scorecard per dataset per snapshot: contract outcome plus field completeness.
with completeness as (
    select
        snapshot_date,
        count(*)                                     as listings,
        count(price_usd) / count(*)                  as price_completeness,
        count(minimum_nights) / count(*)             as minimum_nights_completeness,
        count(review_scores_rating) / count(*)       as rating_completeness,
        count(license) / count(*)                    as license_completeness,
        count(beds) / count(*)                       as beds_completeness,
        count(bedrooms) / count(*)                   as bedrooms_completeness
    from {{ ref('stg_listings') }}
    group by 1
)

select
    m.dataset,
    m.snapshot_date,
    m.row_count,
    m.status,
    {{ array_length('m.violations') }} as violation_count,
    m.violations,
    m.ingested_at,
    c.price_completeness,
    c.minimum_nights_completeness,
    c.rating_completeness,
    c.license_completeness,
    c.beds_completeness,
    c.bedrooms_completeness
from {{ ref('stg_bronze_manifest') }} as m
left join completeness as c
    on m.dataset = 'listings' and m.snapshot_date = c.snapshot_date
