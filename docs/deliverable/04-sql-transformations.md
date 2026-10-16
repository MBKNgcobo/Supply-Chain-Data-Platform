# Deliverable 4 — Repeatable SQL transformations: raw → cleaned, typed, documented

| Field | Value |
| --- | --- |
| Status | **CODE-COMPLETE, COMPILE-VERIFIED ONLY.** `dbt parse` and `dbt compile` exit 0 (ADR-003 verification); `dbt build` has never run against a populated warehouse — `data/` holds no source file, so tests currently pass trivially over empty tables. |
| Date | 2026-10-14 |
| Artifacts | `dbt/models/staging/*`, `dbt/models/intermediate/*`, `dbt/models/marts/*`, `dbt/tests/*`, `dbt/macros/generate_schema_name.sql` |

## 1. The chain

```
bronze.supply_chain_raw          TEXT, append-only, written by the Python loader
        │
        ▼  stg_products (view, silver)         ← the ONE cast boundary + the ONE dedupe
        │
        ├─► int_sku_inventory (view, silver)   ← inventory projection (no consumer yet)
        │
        └─► fct_sku_performance (table, gold)  ← 24-column published fact
            dim_product (table, gold)          ← labels at one row per sku
```

`dbt_project.yml` places each layer into the pre-existing `silver`/`gold` schemas via `+schema` plus the `generate_schema_name` macro (without it, dbt would create `dbt_silver` and split the schemas).

## 2. What each model does — and deliberately does not

**`stg_products` (silver, view)** — the single place where:
1. TEXT bronze columns are cast to their documented types (data dictionary = authority). Cast failures are intentional: an unparseable value **raises**, rather than silently becoming a null measure. Caveat documented in the model header: Postgres can push predicates past a cast, so validating queries must *project* the column (`count(price)`, not `count(*)`).
2. Text columns are trimmed; `''` → NULL so "missing" and "literally empty" are one state.
3. Latest load wins per `sku`: `row_number() OVER (PARTITION BY sku ORDER BY loaded_at DESC, source_batch_id DESC, source_row_id DESC)` with a deterministic tiebreak, so identical inputs give identical output between runs — repeatability is designed in, not hoped for.

Not: no joins, no aggregates, no derived measures, no null-dropping of non-key columns (missing values stay observable), no merging of `lead_times_1`/`lead_times_2` (SPEC-001 Q3 open), and `customer_demographics` is surfaced with a do-not-publish flag (staging is a faithful projection; exclusion happens at gold).

**`int_sku_inventory` (silver, view)** — pure projection of inventory-relevant columns; inherits grain and types by `ref()`, adds no logic. **No consumer yet** — kept deliberately (ADR-003 decision 5) with the revisit trigger "when a consumer appears".

**`fct_sku_performance` / `dim_product` (gold, tables)** — contract-first projections of `stg_products` (ADR-003): the fact carries the contract's 24 columns in contract order; the dimension carries `sku` + six labels + `source_batch_id`. Both are rebuildable from silver alone; agreement between them is a `relationships` test, not a construction step.

## 3. Why it is repeatable

- **dbt materializations:** views re-create; gold tables are full-refreshable (`dbt build --full-refresh` rebuilds everything from bronze up — SPEC-001 §9's replay path, currently blocked on bronze matching the profiled schema).
- **No stateful logic in SQL:** every model is a pure function of `bronze.supply_chain_raw` at its current contents. Merge semantics (type 1, deterministic tiebreak) live in exactly one place — re-running never re-decides.
- **Loader idempotency upstream:** SHA-256 duplicate check → same file never re-enters bronze; `source_identity` unique index makes an enforced replay loud.
- **Schema placement is code** (`generate_schema_name` macro), not environment folklore.

## 4. Documentation layer (part of the deliverable)

- Every model has a YAML file with per-column `description` and `data_type` — column docs live with the model, not in a detached wiki (`docs/data-dictionary.md` explicitly disclaims being warehouse documentation).
- Model headers state grain, what the model does, what it deliberately does not, and which SPEC question governs each open point.
- Source declared in `staging/sources.yml` with column tests; freshness intentionally *not* declared there (no business timestamp — the honest metric is manifest-based, checked in orchestration).
- Dataset contract: `docs/contracts/datasets/gold-fct_sku_performance.contract.yaml` — the approval record, machine-checked against the model (contract↔model consistency script, ADR-003).
- `exposures.yml` declares both named consumers so dbt lineage reaches past gold.

## 5. Tests (21 generic + 2 singular)

| Kind | Coverage |
| --- | --- |
| `stg_products` | `sku` unique + not_null; `source_batch_id` not_null; `price`/`defect_rates` not_null at **severity warn** (record, don't fail — null rates unknown until profiling) |
| `int_sku_inventory` | `sku` unique + not_null, `source_batch_id` not_null |
| `fct_sku_performance` | `sku` unique + not_null; `relationships → dim_product.sku`; `source_batch_id` not_null |
| `dim_product` | `sku` unique + not_null; `source_batch_id` not_null |
| Source (bronze) | `sku`/`source_batch_id` not_null, `source_identity` unique + not_null |
| Singular | `assert_defect_rates_within_accepted_range` (superset 0–100, **warn** — encodes no guess; promotes to error when SPEC-001 Q7's range is answered); `assert_fct_sku_performance_reconciles_to_silver` (per-`source_batch_id` counts must match — catches silent row drops and fan-out); `assert_order_quantities_non_negative` (**error** — settled business rule: a quantity's sign is not an encoding guess; nulls pass) |

## 6. Verification and honest limits

**Verified (ADR-003, dbt-core 1.12.5 / dbt-postgres 1.11.0):** `dbt parse` exit 0; `dbt compile --no-populate-cache` exit 0; 4 models, **22** tests, 1 source, 2 exposures in `dbt ls`; contract↔model 24-column consistency script passes. **Live-verified 2026-10-15:** first live `dbt build` executed against the populated `supply_chain` database — PASS=26 WARN=0 ERROR=0, exit 0 (see `docs/testing/dbt-test-plan.md` §Verification).

**Still unverified:** the §3 claim of repeatable rebuild under `--full-refresh`, and behaviour under a second (changed) source delivery. (The earlier deferral — "no SQL executed against a database; test results on populated data" — was closed by the 2026-10-15 live build.)

