-- SCD2 listing dimension. A version is valid on dates in [valid_from, valid_to); valid_to is null while current.
select
    dbt_scd_id                       as listing_sk,
    listing_id,
    listing_name, host_id, neighbourhood, borough, property_type, room_type,
    accommodates, bedrooms, beds, minimum_nights, maximum_nights,
    instant_bookable, license, ll18_status,
    cast(dbt_valid_from as date)     as valid_from,
    cast(dbt_valid_to as date)       as valid_to,
    dbt_valid_to is null             as is_current,
    row_number() over (partition by listing_id order by dbt_valid_from) as version_number
from {{ ref('listings_scd2') }}
