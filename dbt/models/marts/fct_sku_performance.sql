-- fct_sku_performance: current-state performance measures per SKU, published
-- for Power BI operational reporting and procurement analysis.
--
-- GRAIN: one row per sku (SPEC-001 Q4: SKU unique; asserted below as unique +
-- not_null). The source has no date column, so this is a current-state
-- snapshot of a static file, not a periodic one — it cannot express trend or
-- period-over-period until the source supplies dates (SPEC-001 A4).
--
-- SHAPE: denormalized — dimensions and measures on one row — exactly as
-- docs/contracts/datasets/gold-fct_sku_performance.contract.yaml specifies.
-- dim_product sits alongside as the labelling target of the contract's
-- relationships test. The strict-star alternative and its rejection are
-- recorded in ADR-003.
--
-- WHAT THIS MODEL DOES, AND ONLY THIS:
--   1. projects the contract's 24 columns from stg_products, in contract
--      order, so the file and the SQL can be read side by side.
--
-- WHAT IT DELIBERATELY DOES NOT DO:
--   - no re-cast, no re-dedupe. Both live once, in stg_products (ADR-001 §1,
--     ADR-002 R1).
--   - no joins. dim_product is built from the same silver rows, not joined in;
--     agreement between the two is a test, not a construction step.
--   - no customer_demographics. SPEC-001 Q6 answered (gender data): ADR-001 §6
--     excludes it from every published model, and its absence from this table
--     IS the exclusion — recorded as such in the contract.
--   - no derived measures. Whether revenue_generated is gross or net, and
--     whether costs includes shipping, are open (SPEC-001 §12); nothing is
--     recomputed, netted or re-labelled here. lead_times_1 and lead_times_2
--     stay separate (SPEC-001 Q3 open): do not sum or coalesce them.
--   - no boolean or aggregate reading of availability: the dtype is settled
--     (Q7) but the semantics — count versus flag — are not.
--
-- PUBLISH GATE: BLOCKED (owner unassigned, SPEC-001 draft, profiling not
-- done). Code complete is not publishable; see the contract's publish_gate
-- block and ADR-003.

select
    -- key
    sku,

    -- dimensions, in contract order
    product_type,
    supplier_name,
    shipping_carriers,
    transportation_modes,
    location,
    routes,

    -- measures. Types inherited from stg_products (docs/data-dictionary.md).
    -- revenue_generated: gross vs net UNDEFINED (SPEC-001 §12) — do not label
    --   this in a report until that is decided.
    -- costs: includes shipping? UNDEFINED (SPEC-001 §12) — passed through
    --   unmodified so the definition is decided once, not re-litigated.
    price,
    revenue_generated,
    costs,
    shipping_costs,
    manufacturing_costs,
    number_of_products_sold,
    order_quantities,
    stock_levels,
    production_volumes,

    -- defect_rates: range UNVERIFIED (0-100 or 0-1, SPEC-001 Q7). No range
    --   test is asserted against either candidate here; dbt/tests/
    --   assert_defect_rates_within_accepted_range.sql checks the superset
    --   0-100 at severity warn, which catches garbage without encoding the
    --   guess.
    defect_rates,

    -- the two colliding lead-time headers, kept separate (SPEC-001 Q3 open)
    lead_times_1,
    lead_times_2,

    manufacturing_lead_time,
    shipping_times,

    -- availability: integer per Q7; semantics (count vs flag) still open.
    -- inspection_results: stays text (Q7 typed it VARCHAR(20)); its
    --   pass/fail-versus-score meaning is still unconfirmed.
    availability,
    inspection_results,

    -- provenance: which delivery produced this row (contract schema carries
    -- source_batch_id only)
    source_batch_id

from {{ ref('stg_products') }}
