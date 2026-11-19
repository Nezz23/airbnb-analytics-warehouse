{#-
  Historical replay: dbt stamps SCD2 boundaries (including hard deletes) with snapshot_get_time().
  When the pipeline replays a listings snapshot it passes --vars snapshot_date, and every
  boundary then lands on that real snapshot date instead of the wall-clock run time.
-#}
{% macro as_of_timestamp() -%}
    {%- set as_of = var('snapshot_date') -%}
    {%- if as_of -%} cast('{{ as_of }}' as timestamp) {%- else -%} {{ current_timestamp() }} {%- endif -%}
{%- endmacro %}

{% macro duckdb__snapshot_get_time() -%} {{ as_of_timestamp() }} {%- endmacro %}
{% macro snowflake__snapshot_get_time() -%} {{ as_of_timestamp() }} {%- endmacro %}
