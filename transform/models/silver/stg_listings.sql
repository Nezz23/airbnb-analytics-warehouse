{{ config(unique_key=['listing_id', 'snapshot_date'], incremental_strategy='delete+insert') }}

-- One typed row per listing per accepted snapshot. Snapshots the contract quarantined never enter.
-- Incremental on snapshot identity (not a high-water mark), so a late, older snapshot still loads.
with accepted as (
    select snapshot_date
    from {{ ref('stg_bronze_manifest') }}
    where dataset = 'listings' and status = 'accepted'
    {% if is_incremental() %}
      and snapshot_date not in (select distinct snapshot_date from {{ this }})
    {% endif %}
),

typed as (
    select
        try_cast(l.id as bigint)                          as listing_id,
        l.snapshot_date,
        try_cast(l.last_scraped as date)                  as last_scraped,
        l.name                                            as listing_name,
        try_cast(l.host_id as bigint)                     as host_id,
        try_cast(l.host_since as date)                    as host_since,
        {{ to_bool('l.host_is_superhost') }}              as host_is_superhost,
        try_cast(l.calculated_host_listings_count as integer) as host_listings_count,
        l.neighbourhood_cleansed                          as neighbourhood,
        l.neighbourhood_group_cleansed                    as borough,
        try_cast(l.latitude as double)                    as latitude,
        try_cast(l.longitude as double)                   as longitude,
        l.property_type,
        l.room_type,
        try_cast(l.accommodates as integer)               as accommodates,
        try_cast(l.bedrooms as integer)                   as bedrooms,
        try_cast(l.beds as integer)                       as beds,
        -- `price` is the nightly rate of one quoted stay (check-in date and length vary per listing),
        -- so it is a measure to compare like-for-like on quote_nights, not a list price.
        {{ parse_money('l.price') }}                      as price_usd,
        try_cast(l.price_quote_checkin_date as date)      as quote_checkin_date,
        {{ dbt.datediff('try_cast(l.price_quote_checkin_date as date)', 'try_cast(l.price_quote_checkout_date as date)', 'day') }} as quote_nights,
        try_cast(l.minimum_nights as integer)             as minimum_nights,
        try_cast(l.maximum_nights as integer)             as maximum_nights,
        try_cast(l.availability_30 as integer)            as availability_30,
        try_cast(l.availability_90 as integer)            as availability_90,
        try_cast(l.availability_365 as integer)           as availability_365,
        try_cast(l.number_of_reviews as integer)          as number_of_reviews,
        try_cast(l.number_of_reviews_ltm as integer)      as number_of_reviews_ltm,
        try_cast(l.review_scores_rating as double)        as review_scores_rating,
        try_cast(l.estimated_occupancy_l365d as integer)  as estimated_occupancy_l365d,
        {{ to_bool('l.instant_bookable') }}               as instant_bookable,
        -- Hosts type registration numbers by hand: 'Ose-strreg-0001075' is a registration.
        nullif(upper(trim(l.license)), '')                as license
    from {{ source('bronze', 'listings') }} as l
    inner join accepted as a on l.snapshot_date = a.snapshot_date
)

select
    *,
    {{ ll18_status('license', 'minimum_nights') }} as ll18_status
from typed
