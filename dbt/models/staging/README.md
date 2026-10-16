# Staging layer

Staging is **one model per source relation**, not one per subject area.
There is exactly one source (`bronze.supply_chain_raw`, declared in
`sources.yml`), so `stg_products.sql` is the sole staging model.

See [`docs/decisions/ADR-002-staging-is-one-model-per-source.md`](../../../docs/decisions/ADR-002-staging-is-one-model-per-source.md)
and [`ADR-001`](../../../docs/decisions/ADR-001-staging-layering.md) (staging
"casts and trims; it does not model").

## Why there are no `stg_inventory` / `stg_sales_orders` / `stg_suppliers_procurement` / `stg_shipping_logistics`

These four were 0-byte placeholders. They have been removed because:

1. They implied four sources that do not exist (ADR-002 §Decision 1).
2. Empty / comment-only `.sql` files are still discovered by dbt and break
   `dbt build` (ADR-002 risk R2).
3. A second staging model over `bronze.supply_chain_raw` would duplicate the
   latest-load-wins window and become a second, independently drifting
   definition of "current state per SKU" (ADR-002 risk R1).

When a real subject-area consumer asks for one, build it as
`int_sku_inventory` / `int_orders` etc. in `models/intermediate/`, over
`{{ ref('stg_products') }}`, with its own grain stated in the model header
(ADR-002 "Smallest safe next step" step 4).
