-- Price changes between consecutive accepted snapshots of the same listing, like-for-like:
-- both prices must be quotes for the same stay length (quote_nights).
with snapshots as (
    select snapshot_date, row_number() over (order by snapshot_date) as snapshot_idx
    from (select distinct snapshot_date from {{ ref('fct_listing_snapshot') }})
),

priced as (
    select
        f.listing_id, f.snapshot_date, s.snapshot_idx, f.borough, f.neighbourhood, f.room_type,
        f.ll18_status, f.price_usd, f.quote_nights,
        lag(f.price_usd)    over w as previous_price_usd,
        lag(f.quote_nights) over w as previous_quote_nights,
        lag(s.snapshot_idx) over w as previous_idx
    from {{ ref('fct_listing_snapshot') }} as f
    inner join snapshots as s using (snapshot_date)
    where f.price_usd is not null
    window w as (partition by f.listing_id order by s.snapshot_idx)
)

select
    listing_id,
    snapshot_date                                         as observed_on,
    borough,
    neighbourhood,
    room_type,
    ll18_status,
    quote_nights,
    previous_price_usd,
    price_usd                                             as new_price_usd,
    price_usd - previous_price_usd                        as change_usd,
    (price_usd - previous_price_usd) / previous_price_usd as change_pct
from priced
where previous_idx = snapshot_idx - 1          -- consecutive snapshots only
  and previous_quote_nights = quote_nights     -- same quoted stay length
  and price_usd <> previous_price_usd
