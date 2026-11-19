{{ config(unique_key=['listing_id', 'snapshot_date', 'stay_month'], incremental_strategy='delete+insert') }}

-- Grain: listing x snapshot x calendar month of the stay night.
-- Day grain (~11M rows per snapshot) stays in bronze for replay; analytics only need monthly.
with accepted as (
    select snapshot_date
    from {{ ref('stg_bronze_manifest') }}
    where dataset = 'calendar' and status = 'accepted'
    {% if is_incremental() %}
      and snapshot_date not in (select distinct snapshot_date from {{ this }})
    {% endif %}
)

select
    try_cast(c.listing_id as bigint)                     as listing_id,
    c.snapshot_date,
    cast(date_trunc('month', try_cast(c.date as date)) as date) as stay_month,
    count(*)                                             as calendar_nights,
    count_if(c.available = 't')                          as available_nights,
    avg(try_cast(c.minimum_nights as integer))           as avg_minimum_nights
from {{ source('bronze', 'calendar') }} as c
inner join accepted as a on c.snapshot_date = a.snapshot_date
group by 1, 2, 3
