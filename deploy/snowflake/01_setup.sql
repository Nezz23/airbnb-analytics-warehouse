-- One-time setup: warehouse, database, schemas, roles. Executed 2026-11-20.
-- Run as a role with CREATE WAREHOUSE / DATABASE / ROLE (SYSADMIN + SECURITYADMIN, or ACCOUNTADMIN).
-- Every statement is idempotent (`if not exists`), so re-running is safe.

-- Compute: one extra-small warehouse shared by dbt and the dashboard.
-- XS bills 1 credit/hour, per second, 60 s minimum per resume. Auto-suspend after 60 s idle means
-- an idle project costs nothing; initially_suspended means creating it costs nothing.
create warehouse if not exists transforming
  warehouse_size = xsmall
  auto_suspend = 60
  auto_resume = true
  initially_suspended = true
  statement_timeout_in_seconds = 900       -- a runaway query stops at 15 min, not at the quota
  comment = 'airbnb-analytics-warehouse: dbt builds and dashboard reads';
alter warehouse transforming set resource_monitor = airbnb_monthly_cap;  -- from 00_cost_guardrails.sql

create database if not exists airbnb;
-- bronze_landing: raw COPY targets. bronze: views with the column names dbt expects (02_load_bronze.sql).
-- silver / gold: built by dbt (generate_schema_name.sql maps +schema to these names verbatim).
create schema if not exists airbnb.bronze_landing;
create schema if not exists airbnb.bronze;
create schema if not exists airbnb.silver;
create schema if not exists airbnb.gold;
create schema if not exists airbnb.analytics;  -- profiles.yml default schema (dbt test audit, etc.)

-- Roles: AIRBNB_TRANSFORMER loads bronze and runs dbt; AIRBNB_REPORTER is read-only on gold for the dashboard.
create role if not exists airbnb_transformer;
create role if not exists airbnb_reporter;
grant role airbnb_transformer to role sysadmin;
grant role airbnb_reporter to role sysadmin;

grant usage on warehouse transforming to role airbnb_transformer;
grant usage on warehouse transforming to role airbnb_reporter;
grant usage, create schema on database airbnb to role airbnb_transformer;
grant all on all schemas in database airbnb to role airbnb_transformer;
grant all on future schemas in database airbnb to role airbnb_transformer;

grant usage on database airbnb to role airbnb_reporter;
grant usage on schema airbnb.gold to role airbnb_reporter;
grant usage on schema airbnb.silver to role airbnb_reporter;          -- dashboard map joins silver.stg_listings
grant select on future tables in schema airbnb.gold to role airbnb_reporter;
grant select on future views in schema airbnb.gold to role airbnb_reporter;
grant select on future tables in schema airbnb.silver to role airbnb_reporter;

-- Service users authenticate with key pairs (no passwords). Generate locally:
--   openssl genrsa 2048 | openssl pkcs8 -topk8 -inform PEM -out ~/.snowflake/airbnb_rsa.p8 -nocrypt
--   openssl rsa -in ~/.snowflake/airbnb_rsa.p8 -pubout -out ~/.snowflake/airbnb_rsa.pub
-- Keep the .p8 outside this repository. Paste the public key body (no header/footer lines) below.
-- create user if not exists airbnb_dbt type = service default_role = airbnb_transformer
--   default_warehouse = transforming rsa_public_key = '<public key>';
-- grant role airbnb_transformer to user airbnb_dbt;
-- create user if not exists airbnb_dashboard type = service default_role = airbnb_reporter
--   default_warehouse = transforming rsa_public_key = '<public key>';
-- grant role airbnb_reporter to user airbnb_dashboard;
