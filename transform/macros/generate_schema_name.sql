{#- Use the layer name (silver, gold) as the schema, not "<target_schema>_silver". -#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {{ custom_schema_name | trim if custom_schema_name else target.schema }}
{%- endmacro %}
