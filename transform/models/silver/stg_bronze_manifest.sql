{{ config(materialized='view') }}

select
    cast(dataset as varchar)          as dataset,
    cast(snapshot_date as date)       as snapshot_date,
    cast({{ adapter.quote('rows') }} as bigint) as row_count,  -- ROWS is reserved on Snowflake
    cast(status as varchar)           as status,
    violations,
    cast(file_sha256 as varchar)      as file_sha256,
    cast(contract_sha256 as varchar)  as contract_sha256,
    cast(ingested_at as timestamp)    as ingested_at
from {{ source('bronze', 'manifest') }}
