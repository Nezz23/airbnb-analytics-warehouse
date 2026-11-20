# Snowflake deployment runbook (deployed and validated 2026-11-20)

Deployed to a Snowflake trial account on 2026-11-20; results are in the README (section 9). Every step that creates objects or uses
compute is listed explicitly so it can be approved before any spend. Locally, the same dbt project
runs on DuckDB, and CI checks Snowflake readiness offline (see [What is verified](#what-is-verified-offline)).

## Cost model and guardrails

| Lever | Setting | Where |
|---|---|---|
| Hard spend cap | Resource monitor, 5 credits/month, suspends the warehouse at 100% | `deploy/snowflake/00_cost_guardrails.sql` |
| Compute size | One XS warehouse (1 credit/hour, billed per second, 60 s minimum per resume) | `01_setup.sql` |
| Idle cost | `auto_suspend = 60`, `initially_suspended = true` | `01_setup.sql` |
| Runaway queries | `statement_timeout_in_seconds = 900` on the warehouse; 60 s for dashboard sessions | `01_setup.sql`, `dashboard/backends.py` |
| Session leaks | `client_session_keep_alive: false` in the dbt profile | `transform/profiles.yml` |
| Attribution | `query_tag` = `airbnb_dbt` / `airbnb_dashboard` | profile, dashboard backend |
| Dashboard reads | `st.cache_data(ttl=600)`: repeated views do not re-query | `dashboard/app.py` |
| Storage | ~270 MB of bronze Parquet; storage is billed monthly per TB, so negligible here | n/a |
| Cleanup | `99_teardown.sql` (destructive, every line commented out) | `deploy/snowflake/` |

Expected compute: a full rebuild (10 snapshots: load, SCD2 replay, build, tests) should take a few
minutes on XS, which is a fraction of a credit. Check your contract's price per credit. A trial account's
free credits cover this many times over. Measure the first real run with:

```sql
select query_tag, count(*) as queries, sum(total_elapsed_time) / 1000 as seconds
from table(airbnb.information_schema.query_history(result_limit => 10000))
where query_tag like 'airbnb_%' group by 1;
```

## Prerequisites (local, free)

```bash
uv sync --extra snowflake          # dbt-snowflake + snowflake-connector-python (optional extra)
brew install snowflake-cli          # `snow` CLI, used for PUT (client-side upload)
```

Credentials are read from the environment only. Never commit them; `.gitignore` excludes `.env*`
and key files. Key-pair authentication (no passwords):

| Variable | Used by | Default |
|---|---|---|
| `DBT_TARGET=snowflake` | dbt / pipeline | `dev` (DuckDB) |
| `SNOWFLAKE_ACCOUNT` | dbt, dashboard | required |
| `SNOWFLAKE_USER` | dbt, dashboard | required |
| `SNOWFLAKE_PRIVATE_KEY_PATH` | dbt, dashboard | required (path to a `.p8` outside the repo) |
| `SNOWFLAKE_ROLE` | dbt: `AIRBNB_TRANSFORMER`; dashboard: `AIRBNB_REPORTER` | per tool |
| `SNOWFLAKE_WAREHOUSE` | both | `TRANSFORMING` |
| `SNOWFLAKE_DATABASE` | both | `AIRBNB` |
| `AIRBNB_WAREHOUSE_BACKEND=snowflake` | dashboard | `duckdb` |

## Steps (each needs approval: they create objects or use compute)

1. **Guardrails** (ACCOUNTADMIN): `snow sql -f deploy/snowflake/00_cost_guardrails.sql`
2. **Setup** (ACCOUNTADMIN, or SYSADMIN + SECURITYADMIN): `snow sql -f deploy/snowflake/01_setup.sql`,
   then create the two service users at the bottom of that file with your public keys.
3. **Bronze, built locally** (free): `uv run python -m ingestion.bronze`
4. **Load bronze**:
   ```bash
   uv run python deploy/snowflake/render_load.py > /tmp/02_load_bronze.sql   # review it first
   snow sql -f /tmp/02_load_bronze.sql
   ```
   Incremental loads: `--since YYYY-MM-DD` reloads only those snapshots. Every load is delete plus
   forced COPY per snapshot, so rerunning never duplicates rows.
5. **Reconcile**: `snow sql -f deploy/snowflake/03_verify_bronze.sql` must return **no rows**
   (every manifest entry's row count equals what landed).
6. **Transform and test**:
   ```bash
   DBT_TARGET=snowflake SNOWFLAKE_ROLE=AIRBNB_TRANSFORMER uv run python pipeline.py --skip-bronze
   ```
   This runs exactly what runs locally: silver, SCD2 replay one snapshot at a time, gold, then every
   dbt data test and unit test, including `assert_scd2_reconciles_with_silver` and
   `assert_quarantined_snapshots_excluded`. Results go to `data/quality/test_history.jsonl`.
7. **Dashboard**:
   ```bash
   AIRBNB_WAREHOUSE_BACKEND=snowflake SNOWFLAKE_ROLE=AIRBNB_REPORTER uv run streamlit run dashboard/app.py
   ```
8. **Freshness**: `dbt source freshness --target snowflake` warns after 40 days without a new manifest entry.

## Layout on Snowflake

| Schema | Contents | Written by |
|---|---|---|
| `BRONZE_LANDING` | COPY targets (`LISTINGS`, `CALENDAR`, `REVIEWS`, `MANIFEST` as VARIANT), stage, file formats | `render_load.py` |
| `BRONZE` | Views exposing `snapshot_date` and typed manifest columns: the dbt `bronze` source | `render_load.py` |
| `SILVER` | Typed incremental staging and `LISTINGS_SCD2` | dbt |
| `GOLD` | Star schema and marts | dbt |

## Portability fixes made during the readiness audit

| Problem | Effect on Snowflake | Fix |
|---|---|---|
| The old runbook PUT `*/*.parquet` into one stage prefix; every bronze file is named `part-0.parquet` | Each snapshot overwrote the last: only one would load | One prefix per `(dataset, snapshot)` in `render_load.py` |
| `USING TEMPLATE … INFER_SCHEMA` without `IGNORE_CASE` | Columns created as quoted lowercase, so unquoted dbt SQL (`l.id`) fails to resolve | `ignore_case => true` |
| `stg_bronze_manifest` selected a bare `rows` column | `ROWS` is a reserved word: syntax error | `adapter.quote('rows')`, and the manifest view exposes `"rows"` |
| Bronze has `_snapshot_date`; locally `snapshot_date` comes from the hive path | dbt references `snapshot_date` | `BRONZE` views add `snapshot_date` |
| `pipeline.py` always ran local DuckDB bronze ingestion | Pointless for a Snowflake target, and easy to confuse with a load | `--skip-bronze`; a guard refuses `DBT_TARGET=snowflake` without it |
| Named `WINDOW w as (...)` clause in `mart_price_changes` (found on the first real build) | Not supported: the model failed to compile | Window written inline per column; the compatibility test now rejects named windows |

Already portable before the audit: `parse_money` and `array_length` dispatch per adapter, date math
uses `dbt.datediff`, the SCD2 replay timestamp override exists for `snowflake__snapshot_get_time`, and
`try_cast` is only applied to text (bronze keeps every source column as text, which Snowflake requires).
`qualify`, `count_if`, `median`, `is not distinct from`, `group by all` and `delete+insert` incremental
models are all supported on Snowflake.

## What is verified offline

`tests/test_snowflake_compat.py`, run in CI:

- `dbt parse` of the whole project with the **Snowflake adapter** (no connection);
- models, snapshots and singular tests contain no DuckDB-only SQL (dispatched macros are exempt);
- no Snowflake reserved word is used as a bare identifier;
- the rendered load script stages every snapshot under its own prefix, is idempotent (delete plus
  forced COPY), and uses `ignore_case`.

Not verifiable without an account: `dbt compile`/`build` (they introspect the target), COPY behavior
on real files, and query performance. Step 5 and the dbt tests in step 6 cover these on first run.

## Known limitations

- **Slow upload links:** PUT can stall on a slow connection (seen at ~1 Mbps upload). Rerunning is safe: each
  snapshot has its own stage prefix and is reloaded with delete plus forced COPY.

- **Schema drift:** the landing tables are templated from the oldest snapshot. A column that Inside
  Airbnb adds later is ignored by `match_by_column_name` until you `alter table … add column`. No drift
  exists across the current 10 snapshots (checked per dataset); `03_verify_bronze.sql` checks row
  counts, not columns.
- Data tests run on the warehouse, so a full `dbt build` on Snowflake uses warehouse time for tests as well.
