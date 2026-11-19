-- Type 1 host dimension: latest known attributes per host.
select
    host_id,
    host_since,
    host_is_superhost,
    host_listings_count,
    snapshot_date as last_seen_snapshot
from {{ ref('stg_listings') }}
qualify row_number() over (partition by host_id order by snapshot_date desc, listing_id) = 1
