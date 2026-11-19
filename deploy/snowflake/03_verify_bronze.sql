-- Post-load reconciliation, run after 02_load_bronze.sql and before dbt. Not yet executed.
-- Every file lands in bronze (quarantined ones too), so for every manifest entry the loaded row
-- count must equal the count bronze ingestion measured locally. Any row returned is a failed load.

with loaded as (
    select 'listings' as dataset, snapshot_date, count(*) as loaded_rows from airbnb.bronze.listings group by 2
    union all
    select 'calendar', snapshot_date, count(*) from airbnb.bronze.calendar group by 2
    union all
    select 'reviews', snapshot_date, count(*) from airbnb.bronze.reviews group by 2
)
select m.dataset, m.snapshot_date, m.status, m."rows" as manifest_rows, l.loaded_rows
from airbnb.bronze.manifest as m
full outer join loaded as l on l.dataset = m.dataset and l.snapshot_date = m.snapshot_date
where l.loaded_rows is distinct from m."rows"
order by 1, 2;
