-- Supply churn per snapshot: listings that appeared, returned after a gap, or disappeared.
with snapshots as (
    select snapshot_date, row_number() over (order by snapshot_date) as snapshot_idx
    from (select distinct snapshot_date from {{ ref('fct_listing_snapshot') }})
),

presence as (
    select
        f.listing_id,
        s.snapshot_idx,
        lag(s.snapshot_idx)  over (partition by f.listing_id order by s.snapshot_idx) as prev_idx,
        lead(s.snapshot_idx) over (partition by f.listing_id order by s.snapshot_idx) as next_idx
    from {{ ref('fct_listing_snapshot') }} as f
    inner join snapshots as s using (snapshot_date)
),

events as (
    select snapshot_idx, case when prev_idx is null then 'new' else 'relisted' end as event
    from presence
    where snapshot_idx > 1 and (prev_idx is null or prev_idx < snapshot_idx - 1)
    union all
    select snapshot_idx + 1, 'delisted'
    from presence
    where snapshot_idx < (select max(snapshot_idx) from snapshots)
      and (next_idx is null or next_idx > snapshot_idx + 1)
)

select
    s.snapshot_date,
    count_if(e.event = 'new')       as new_listings,
    count_if(e.event = 'relisted')  as relisted_listings,
    count_if(e.event = 'delisted')  as delisted_listings,
    (select count(*) from {{ ref('fct_listing_snapshot') }} f where f.snapshot_date = s.snapshot_date) as active_listings
from snapshots as s
left join events as e using (snapshot_idx)
where s.snapshot_idx > 1
group by s.snapshot_date
