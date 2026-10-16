# ADR-002: Staging is one model per source, not one per subject area

| Field | Value |
| --- | --- |
| Status | Proposed (review output of `data-architect` persona) |
| Date | 2026-10-14 |
| Relates to | SPEC-001 §4, §5, §11 Q1/Q3/Q7; ADR-001 |
| Scope | `dbt/models/staging/*`, `dbt/models/intermediate/`, `dbt/models/marts/` |
| Decision | **REQUEST CHANGES** — `stg_inventory` must not be built as specified |

## Context

`dbt/models/staging/` currently holds four 0-byte placeholders named by subject
area:

| File | Bytes |
| --- | --- |
| `stg_inventory.sql` | 0 |
| `stg_sales_orders.sql` | 0 |
| `stg_suppliers_procurement.sql` | 0 |
| `stg_shipping_logistics.sql` | 0 |

The project has exactly **one** staging model with content, `stg_products.sql`
(165 lines), and exactly **one** source relation:

- `dbt/models/staging/sources.yml` declares a single source table,
  `bronze.supply_chain_raw`.
- `stg_products.sql:43` is the only `source()` call in the codebase.
- `supply_chain_data.csv` is the only source file
  (`docs/source-data-contract.md`), landing all 24 columns in that one table at
  one row per physical CSV row.

`stg_products` already casts, trims and de-duplicates **all 24 columns** to one
row per `sku`, and already carries a `unique` + `not_null` test on `sku`
(`stg_products.yml:16-23`). There is no second source for a subject-area staging
model to read.

## Decision

**1. Staging is 1:1 with a source relation. There is one staging model.**
`stg_products` is it, until a second source feed exists. The four subject-area
placeholders imply four sources that do not exist.

**2. Subject-area splits belong in `intermediate/`, as `int_*`, over `ref()`.**
The subject split that `stg_inventory` / `stg_sales_orders` /
`stg_suppliers_procurement` / `stg_shipping_logistics` are reaching for is real
work — but it is a projection of `stg_products`, not a cast of bronze. Per
ADR-001 §2, staging "casts and trims; it does not model". A second staging model
over `bronze.supply_chain_raw` would re-run the latest-load-wins window and
become a second, independently drifting definition of "current state per SKU".

**3. The dimension tables already named in the spec are the destination for the
supplier/carrier split.** SPEC-001 §4 names `gold.dim_product`,
`gold.dim_supplier`, `gold.dim_carrier`. Those consume
`supplier_name` / `shipping_carriers` / `transportation_modes`, which
`stg_products` already exposes (`stg_products.sql:64-68`). Nothing new is needed
in staging to reach them.

## Risks, ordered by operational impact

**R1 — Four independent "latest load wins" windows over one table. (BLOCKING)**
Each new staging model would repeat
`row_number() over (partition by sku order by loaded_at desc, source_batch_id
desc, source_row_id desc)`. Four copies of that window are four answers to
"what is current stock for this SKU", which agree today and drift the first time
one is edited. `gold.fct_sku_performance`'s reconciliation rule is
`count(distinct sku in silver for source_batch_id)` — with four silver models,
"silver" has no referent, so the publish gate cannot be evaluated at all. This
is SPEC-001 §5's "never change grain silently via a join or dedupe" failing
before any grain was even defined.

**R2 — 0-byte files are discovered as nodes. (HIGH)**
The four placeholders sit under `model-paths: ["models"]`, so dbt discovers
four models whose entire SQL is empty. Either they fail at run time or they
compile to nothing; both break SPEC-001 §10 criterion 10 (`dbt build` succeeds).
*dbt is not installed in this workspace, so this could not be executed — verify
with `dbt ls` and `dbt build` before relying on the specific failure mode.*

**R3 — No owner, and the publish gate is closed. (HIGH)**
Both contracts carry `owner: UNASSIGNED` (SPEC-001 Q1) and
`publish_gate.current_gate_state: blocked`. Q3 (`lead_times_1` vs `_2`) and Q7
(`availability`, `inspection_results`, `defect_rates` range) are still open. Any
new silver model built now hard-codes today's guesses into a second place.

**R4 — `dim_product` does not exist. (MEDIUM)**
`gold-fct_sku_performance.contract.yaml:122` has a `relationships` test to
`ref('dim_product')`. Nothing in `models/` provides it (ADR-001 #5, still OPEN),
so the gold publish gate cannot pass regardless of staging.

**R5 — No `exposures` declared. (MEDIUM)**
The gold contract names consumers `powerbi-operational-reporting` and
`procurement-analysts`, but no `exposures:` block exists anywhere in `dbt/`. dbt
lineage therefore stops at gold, and a Power BI dashboard's dependency on
internal model shapes is invisible to `dbt build` and to impact analysis.

**R6 — `bronze.sales_raw` still created. (MEDIUM)**
`001_create_schemas.sql:2` still creates the wrong-schema table
(ADR-001 #4, still OPEN). It is an invitation to write a staging model against
it — the exact defect ADR-001 rejected.

## Missing contracts, ownership, lineage, recovery

- **Owner** — unassigned on both contracts (Q1). Grain, metric definitions and
  SLA have no one to confirm them.
- **Lineage** — no `exposures`; the declared BI consumers are not in dbt's graph.
- **Recovery** — SPEC-001 §9 "full historical rebuild: delete silver/gold,
  rebuild from bronze" now has an unstated dependency: it only works if silver
  has one definition of "current". Four staging models make rebuild
  order-dependent and silently non-deterministic.
- **Publish boundary** — undefined, because "silver" names four models.

## Smallest safe next step

1. Record this ADR. Do not write `stg_inventory`.
2. Delete the four 0-byte placeholders, or replace each with a comment-only
   pointer to this ADR, so they stop being discovered nodes.
3. Keep `stg_products` as the sole staging model.
4. When a subject-area consumer actually asks for one, build it as
   `int_sku_inventory` / `int_orders` etc. in `models/intermediate/`, over
   `{{ ref('stg_products') }}`, with its own grain stated in the header.

## Verification

Static, against the repository as it stands on 2026-10-14:

- 0-byte sizes of the four placeholders confirmed on disk.
- Exactly one `source()` call in the codebase (`stg_products.sql:43`); exactly
  one source table in `sources.yml` (`bronze.supply_chain_raw`).
- `stg_products.sql` selects all 24 source columns; `stg_products.yml` tests
  `sku` `unique` + `not_null`.
- `models/intermediate/` and `models/marts/` exist and are empty, so the
  `int_*` target in step 4 is available with no project change.

Not verified: `dbt ls` / `dbt build` behaviour (dbt is not installed in this
workspace; `import dbt` fails). The claim in R2 is that empty SQL is a defect
either way, not a prediction of the specific error text.

## Rejected alternatives

- **Build `stg_inventory` as a column subset of `stg_products`.** Rejected: it
  would be correct today and become R1 the first time the two diverge. A subset
  of an already-modelled relation is an intermediate model.
- **Build it against `bronze.supply_chain_raw` directly.** Rejected: duplicates
  the cast and the dedup, and puts a second typed boundary in the project —
  the exact thing ADR-001 §1 exists to prevent.
- **Rename the placeholders to `int_*` and leave them 0-byte.** Rejected: fixes
  the name but leaves four discovered nodes with no content. The subject split
  should be created when a consumer exists, not pre-named.
- **Keep one staging model per subject area, one source per subject.** This
  becomes correct the moment a second source feed arrives. It is wrong now, and
  the file names should follow the sources, not the org chart.
