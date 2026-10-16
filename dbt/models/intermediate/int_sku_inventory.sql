-- int_sku_inventory: the inventory-relevant projection of stg_products.
--
-- GRAIN: one row per sku.
--   SPEC-001 Q4 is answered ("SKU is unique, so adopt one per row"), and
--   stg_products already enforces unique + not_null on sku, so this model
--   inherits that grain rather than re-establishing it.
--
-- WHAT THIS MODEL DOES, AND ONLY THIS:
--   1. projects the inventory-relevant columns of stg_products (stock on hand,
--      order/production quantities, units sold, availability).
--   2. keeps the provenance columns (source_batch_id, loaded_at) so any row
--      still traces to a load.
--
-- WHAT IT DELIBERATELY DOES NOT DO:
--   - no re-dedupe. "Latest load wins per sku" lives once, in stg_products.
--     Re-running that window here would create a second, independently
--     drifting definition of "current state per SKU" (ADR-002 risk R1).
--   - no re-cast. The TEXT-to-typed boundary lives once, in staging
--     (ADR-001 §1). availability is already integer here because
--     stg_products casts it, not because this model does.
--   - no joins, no aggregates, no derived measures (net position, coverage
--     ratio, reorder flags, etc.). Those belong in a later intermediate or a
--     gold mart, once a consumer asks for them and their definitions are
--     settled. Per ADR-001 §2, staging casts and trims; intermediate models;
--     this projection does neither yet.
--   - availability is carried as INTEGER (SPEC-001 Q7 published the dtype in
--     docs/data-dictionary.md), but its MEANING is still unconfirmed: a count
--     of available units versus a 0/1 flag is not decided. The type is known;
--     the semantics are not. Do not build a boolean predicate or a sum on top
--     of it until that is resolved.
--
-- This is an intermediate building block, NOT a published gold table. The
-- gold publish gate (gold-fct_sku_performance.contract.yaml) is still blocked,
-- dim_product does not exist yet, and no exposure consumes this model.

select
    -- key
    sku,

    -- dimensions that give an inventory row its context
    product_type,
    supplier_name,
    location,

    -- inventory measures
    stock_levels,               -- stock on hand
    order_quantities,           -- ordered quantity
    production_volumes,         -- production volume
    number_of_products_sold,    -- units sold
    availability,               -- integer per Q7; meaning (count vs flag) still open

    -- provenance, so any row traces back to a load and to bronze
    source_batch_id,
    source_row_id,
    loaded_at

from {{ ref('stg_products') }}