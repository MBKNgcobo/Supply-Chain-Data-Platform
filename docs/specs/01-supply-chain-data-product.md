# Spec: Supply Chain Operational Data Product

| Field | Value |
| --- | --- |
| Spec ID | SPEC-001 |
| Version | 0.1 (DRAFT — not approved) |
| Status | Blocked on Open Questions (see §11) |
| Author | Data engineering (this workspace) |
| Created | 2026-10-13 |
| Supersedes | nothing |
| Approval owner | **UNASSIGNED — blocking** |

## Assumptions Stated Up Front

These are assumptions, not decisions. Each one is repeated in §11 as an open question. Nothing below should be treated as agreed until §11 is closed.

| # | Assumption made in this draft | Confidence |
| --- | --- | --- |
| A1 | The source of truth is the single static Kaggle CSV (`supply_chain_data.csv`), not a live supplier or ERP system. | High — stated in `docs/source-data-contract.md` |
| A2 | This is a **batch** platform, not streaming. | High — stated in `README` ("automated batch data pipeline") |
| A3 | The grain of the primary business fact is one row per SKU. | **Medium** — the dataset has no order, shipment, or event identifier to support a finer grain. Needs confirmation. |
| A4 | There is **no date/time column** in the source, so the platform cannot yet express trend, period-over-period, or point-in-time analysis. | **High** — confirmed against the documented column list. Needs a decision, not a correction. |
| A5 | Target platform is PostgreSQL with a bronze/silver/gold layering, transformed by dbt and orchestrated by Airflow. | High — `README`, `sql/bootstrap/001_create_schemas.sql`, `dbt/`, `dags/` |
| A6 | The data is synthetic/public and contains no regulated personal data. | **Medium** — a column named `Customer demographics` exists; its contents are unverified. |

## 1. Objective

Replace manual, per-request SQL analysis of a single messy CSV with an automated, repeatable batch pipeline that loads the source into a PostgreSQL warehouse, enforces quality rules, records its own run history, and publishes modelled tables that Power BI can consume without anyone re-deriving the numbers by hand.

The immediate engineering deliverable is already partly built: `src/supply_chain_pipeline/` implements extract → validate → load with SHA-256 idempotency and a batch manifest. This spec exists because that implementation was written against a **different schema than the documented source contract**, and that gap must be resolved before more code is written.

## 2. Business Outcome

| Item | Value |
| --- | --- |
| Primary users | Procurement teams, inventory planners, operations managers, business analysts (per `README`) |
| Decision supported | Which SKUs, suppliers, carriers, and transportation modes to act on for revenue, stock, cost, and defect-rate performance |
| Current pain | Supply chain data arrives as separate files with inconsistent values and requires manual SQL analysis (per `README`) |
| Definition of done for the business | An analyst answers "which product type / supplier / carrier is underperforming, and why" from published warehouse tables alone, with no hand-written joins and no CSV opened in Excel |

Non-goal: this is not a real-time inventory feed, not a demand forecast, and not a system of record. It is a reporting layer over a static snapshot.

## 3. Source Systems And Contracts

### 3.1 Source of record

| Attribute | Value |
| --- | --- |
| System of record | Kaggle public dataset "Supply Chain Analysis" (`harshsingh2209/supply-chain-analysis`) |
| File | `supply_chain_data.csv` |
| Business origin | Fashion and beauty startup; makeup product supply chain |
| Delivery method | Manual download into `data/incoming/` (no API, no SFTP, no streaming) |
| Owner | Public dataset publisher — **no internal owner exists. Blocking.** |
| Update mechanism | Unknown / not published as a schedule. **Blocking.** |
| Licence | Kaggle public data licence. Redistribution terms unverified. |

### 3.2 Documented schema

`docs/source-data-contract.md` lists 24 column entries. It also records the business key as `SKU`, and leaves *Data types*, *Data quality rules*, and *Update behaviour* **blank**. This spec does not fill those blanks by guessing; see §11.

Confirmed schema hazards that the pipeline must handle explicitly:

1. **Duplicate column name.** `Lead time` appears twice (as `Lead times` and `Lead time`). Python's `csv.DictReader` silently collapses duplicate headers and keeps only the last value. This is silent data loss, not an error. The extractor must detect duplicate headers and fail loudly.
2. **No unique row identifier.** Nothing guarantees `SKU` is unique across rows. `SKU` is documented as the business key but uniqueness is unverified.
3. **No temporal column.** There is no date, timestamp, period, or snapshot field. Freshness, cadence, trend, and point-in-time semantics are therefore undefined at the source.
4. **Ambiguous column names.** `Costs` (logistics total) and `Cost`-prefixed fields coexist; `Availability` may be a count or a boolean; `Customer demographics` may be a scalar or a composite. All require profiling before typing.
5. **Mixed-type columns.** `Availability`, `Inspection results`, and `Defect rates` are the highest-risk candidates for mixed types. The existing extractor reads everything as string, which is the correct first move and must be preserved.

### 3.3 Extraction constraints inherited from the existing code

`src/supply_chain_pipeline/extract.py` already enforces: file existence, `.csv` extension, encoding fallback chain (`utf-8-sig` → `utf-8` → `latin-1` → `cp1252`), SHA-256 content hashing, and required-header validation. Header comparison is normalised with `.strip().lower()`, so `SKU` and `sku` match — good. Duplicate-header detection is **absent** and must be added.

Machine-readable contract: `docs/contracts/sources/supply_chain_data-csv.contract.yaml`.

## 4. Destination

PostgreSQL warehouse, three layers plus an operational schema. Schemas already created in `sql/bootstrap/001_create_schemas.sql`: `bronze`, `silver`, `gold`, `etl`.

| Layer | Object | Grain | Materialisation | Status |
| --- | --- | --- | --- | --- |
| Bronze | `bronze.sales_raw` | one row per source CSV row | append-only, all columns as TEXT | **Exists but columns are placeholders** (see §4.1) |
| Silver | `silver.sku_snapshot` (proposed) | one row per SKU per load | dbt incremental or view | Not built |
| Gold | `gold.fct_sku_performance` (proposed) | one row per SKU | dbt table | Not built |
| Gold | `gold.dim_product`, `gold.dim_supplier`, `gold.dim_carrier` (proposed) | one row per entity | dbt table | Not built |
| Ops | `etl.batch_manifest` | one row per batch | table | Created in Python (`manifest.py`) only |
| Ops | `etl.row_rejects` | one row per rejected source row | table | **Referenced in `load.py`, never created. Runtime failure.** |
| Ops | `etl.batch_runs`, `etl.batch_run_steps` | one row per run / step | tables | Created in SQL, unused by any code |

`dbt/` currently contains only empty directories (`models/staging`, `models/intermediate`, `models/marts`, `tests`, `seeds`) and empty `dbt_project.yml` / `profiles.yml.example`.

### 4.1 Bronze table must be reconciled with the source contract

`bronze.sales_raw` is currently declared as `order_id, customer, amount, order_date` — none of which exist in the documented source. It must be regenerated from the profiled source schema (all TEXT, plus lineage columns `batch_id`, `ingest_time`, `source_file`, `source_row_number`).

`load.py` additionally targets a `production_orders` table with columns `sku, description, price, quantity, source_identity, version, last_updated_at` that is never created anywhere in the repository. `description` and `quantity` do not exist in the source contract either. **This is the central inconsistency this spec must resolve** (§11, Q2).

Machine-readable contract: `docs/contracts/datasets/gold-fct_sku_performance.contract.yaml` (proposed, draft).

## 5. Data Grain

| Output | Grain | Uniqueness guarantee | Enforced by |
| --- | --- | --- | --- |
| `bronze.sales_raw` | one row per physical CSV row | `(batch_id, source_row_number)` | natural — append-only |
| `etl.batch_manifest` | one row per batch | `batch_id` PRIMARY KEY | exists in `manifest.py` |
| `etl.row_rejects` | one row per rejected CSV row | `(file_hash, row_number)` | **must be added — currently insert-only, re-runs duplicate** |
| `silver.sku_snapshot` | one row per SKU per successful load | `(batch_id, sku)` | not built |
| `gold.fct_sku_performance` | one row per SKU (current state) | `sku` | not built |

Grain change policy: any change to a gold grain is a **breaking contract change** requiring a new schema version, a downstream impact note, and sign-off. Never change grain silently via a join or dedupe.

Slowly changing behaviour: currently an implicit **type 1** overwrite — `load.py` increments a `version` counter on conflict but keeps no history. Whether `silver`/`gold` should be type 1 or type 2 is unresolved (§11, Q5). A type 2 history requires a valid-from date the source does not provide.

## 6. Cadence And Freshness

| Attribute | Value | Basis |
| --- | --- | --- |
| Run mode | Batch, on demand | Only mode the static CSV supports |
| Trigger | Airflow DAG in `dags/` | `dags/` is currently empty — DAG not written |
| Proposed schedule | Daily at 06:00 UTC | **Assumption only — no business requirement exists** |
| Freshness target | Data no older than 24h | Derived from the assumed daily schedule, not from a business need |
| Expected runtime | Unknown — depends on row count, which is unrecorded | |
| Late-arrival tolerance | Not applicable (single static file) | |

Because the source has no timestamp column, "freshness" cannot be measured on business data. It can only be measured as *time since last successful load*, from `etl.batch_manifest`. That is a real limitation and must be stated to consumers rather than hidden behind a green dashboard.

SLA statement to publish to consumers: *"This dataset reflects the most recent successful load of a manually supplied CSV file. It is not a live feed and cannot be guaranteed current."*

## 7. Quality Rules

### 7.1 Extract layer (already implemented — keep)

| Rule | Severity | Implementation |
| --- | --- | --- |
| File exists | Fatal | `extract.py` |
| Extension is `.csv` | Fatal | `extract.py` |
| Decodable as one of 4 encodings | Fatal | `extract.py` |
| File is non-empty and has a header row | Fatal | `extract.py` |
| All required headers present (case/whitespace-insensitive) | Fatal | `extract.py` |
| **No duplicate header names** | **Fatal — MISSING, must add** | prevents `DictReader` silent collapse |
| SHA-256 computed for every file | Mandatory | `extract.py` |

### 7.2 Validate layer (partially implemented)

| Rule | Severity | Implementation |
| --- | --- | --- |
| Batch aborts if zero rows read | Fatal | `validate.py` |
| `sku` present and non-blank on every row | Row-level reject | `validate.py` |
| File-level idempotency: hash already `SUCCESS` → skip | Skip as `DUPLICATE` | `validate.py` + `manifest.py` |
| `sku` unique within the file | **Not implemented — required if `sku` is the business key** | |
| Numeric columns parse (`Price`, `Revenue generated`, `Number of products sold`, `Stock levels`, `Lead times`, `Order quantities`, `Shipping times`, `Shipping costs`, `Production volumes`, `Manufacturing lead time`, `Manufacturing costs`, `Defect rates`, `Costs`) | Row-level reject | Not implemented |
| `Defect rates` within 0–100 (or 0–1) | Row-level reject | Not implemented — range unverified |
| `Availability` / `Inspection results` domain check | Row-level reject | Not implemented — encoding unknown |
| Reject-rate threshold breach (proposed: >5% of rows rejected) halts publish and alerts | Fatal for publish | Not implemented |

Reject handling policy: rejected rows go to `etl.row_rejects` with the raw payload, row number, and error message; the batch still commits its valid rows. A batch must never fail silently, and a reject must never be dropped without a record.

### 7.3 Reconciliation

| Check | Definition | Status |
| --- | --- | --- |
| Row-count reconciliation | `rows_read == rows_loaded + rows_rejected` for every batch | **Asserted** — `load.py` refuses to commit a mismatched batch (ADR-005); pinned by `tests/test_load.py::TestFailureAtomicity` |
| Bronze completeness | `count(bronze.sales_raw) where batch_id = X` equals manifest `rows_loaded` | Not implemented |
| Silver completeness | distinct `sku` in silver equals distinct `sku` in bronze for the batch | Not implemented |
| Gold completeness | `count(gold.fct_sku_performance)` equals distinct `sku` in silver | **Implemented** — dbt singular test `assert_fct_sku_performance_reconciles_to_silver.sql`, per `source_batch_id` |
| Value reconciliation | `sum(Revenue generated)` in gold equals bronze | **Meaningless until duplicate-header and dtype handling is settled** |

### 7.4 Testing

- Python: pytest unit tests for extractor/validator/loader, including a fixture with duplicate headers, a blank-`sku` row, an undecodable byte sequence, and a re-fed identical file. `tests/` does not exist yet.
- dbt: `not_null` and `unique` on every gold key, `accepted_values` on categorical dimensions, plus a relationship test from `fct_sku_performance.sku` to `dim_product.sku`.
- CI: GitHub Actions runs pytest + dbt build on every change. `.github/workflows/ci.yml` is referenced by `README` but not present.

## 8. Security And Governance

| Control | Requirement | Current state |
| --- | --- | --- |
| Ownership | A named accountable owner for the dataset | **None. Blocking.** |
| Credentials | DB password never in source | `cli.py` reads `DB_PASSWORD` from env with a default of `postgres` — must remove the default and fail closed |
| Secrets file | `.env.example` documents required vars | File exists but is **empty** |
| Classification | Proposed: **Internal** | Undocumented |
| PII fields | `Customer demographics` — contents unverified | Unassessed |
| PII policy | If `Customer demographics` contains direct or quasi-identifiers: mask in non-production, restrict `gold` access, exclude from BI extracts | Not implemented |
| Access | Read-only role for BI/Power BI; write role only for the pipeline service account | Not implemented |
| Lineage | Column-level lineage from source header → bronze → silver → gold | Not implemented |
| Retention | Raw source CSV retained indefinitely for replay | Undocumented |
| Retention | `etl.row_rejects` pruned after 90 days | Not implemented |
| Least privilege | Airflow, dbt, and Power BI use distinct credentials | Not implemented — one connection string shared |

## 9. Backfill And Replay

| Scenario | Required behaviour | Current state |
| --- | --- | --- |
| Same file re-fed | Skip; record `DUPLICATE`; no duplicate rows | **Works** — SHA-256 manifest check |
| File edited then re-fed (new hash) | Treated as a new batch; upsert overwrites current state; prior state lost unless history table exists | Upsert works; history does not exist |
| Corrupted partial load | Entire batch rolls back atomically; manifest records `FAILED` | **Works** — `load.py` wraps COPY + upsert + rejects in one transaction |
| Failed batch retry | Re-run same file after fixing cause; no manual SQL cleanup | Works, because the failed batch never committed |
| Full historical rebuild | Delete silver/gold, rebuild from bronze | **Blocked** — bronze columns don't match the source, so bronze cannot serve as the replay source of truth |
| Replay of rejected rows only | Fix rule or data, reload only `etl.row_rejects` rows | Not supported |

Must never happen:

- The same source file loaded twice into bronze under two batch IDs.
- A partially loaded batch left in a committed state.
- A `FAILED` batch marked `SUCCESS`.
- Rows silently dropped without a `etl.row_rejects` record.
- A gold table rebuilt from a source other than bronze.

## 10. Success Criteria

The spec is accepted when:

1. [ ] §11 is closed — every question has a recorded decision, an owner, and a date.
2. [ ] `docs/source-data-contract.md` records actual column names, dtypes, and row count from a profiled real file, and the duplicate `Lead time` column has a documented resolution.
3. [ ] `bronze.sales_raw` matches the profiled source schema, and the phantom `production_orders` / `staging_orders` DDL is either created in `sql/bootstrap/` or removed from `load.py`.
4. [ ] `etl.row_rejects` and `etl.batch_manifest` are created in version-controlled SQL, not only at runtime.
5. [ ] The extractor rejects duplicate headers instead of collapsing them.
6. [ ] One duplicate audit system is chosen: `etl.batch_manifest` **or** `etl.batch_runs` + `etl.batch_run_steps`. Two overlapping systems is itself a defect.
7. [ ] Row-count reconciliation `rows_read == rows_loaded + rows_rejected` is asserted in code and tested.
8. [ ] `python -m supply_chain_pipeline.cli --file <fixture.csv>` exits 0 on a clean file and non-zero on each fatal condition.
9. [ ] Re-feeding the identical file twice yields exactly one loaded batch.
10. [ ] `dbt build` succeeds and all gold tests pass against the loaded warehouse.
11. [ ] An Airflow DAG runs the pipeline end to end on a schedule with retries, and a failed task leaves `etl.batch_manifest.status = 'FAILED'`.
12. [ ] A Power BI report answers the primary user question from gold tables with no manual joins.
13. [ ] CI runs pytest and dbt build on pull requests.

Out of scope for this spec: Power BI dataset design, forecasting, supplier scorecarding beyond the published facts, multi-tenant access control.

## 11. Open Questions

Blocking questions must be answered before `/plan` and before any further implementation. Each lists a proposed default so a single approval unblocks the work.

**Q1 — Who owns this dataset? (BLOCKING)**
No business owner is named anywhere in the repository. Without an owner there is no one to confirm grain, metric definitions, or the SLA.
*Proposed default:* the procurement lead for the reporting period, named in `docs/source-data-contract.md` and in the dataset contract `owner` field.
A: Procurement teams, inventory planners, operations managers, and business analysts.

**Q2 — Which schema is authoritative: the documented source contract, or the code? (BLOCKING)**
`docs/source-data-contract.md` describes `SKU / Price / Availability / Number of products sold / Revenue generated / Stock levels / ...`. `cli.py` requires `sku, description, price, quantity` and `load.py` upserts into `production_orders(sku, description, price, quantity, ...)`. `bronze.sales_raw` declares `order_id, customer, amount, order_date`. Three incompatible schemas.
A: the profiled real CSV is authoritative. Rewrite `bronze.sales_raw` from it, update the CLI required headers to the real columns, and delete the `production_orders` placeholder path.

**Q3 — How is the duplicate `Lead time` column resolved? (BLOCKING)**
The source contains both `Lead times` and `Lead time`. Are they the same measure? Is one `Lead times` per order and the other `Manufacturing lead time`? `csv.DictReader` currently keeps only one, silently.
A: load both into bronze with positional suffixes (`lead_times_1`, `lead_times_2`), profile their values, then collapse or rename in silver with a documented decision. Never silently drop.

**Q4 — Is `SKU` actually unique, and what is the real grain? (BLOCKING)**
If `SKU` repeats, "one row per SKU" is wrong and every downstream aggregate double-counts.
*Proposed default:* profile the file. If `SKU` is unique, adopt one row per SKU. If not, define the grain explicitly (e.g. one row per SKU per supplier per carrier) and add a surrogate key.
A: SKU is unique, so adopt one per row

**Q5 — Type 1 or type 2 history? (BLOCKING for silver/gold design)**
The source has no dates, so a type 2 model has no `valid_from`. Type 1 (overwrite) loses the ability to say "this changed since last load".
A: silver = type 1 overwrite keyed on `sku`, retaining `load_batch_id` so any state is recoverable from bronze. Add true type 2 only if a date field is later supplied.

**Q6 — Is `Customer demographics` PII? (BLOCKING for security)**
*Proposed default:* profile the column. If it holds age bands, gender, or any identifier, classify the dataset **Confidential**, mask in non-production, and exclude from the gold BI model.
A: Holds gender data. Unknown, Male, Non-binary, Female

**Q7 — What are the actual dtypes and encodings of the ambiguous columns? (BLOCKING for silver)**
`Availability`, `Inspection results`, `Defect rates`, `Costs`, and `Customer demographics` are unverified.
*Proposed default:* profile and publish a typed data dictionary in `docs/data-dictionary.md` before writing any silver SQL. Keep bronze as TEXT.
A: docs/data-dictionary.md created with dtypes
product_type VARCHAR(50),
sku VARCHAR(20),
price NUMERIC(12,2),
availability INTEGER,
number_of_products_sold INTEGER,
revenue_generated NUMERIC(14,2),
customer_demographics VARCHAR(50),
stock_levels INTEGER,
lead_times INTEGER,
order_quantities INTEGER,
shipping_times INTEGER,
shipping_carriers VARCHAR(50),
shipping_costs NUMERIC(12,2),
supplier_name VARCHAR(50),
location VARCHAR(100),
lead_time INTEGER,
production_volumes INTEGER,
manufacturing_lead_time INTEGER,
manufacturing_costs NUMERIC(12,2),
inspection_results VARCHAR(20),
defect_rates NUMERIC(10,4),
transportation_modes VARCHAR(50),
routes VARCHAR(50),
costs NUMERIC(14,2)

**Q8 — How often must this actually run, and for whom? (non-blocking, needed before scheduling)**
No freshness requirement has been stated. A daily schedule is a guess.
A: schedule daily 06:00 UTC, and label the dataset as "snapshot, not live" so no consumer builds an SLA on it.

**Q9 — Is the data licensed for internal redistribution? (non-blocking, needed before Power BI publish)**
Kaggle public data has licence terms; internal redistribution is unverified.
*Proposed default:* record the Kaggle licence and attribution in `docs/source-data-contract.md`, and keep the published model internal-only.
A:licensed for internal redistribution

**Q10 — Which orchestration and test tooling is actually wired up? (non-blocking, needed for CI)**
`dags/`, `tests/`, `.github/workflows/ci.yml`, `pyproject.toml`, `compose.yaml`, `Dockerfile`, and `.env.example` are all empty or absent, so the reliability claims in `README` are not yet real.
A: implement in this order — `pyproject.toml` + pytest fixtures → `sql/bootstrap` DDL complete → `dbt_project.yml` + profiles → `dags/` DAG → `compose.yaml` → CI.

## 12. Explicitly Not Decided Here

Metric definitions (`Revenue generated` gross vs net; whether `Costs` includes shipping), row-count targets, warehouse sizing, and Power BI semantic model layout are deferred until Q1–Q7 close.
