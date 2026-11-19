-- No quarantined snapshot may reach silver or gold.
select f.snapshot_date
from {{ ref('fct_listing_snapshot') }} as f
inner join {{ ref('stg_bronze_manifest') }} as m
    on m.dataset = 'listings' and m.snapshot_date = f.snapshot_date
where m.status <> 'accepted'
