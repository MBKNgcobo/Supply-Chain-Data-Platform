-- Reconciliation (contract quality check: "count(gold) ==
-- count(distinct sku in silver for source_batch_id)").
--
-- WHAT IT PROVES: fct_sku_performance is a straight projection of
-- stg_products, so every silver row must appear in gold exactly once, and the
-- rows must agree per source_batch_id — the delivery each row cites. Catches
-- the two failure modes a uniqueness test cannot: gold silently dropping rows
-- (a filter added later), and fan-out (a join added later). Either way the
-- per-batch counts diverge and this test returns rows.
--
-- FAILS WHEN: it returns one or more rows. Passes trivially on empty tables —
-- the standing caveat from ADR-001: a test over an empty warehouse proves
-- nothing until bronze is populated.
--
-- "silver" here means stg_products, which is already de-duplicated to one row
-- per sku (ADR-001 §3), so per-batch equality with gold is exact: gold neither
-- adds, drops, nor re-batches a row.

with gold as (

    select
        source_batch_id,
        count(*) as row_count

    from {{ ref('fct_sku_performance') }}
    group by 1

),

silver as (

    select
        source_batch_id,
        count(*) as row_count

    from {{ ref('stg_products') }}
    group by 1

)

select
    coalesce(gold.source_batch_id, silver.source_batch_id) as source_batch_id,
    coalesce(gold.row_count, 0)                            as gold_rows,
    coalesce(silver.row_count, 0)                          as silver_rows

from gold
full outer join silver
    on gold.source_batch_id = silver.source_batch_id

where coalesce(gold.row_count, 0) != coalesce(silver.row_count, 0)
