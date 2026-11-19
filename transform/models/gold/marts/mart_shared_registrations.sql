-- Registration numbers displayed on more than one listing in the same snapshot.
-- One registration covers one unit, so reuse (especially across hosts) is worth a closer look.
select
    snapshot_date,
    license                                   as registration_number,
    count(*)                                  as listings,
    count(distinct host_id)                   as hosts,
    count(distinct neighbourhood)             as neighbourhoods,
    count(distinct borough)                   as boroughs,
    min(price_usd)                            as min_price_usd,
    max(price_usd)                            as max_price_usd
from {{ ref('fct_listing_snapshot') }}
where ll18_status = 'registered'
group by 1, 2
having count(*) > 1
