-- dim_product: the product dimension for gold — labels for the SKU-keyed
-- published datasets.
--
-- GRAIN: one row per sku. Inherited from stg_products (SPEC-001 Q4: SKU is
-- unique, unique + not_null asserted there) and restated in dim_product.yml so
-- the grain is enforced where this model is consumed.
--
-- WHAT THIS MODEL DOES, AND ONLY THIS:
--   1. projects the six text dimensions of stg_products plus the key.
--   2. carries source_batch_id, so a dimension row traces to a delivery the
--      same way a fact row does — provenance is carried through every layer.
--
-- WHAT IT DELIBERATELY DOES NOT DO:
--   - no measures. Measures live in fct_sku_performance (ADR-003).
--   - no re-cast, no re-dedupe. Both live once, in stg_products (ADR-001 §1,
--     ADR-002 R1); this model inherits them by referencing that model.
--   - no customer_demographics. SPEC-001 Q6 answered: it holds gender data and
--     is excluded from every published model (ADR-001 §6). A dimension is the
--     most-joined shape in the warehouse, which is exactly why PII must not
--     ride in it.
--   - no surrogate key. sku is the business key; a hash of it would be a
--     second key with no consumer (ADR-001 §7).
--
-- NO DATASET CONTRACT YET: the only consumer of this model today is the
-- relationships test inside the (blocked) fct_sku_performance gate. A contract
-- is written when a consumer other than that test appears — see ADR-003.

select
    -- key
    sku,

    -- labels. As delivered by the source: no category mapping, no supplier
    -- normalisation. Those are modelling decisions and belong downstream, when
    -- a consumer asks for them.
    product_type,
    supplier_name,
    location,
    shipping_carriers,
    transportation_modes,
    routes,

    -- provenance, so any dimension row traces to the delivery that produced it
    source_batch_id

from {{ ref('stg_products') }}
