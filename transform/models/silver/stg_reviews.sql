{{ config(unique_key='review_id', incremental_strategy='delete+insert') }}

-- The source review log is cumulative: each snapshot repeats every earlier review.
-- Keep one row per review, taken from the newest accepted snapshot that carries it.
with accepted as (
    select snapshot_date
    from {{ ref('stg_bronze_manifest') }}
    where dataset = 'reviews' and status = 'accepted'
    {% if is_incremental() %}
      and snapshot_date > (select max(source_snapshot_date) from {{ this }})
    {% endif %}
)

select
    try_cast(r.id as bigint)          as review_id,
    try_cast(r.listing_id as bigint)  as listing_id,
    try_cast(r.date as date)          as review_date,
    r.snapshot_date                   as source_snapshot_date
from {{ source('bronze', 'reviews') }} as r
inner join accepted as a on r.snapshot_date = a.snapshot_date
qualify row_number() over (partition by try_cast(r.id as bigint) order by r.snapshot_date desc) = 1
