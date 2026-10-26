{#- Schémas silver / gold tels quels, au lieu de public_silver / public_gold (comportement par défaut de dbt). -#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {{ custom_schema_name | trim if custom_schema_name is not none else target.schema }}
{%- endmacro %}
