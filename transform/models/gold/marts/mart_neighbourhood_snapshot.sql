-- Price, availability and supply per neighbourhood per snapshot.
select
    snapshot_date,
    borough,
    neighbourhood,
    count(*)                                                        as active_listings,
    count_if(room_type = 'Entire home/apt')                         as entire_home_listings,
    median(price_usd)                                               as median_price_usd,
    median(case when room_type = 'Entire home/apt' then price_usd end) as median_entire_home_price_usd,
    -- Like-for-like rate: only 30-night quotes, the most common quote length.
    median(case when quote_nights = 30 then price_usd end)          as median_30_night_rate_usd,
    count(price_usd) / count(*)                                     as share_with_price,
    avg(availability_30) / 30.0                                     as avg_availability_rate_30d,
    avg(availability_365) / 365.0                                   as avg_availability_rate_365d,
    count_if(minimum_nights < 30) / count(*)                        as share_short_term,
    count_if(ll18_status = 'unregistered_short_term') / count(*)    as share_unregistered_short_term,
    sum(number_of_reviews_ltm)                                      as reviews_last_12m,
    avg(review_scores_rating)                                       as avg_rating
from {{ ref('fct_listing_snapshot') }}
group by 1, 2, 3
