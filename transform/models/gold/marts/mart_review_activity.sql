-- Monthly review counts per neighbourhood: a demand proxy with years of history, far longer than
-- the snapshot window. Each listing is placed in the neighbourhood of its latest accepted snapshot.
with listing_place as (
    select listing_id, borough, neighbourhood
    from {{ ref('fct_listing_snapshot') }}
    qualify row_number() over (partition by listing_id order by snapshot_date desc) = 1
)

select
    cast(date_trunc('month', r.review_date) as date) as review_month,
    p.borough,
    p.neighbourhood,
    count(*)                                         as reviews,
    count(distinct r.listing_id)                     as reviewed_listings
from {{ ref('stg_reviews') }} as r
inner join listing_place as p using (listing_id)
group by 1, 2, 3
