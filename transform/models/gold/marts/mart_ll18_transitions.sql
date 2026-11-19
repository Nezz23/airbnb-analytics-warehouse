-- How listings move between Local Law 18 statuses from one accepted snapshot to the next.
-- 'not_listed' marks a listing absent on that side of the pair.
with snapshots as (
    select
        snapshot_date,
        lag(snapshot_date) over (order by snapshot_date) as previous_snapshot_date
    from (select distinct snapshot_date from {{ ref('fct_listing_snapshot') }})
),

pairs as (
    select s.previous_snapshot_date, s.snapshot_date, f.listing_id, f.ll18_status
    from snapshots as s
    inner join {{ ref('fct_listing_snapshot') }} as f on f.snapshot_date = s.snapshot_date
    where s.previous_snapshot_date is not null
),

previous as (
    select s.previous_snapshot_date, s.snapshot_date, f.listing_id, f.ll18_status
    from snapshots as s
    inner join {{ ref('fct_listing_snapshot') }} as f on f.snapshot_date = s.previous_snapshot_date
)

select
    coalesce(c.previous_snapshot_date, p.previous_snapshot_date) as from_snapshot_date,
    coalesce(c.snapshot_date, p.snapshot_date)                   as to_snapshot_date,
    coalesce(p.ll18_status, 'not_listed')                        as from_status,
    coalesce(c.ll18_status, 'not_listed')                        as to_status,
    count(*)                                                     as listings
from pairs as c
full outer join previous as p
    on c.snapshot_date = p.snapshot_date and c.listing_id = p.listing_id
group by 1, 2, 3, 4
