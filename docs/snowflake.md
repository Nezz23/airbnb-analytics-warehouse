# Snowflake runbook (not yet executed)

Everything below is prepared but **has not been run**: it needs a Snowflake account and your approval for any spend.
Locally, the same dbt project runs on DuckDB.

## 1. One-time setup (role with CREATE DATABASE)

```sql
create warehouse if not exists transforming warehouse_size = xsmall auto_suspend = 60 auto_resume = true
  initially_suspended = true;                -- XS + 60 s auto-suspend keeps idle cost at zero
create database if not exists airbnb;
create schema if not exists airbnb.bronze;
create role if not exists transformer;
grant usage on warehouse transforming to role transformer;
grant all on database airbnb to role transformer;
create file format if not exists airbnb.bronze.parquet_ff type = parquet;
create stage if not exists airbnb.bronze.landing file_format = airbnb.bronze.parquet_ff;
```

## 2. Load bronze (after `python -m ingestion.bronze` has produced Parquet locally)

```sql
-- From SnowSQL / snow CLI, per dataset:
put file://data/bronze/listings/*/*.parquet @airbnb.bronze.landing/listings/ auto_compress = false;

create table if not exists airbnb.bronze.listings using template (
  select array_agg(object_construct(*)) from table(infer_schema(
    location => '@airbnb.bronze.landing/listings/', file_format => 'airbnb.bronze.parquet_ff')));
copy into airbnb.bronze.listings from @airbnb.bronze.landing/listings/
  match_by_column_name = case_insensitive file_format = (type = parquet);
```

Repeat for `calendar` and `reviews`, and load `_manifest.jsonl` into `airbnb.bronze.manifest`
(JSON file format, one row per line). Bronze Parquet carries `snapshot_date` as a column
(`_snapshot_date`), so hive partition paths are not needed on Snowflake; map it to `snapshot_date`
in the COPY or with a view.

## 3. Run dbt against Snowflake

```bash
uv add --optional snowflake dbt-snowflake
export DBT_TARGET=snowflake SNOWFLAKE_ACCOUNT=... SNOWFLAKE_USER=... SNOWFLAKE_PRIVATE_KEY_PATH=...
# replay SCD2 then build, exactly as pipeline.py does locally
```

`pipeline.py` currently also runs bronze ingestion into the local warehouse; for Snowflake, run the
dbt steps after loading bronze. Portability points already in the project: `parse_money` and
`array_length` dispatch per adapter, date math uses `dbt.datediff`, and the SCD2 replay timestamp
override exists for both `duckdb__` and `snowflake__snapshot_get_time`.

## Expected cost

Data volume is small (bronze is ~270 MB). An XS warehouse bills 1 credit/hour while running, per
second with a 60 s minimum; a full build should take minutes. Check your contract's credit price
before running.
