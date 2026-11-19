-- Booking pace: how the open share of a future stay month shrinks as that month approaches.
-- Unavailable nights include host blocks, so this is an upper bound on bookings, not a count of them.
select
    c.snapshot_date,
    l.borough,
    c.stay_month,
    {{ dbt.datediff("cast(date_trunc('month', c.snapshot_date) as date)", 'c.stay_month', 'month') }} as months_ahead,
    count(distinct c.listing_id)                                          as listings,
    sum(c.calendar_nights)                                                as calendar_nights,
    sum(c.available_nights)                                               as available_nights,
    sum(c.available_nights) / sum(c.calendar_nights)                      as open_night_rate
from {{ ref('stg_calendar_monthly') }} as c
inner join {{ ref('fct_listing_snapshot') }} as l
    on c.listing_id = l.listing_id and c.snapshot_date = l.snapshot_date
group by 1, 2, 3, 4
