{#
  dbt's default generate_schema_name concatenates the target schema with the
  custom schema, which would turn +schema: silver into "dbt_silver" and split the
  bronze/silver/gold schemas that 001_create_schemas.sql already created.

  This override uses the custom schema as-is, so models land in exactly
  bronze / silver / gold.
#}
{% macro generate_schema_name(custom_schema_name, node) -%}

    {%- set default_schema = target.schema -%}

    {%- if custom_schema_name is none -%}
        {{ default_schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}

{%- endmacro %}
