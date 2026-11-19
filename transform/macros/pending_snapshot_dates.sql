{#- Accepted listings snapshots newer than the last one replayed into SCD2. Used by the pipeline. -#}
{% macro pending_snapshot_dates() %}
    {% set scd2 = adapter.get_relation(database=target.database, schema='silver', identifier='listings_scd2') %}
    {% set sql %}
        select distinct cast(snapshot_date as varchar) as d from {{ ref('stg_listings') }}
        {% if scd2 %} where snapshot_date > (select coalesce(max(cast(dbt_updated_at as date)), date '1900-01-01') from {{ scd2 }}) {% endif %}
        order by 1
    {% endset %}
    {% set dates = run_query(sql).columns[0].values() | list %}
    {{ print('PENDING_DATES=' ~ (dates | join(','))) }}
{% endmacro %}

{#- Full refresh: SCD2 history can only be rebuilt by replaying every snapshot from an empty table. -#}
{% macro drop_listings_scd2() %}
    {% set rel = adapter.get_relation(database=target.database, schema='silver', identifier='listings_scd2') %}
    {% if rel %}
        {% do adapter.drop_relation(rel) %}
        {% do adapter.commit() %}  {#- run-operation does not commit on its own -#}
        {{ print('dropped ' ~ rel) }}
    {% endif %}
{% endmacro %}
