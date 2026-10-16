# ADR-001: Staging layering and the typed cast boundary

| Field | Value |
| --- | --- |
| Status | Accepted (implementation-level, reversible) |
| Date | 2026-10-14 |
| Relates to | SPEC-001 §11 Q2, Q3, Q4, Q5, Q6, Q7 |
| Scope | `dbt/models/staging/stg_products.sql`, `sql/bootstrap/003_*.sql` |

## Context

`stg_products.sql` was a 0-byte placeholder and could not have been written as a
standalone task, because there was no source relation to read from. Three
incompatible schemas existed for the same source file:

1. `bronze.sales_raw(order_id, customer, amount, order_date)` in
   `sql/bootstrap/001_create_schemas.sql` — does not describe this CSV.
2. `production_orders(sku, description, price, quantity)` in
   `src/supply_chain_pipeline/load.py` — a third, different schema, and the
   table is never created by any bootstrap script.
3. The 24-column list in `docs/data-dictionary.md` and
   `docs/contracts/sources/supply_chain_data-csv.contract.yaml`, which agree
   with each other.

Additionally `dbt/dbt_project.yml` and `dbt/profiles.yml.example` were 0 bytes,
so dbt could not resolve a profile or materialise into any schema.

## Decision

**1. Bronze is the typed-cast boundary, and bronze stays TEXT.**
All 24 source columns are declared `text` in `bronze.supply_chain_raw`. Casting
happens in `stg_products`, once, in one readable place. This follows the comment
already present in `001_create_schemas.sql` ("keep as TEXT to avoid type loss")
and the contract's `target_type: cast in silver only`.

**2. `stg_products` casts and trims; it does not model.**
No joins, no aggregates, no derived measures, no category normalisation. Those
belong in `models/intermediate/`. Non-key columns are not null-dropped, so the
count of missing values stays observable downstream.

**3. Latest successful load wins per `sku`, type 1.**
Per SPEC-001 Q5. `row_number() over (partition by sku order by loaded_at desc,
source_batch_id desc, source_row_id desc)` with a deterministic tiebreak, so the
result does not change between runs when two loads share a timestamp.
`source_batch_id` is retained so any prior state is recoverable from bronze.

**4. `availability` and `inspection_results` are deliberately not cast.**
SPEC-001 Q7 is open: availability may be a count or a flag, inspection_results a
pass/fail or a score. Casting would encode a guess that is expensive to reverse
once consumers depend on it. They are passed through as `text`.

**5. `lead_times_1` and `lead_times_2` are both kept, unmerged.**
SPEC-001 Q3. Renaming them to `_1`/`_2` means neither is silently dropped.

**6. `customer_demographics` is surfaced but flagged, not dropped.**
SPEC-001 Q6 confirmed it holds gender data. Staging must remain a faithful typed
projection, so the column is present and documented as provisional PII with an
explicit "do not join into a published model" instruction.

**7. No surrogate key, no dbt_utils.**
`sku` is already unique (SPEC-001 Q4). A hash of it would be a second key with
no consumer, and `dbt_utils` is not a dependency of this repo, so nothing in the
model references it.


## Consequences

- A dbt test on `stg_products` can only be meaningful once bronze is populated,
  because a view over an empty table passes every uniqueness test trivially.
- `dbt_project.yml` sets `+schema: silver` / `gold`, so `macros/generate_schema_name.sql`
  is required. Without it dbt concatenates and creates `dbt_silver`, splitting the
  schemas that `001_create_schemas.sql` already made.
- No `freshness` block is declared on the source. The file has no timestamp, so
  dbt could only measure the loader's clock. Freshness is reported as time since
  the last SUCCESS row in `etl.batch_manifest`, in Airflow.

## Verification

`stg_products` was compiled (config block stripped, `source()` resolved exactly
as dbt would) and executed as a view against a live PostgreSQL 16 instance with
a fixture containing two loads of the same SKUs, blank strings, untrimmed
whitespace, and a null-SKU row. 19/19 assertions passed, covering grain, unique
key, latest-load-wins, provenance retention, trimming, empty-string-to-null, the
TEXT columns staying TEXT, both lead-time columns surviving, and PII retention.

One behaviour was established by experiment rather than assumed: an unparseable
numeric raises `invalid input syntax` **when the cast is evaluated**, but Postgres

## Blocking prerequisites

#1-#3 below were resolved by the loader changes. #4 and #5 remain open.

| # | Prerequisite | State |
| --- | --- | --- |
| 1 | Repoint `load.py` at `bronze.supply_chain_raw`. It previously wrote `production_orders`, which no bootstrap script creates. | **RESOLVED** — `load.py` COPYs into `bronze.supply_chain_raw`; the temp table and `production_orders` upsert are gone. |
| 2 | Normalise CSV headers to snake_case. `load.py` built its COPY header from raw `DictReader` fieldnames (`'Product Type'`). | **RESOLVED** — `extract.normalise_header()` / `build_column_map()`. |
| 3 | Fix the duplicate-header collapse. `csv.DictReader` kept only one of `'Lead times'` / `'Lead time'`. | **RESOLVED** — rows are read positionally against an explicit column map, so both survive as `lead_times_1` and `lead_times_2`. |
| 4 | Delete or reconcile `bronze.sales_raw`. Leaving it invites a second staging model to be written against it. | OPEN — still created by `001_create_schemas.sql`. |
| 5 | `docs/contracts/datasets/gold-fct_sku_performance.contract.yaml` references `ref('dim_product')`, which does not exist. | OPEN — nothing in `models/` provides it. |

### Resolution

Two decisions were made while closing #1-#3 that are not obvious from the code.

**Bronze stays append-only; the loader does not merge.**
The old loader upserted into a table keyed on `sku`, so a re-run silently
overwrote the previous state. Bronze now only appends, and "latest load wins" is
resolved entirely in `stg_products`. That keeps the merge in the one place that
knows the grain (SPEC-001 Q5) and leaves any prior state recoverable from bronze.

Replay behaviour is worth stating explicitly: bronze has a UNIQUE index on
`source_identity`, and `COPY` cannot use `ON CONFLICT`, so re-loading a file whose
rows are already present raises a unique violation rather than quietly doubling
the row count. The manifest check in `cli.py` is what normally prevents that path
being reached. A replay should be loud, not silent.

**Header aliasing is a small explicit map, not a general rule.**
`normalise_header()` is generic snake_case. The only non-generic renames are
`lead_times` -> `lead_times_1` and `lead_time` -> `lead_times_2`, held in
`SOURCE_HEADER_ALIASES`. That map is the source contract expressed in code, and it
is the single place to look when the upstream file changes. If SPEC-001 Q3 is ever
resolved and the two columns collapse, delete those two entries.

Two adjacent defects were found and fixed while making the load path work, and
neither was in the original plan:

- `etl.row_rejects` was written by `load.py`, but `002_create_audit_tables.sql`
  was empty, so the table did not exist. Any row with a missing `sku` would have
  failed the whole transaction at the quarantine step, turning a quarantine into
  a pipeline outage. The table is now created.
- `load.py` serialised reject payloads with `str()` into a `jsonb` column. Python
  dict repr is not valid JSON, so every reject would have failed the cast. Now
  uses `json.dumps`.

The `cli.py` default `required_headers` was also wrong for this source: it was
`["sku", "description", "price", "quantity"]`, and neither `description` nor
`quantity` exists in `supply_chain_data.csv`. Extraction therefore failed on
every real file before reaching any of this code. It now defaults to the columns
the source contract marks `required: true`.


## Rejected alternatives

- **Writing staging against `bronze.sales_raw`.** Rejected: the table does not
  describe the source file. Modelling around a wrong schema would have hidden
  the defect instead of surfacing it.
- **Casting in the Python loader.** Rejected: it puts a typing decision in the
  one place where a bad value would be rejected before anyone can see it, and it
  duplicates the cast rules in two languages.
- **Adding `dbt_utils` for a surrogate key.** Rejected: unused dependency, and
  `sku` is already the key.

| 4 | Delete or reconcile `bronze.sales_raw`. Leaving it invites a second staging model to be written against it. | Schema clarity |
| 5 | `docs/contracts/datasets/gold-fct_sku_performance.contract.yaml` references `ref('dim_product')`, which does not exist. Nothing in `models/` provides it. | Gold publish gate |

predicate pushdown means `count(*)` with a filter can skip the cast and succeed.
Any check intended to catch bad values must project the column. This is recorded
in the model header.

