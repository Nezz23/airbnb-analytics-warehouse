-- Cost guardrails. Run FIRST, as ACCOUNTADMIN (resource monitors need it). Not yet executed.
--
-- A resource monitor is the only hard spend cap Snowflake offers: at 100% of the quota it suspends
-- the warehouse, and queries cannot resume it until the next interval or a quota raise.
-- 5 credits/month comfortably covers this project (a full rebuild is minutes on XS = 1 credit/hour).

use role accountadmin;

create resource monitor if not exists airbnb_monthly_cap
  with credit_quota = 5
       frequency = monthly
       start_timestamp = immediately
  triggers on 50 percent do notify
           on 80 percent do notify
           on 100 percent do suspend            -- lets running statements finish
           on 110 percent do suspend_immediate; -- cancels them

-- Attach after 01_setup.sql has created the warehouse:
-- alter warehouse transforming set resource_monitor = airbnb_monthly_cap;
