-- The SCD2 history must reproduce every accepted silver snapshot exactly: each (listing, snapshot)
-- resolves to one version whose tracked attributes match. This catches skipped or out-of-order
-- replays (fix: python pipeline.py --full-refresh).
with resolved as (
    select s.listing_id, s.snapshot_date, count(d.listing_sk) as versions,
           max(case when d.listing_name is not distinct from s.listing_name
                     and d.host_id = s.host_id
                     and d.minimum_nights is not distinct from s.minimum_nights
                     and d.license is not distinct from s.license
                     and d.room_type = s.room_type then 1 else 0 end) as attributes_match
    from {{ ref('stg_listings') }} as s
    left join {{ ref('dim_listing') }} as d
        on s.listing_id = d.listing_id
       and s.snapshot_date >= d.valid_from
       and (d.valid_to is null or s.snapshot_date < d.valid_to)
    group by 1, 2
)
select * from resolved where versions <> 1 or attributes_match = 0
