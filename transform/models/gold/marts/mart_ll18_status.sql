-- Local Law 18 status mix per borough per snapshot (see macros/compliance.sql for the caveat).
select
    snapshot_date,
    borough,
    ll18_status,
    count(*)                    as listings,
    count(distinct host_id)     as hosts,
    median(price_usd)           as median_price_usd,
    avg(availability_30) / 30.0 as avg_availability_rate_30d
from {{ ref('fct_listing_snapshot') }}
group by 1, 2, 3
