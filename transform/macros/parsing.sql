{#- Inside Airbnb money text ("$1,234.00") -> number. DuckDB needs the 'g' flag; Snowflake replaces all by default. -#}
{% macro parse_money(column) -%}
    {{ return(adapter.dispatch('parse_money', 'airbnb_warehouse')(column)) }}
{%- endmacro %}

{% macro default__parse_money(column) -%}
    try_cast(regexp_replace({{ column }}, '[$,]', '') as double)
{%- endmacro %}

{% macro duckdb__parse_money(column) -%}
    try_cast(regexp_replace({{ column }}, '[$,]', '', 'g') as double)
{%- endmacro %}

{#- Inside Airbnb booleans are 't' / 'f'. -#}
{% macro to_bool(column) -%}
    case {{ column }} when 't' then true when 'f' then false end
{%- endmacro %}

{#- Percent text ("93%") -> 0.93. -#}
{% macro parse_pct(column) -%}
    try_cast(replace({{ column }}, '%', '') as double) / 100
{%- endmacro %}

{#- Element count of an array column. -#}
{% macro array_length(column) -%}
    {{ return(adapter.dispatch('array_length', 'airbnb_warehouse')(column)) }}
{%- endmacro %}

{% macro default__array_length(column) -%} array_size({{ column }}) {%- endmacro %}
{% macro duckdb__array_length(column) -%} len({{ column }}) {%- endmacro %}
