{{
  config(
    materialized='view',
    tags=['staging', 'silver']
  )
}}

-- stg_products: the product-dimension columns of supply_chain_data.csv.
--
-- GRAIN: one row per sku.
--   SPEC-001 Q4 was answered "SKU is unique", so one row per SKU is correct.
--   Because the source has no date column there is no valid_from, so this is a
--   type 1 overwrite keyed on sku, keeping source_batch_id for provenance
--   (SPEC-001 Q5). Nothing is deleted here; state is always recoverable from
--   bronze.
--
-- WHAT THIS MODEL DOES, AND ONLY THIS:
--   1. casts the TEXT bronze columns to their documented types
--   2. trims and normalises the text columns
--   3. picks the most recent successful load for each sku
--
-- WHAT IT DELIBERATELY DOES NOT DO:
--   - no joins, no aggregates, no derived measures. Those belong in intermediate.
--   - no null-dropping on non-key columns. A missing price stays null so the
--     count of missing prices is still observable downstream.
--   - no decision about lead_times_1 vs lead_times_2. Both are passed through
--     unchanged; collapsing them is SPEC-001 Q3 and is still open.
--
-- Cast failures are intentional, not a defect: a value that cannot be cast raises
-- an error rather than silently becoming a null measure, so a broken source is
-- visible instead of quietly understating revenue. Verified against Postgres 16.
--
-- IMPORTANT LIMITATION: this is "fails when evaluated", not "always fails".
-- Postgres can push a predicate past the cast and never evaluate it, so
-- `select count(*) ... where source_batch_id = 'x'` succeeds on a row whose price
-- is unparseable. Any check that must catch a bad value has to project the column
-- (for example count(price), not count(*)). Once the real file is profiled,
-- revisit whether the loader should reject unparseable numerics instead, which
-- would move the failure from dbt to where the row is first seen.

with source as (

    select * from {{ source('bronze', 'supply_chain_raw') }}

),

typed as (

    select
        -- provenance, carried through every layer so any row is traceable to a load
        bronze_row_id            as source_row_id,
        source_batch_id,
        source_identity,
        file_hash,
        row_number,
        loaded_at,

        -- key
        nullif(btrim(sku), '')                                   as sku,

        -- text dimensions: trim, and treat empty string as null so that
        -- "missing" and "literally empty" are not two different states
        nullif(btrim(product_type), '')                          as product_type,
        nullif(btrim(supplier_name), '')                         as supplier_name,
        nullif(btrim(location), '')                              as location,
        nullif(btrim(shipping_carriers), '')                     as shipping_carriers,
        nullif(btrim(transportation_modes), '')                  as transportation_modes,
        nullif(btrim(routes), '')                                as routes,

        -- encoded / partially-encoded columns.
        -- availability is now cast to integer: SPEC-001 Q7 was answered and
        -- docs/data-dictionary.md types it INTEGER. Note the type is known but
        -- the semantics are not -- a count of available units versus a 0/1 flag
        -- is still unconfirmed, so it is exposed as an integer without any
        -- boolean or aggregate interpretation applied here or downstream.
        -- inspection_results stays text: Q7 typed it VARCHAR(20), which needs
        -- no cast. Its pass/fail-versus-score meaning is still unconfirmed.
        nullif(btrim(availability), '')::integer                as availability,
        nullif(btrim(inspection_results), '')                    as inspection_results,

        -- customer_demographics is provisional PII (SPEC-001 Q6: holds gender data).
        -- It is surfaced here because staging must not silently drop columns, and
        -- excluded from gold. Do not join it into a published model.
        nullif(btrim(customer_demographics), '')                 as customer_demographics,

        -- measures. Types from docs/data-dictionary.md.
        nullif(btrim(price), '')::numeric(12, 2)                 as price,
        nullif(btrim(number_of_products_sold), '')::integer      as number_of_products_sold,
        nullif(btrim(revenue_generated), '')::numeric(14, 2)     as revenue_generated,
        nullif(btrim(stock_levels), '')::integer                 as stock_levels,
        nullif(btrim(order_quantities), '')::integer             as order_quantities,
        nullif(btrim(shipping_times), '')::integer               as shipping_times,
        nullif(btrim(shipping_costs), '')::numeric(12, 2)        as shipping_costs,
        nullif(btrim(production_volumes), '')::integer           as production_volumes,
        nullif(btrim(manufacturing_lead_time), '')::integer      as manufacturing_lead_time,
        nullif(btrim(manufacturing_costs), '')::numeric(12, 2)   as manufacturing_costs,

        -- the two colliding lead-time headers. Renamed to _1 / _2 so neither is
        -- lost; see SPEC-001 Q3.
        nullif(btrim(lead_times_1), '')::integer                 as lead_times_1,
        nullif(btrim(lead_times_2), '')::integer                 as lead_times_2,

        -- range unverified: 0-100 or 0-1? SPEC-001 Q7.
        nullif(btrim(defect_rates), '')::numeric(10, 4)          as defect_rates,

        -- definition undefined: does this include shipping? SPEC-001 section 12.
        -- Passed through unmodified so the definition can be decided once, in
        -- intermediate, rather than re-litigated at every consumer.
        nullif(btrim(costs), '')::numeric(14, 2)                 as costs

    from source

),

-- Latest load wins per sku. row_number breaks ties so the result is deterministic:
-- without a tiebreaker the "current" row would change between runs on identical
-- loaded_at values, which would make reconciliation flaky rather than wrong.
latest as (

    select
        *,
        row_number() over (
            partition by sku
            order by loaded_at desc, source_batch_id desc, source_row_id desc
        ) as recency_rank

    from typed
    where sku is not null

)

-- recency_rank is dropped here: it is a window function, not a column, and
-- leaving it in would imply the model carries history it does not carry.
-- No surrogate key is added either. sku is already the business key and, per
-- SPEC-001 Q4, unique; a hash of it would be a second key with no consumer.
select
    sku,
    product_type,
    supplier_name,
    location,
    shipping_carriers,
    transportation_modes,
    routes,
    availability,
    inspection_results,
    customer_demographics,
    price,
    number_of_products_sold,
    revenue_generated,
    stock_levels,
    order_quantities,
    shipping_times,
    shipping_costs,
    production_volumes,
    manufacturing_lead_time,
    manufacturing_costs,
    lead_times_1,
    lead_times_2,
    defect_rates,
    costs,
    source_batch_id,
    source_row_id,
    source_identity,
    file_hash,
    row_number,
    loaded_at

from latest
where recency_rank = 1

