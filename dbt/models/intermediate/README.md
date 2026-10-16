# Intermediate layer
#
# Intermediate models are subject-area projections and transformations over
# the staging layer, not over bronze. They exist to keep business logic out
# of staging (where the TEXT-to-typed boundary lives, once -- ADR-001 §1) and
# out of marts (where publish semantics live).
#
# Rules this layer follows:
#   1. Reference staging via `ref()`, never `source()` directly. A second
#      `source()` call over `bronze.supply_chain_raw` would duplicate the
#      latest-load-wins window and become a second, independently drifting
#      definition of "current state per SKU" (ADR-002 risk R1).
#   2. State the model's grain in its header.
#   3. Ship tests + documentation alongside the model (see `*.yml`).
#
# ## Why `int_sku_inventory` exists instead of `stg_inventory`
#
# ADR-002 ("Staging is one model per source, not one per subject area")
# rejected `stg_inventory` as specified. There is exactly one source
# (`bronze.supply_chain_raw`), so `stg_products` is the sole staging model;
# a subject-area split like inventory is a projection of that model, not a
# cast of bronze. When a real inventory consumer asked for one, it was built
# here as `int_sku_inventory` over `{{ ref('stg_products') }}` -- exactly the
# path ADR-002 "Smallest safe next step" step 4 prescribes.
#
# `int_sku_inventory` is an intermediate building block, not a published gold
# table: the gold publish gate is still blocked, `dim_product` does not exist
# yet, and no exposure consumes this model.
#
# See [`docs/decisions/ADR-002-staging-is-one-model-per-source.md`](../../../docs/decisions/ADR-002-staging-is-one-model-per-source.md).