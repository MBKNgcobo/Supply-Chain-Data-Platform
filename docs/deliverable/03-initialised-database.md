# Deliverable 3 — Initialised database: raw-data storage and operational audit tables

| Field | Value |
| --- | --- |
| Status | **CODE-COMPLETE, NOT EXECUTED.** All DDL exists, is idempotent, and is ordered; it has never been run against a database this project owns (no warehouse reachable — ADR-003 verification scope). |
| Date | 2026-10-14 |
| Artifacts | `sql/bootstrap/001_create_schemas.sql`, `002_create_audit_tables.sql`, `003_create_bronze_supply_chain_raw.sql`; `src/supply_chain_pipeline/manifest.py` |

## 1. What gets created (and by whom)

| Schema | Object | Created by | Purpose |
| --- | --- | --- | --- |
| `bronze` | `supply_chain_raw` | `003_create_bronze_supply_chain_raw.sql` | Raw-data storage: one row per source line, all 24 source columns **TEXT**, plus loader provenance |
| `bronze` | `sales_raw` | `001_create_schemas.sql` ⚠ | **Wrong-schema leftover** — see §4.1 |
| `silver` | *(schema only)* | `001` | dbt staging/intermediate land here |
| `gold` | *(schema only)* | `001` | dbt marts land here |
| `etl` | `batch_runs`, `batch_run_steps` | `001_create_schemas.sql` | Run/step audit: status CHECK (`running`/`success`/`failed`), timings, `run_parameters jsonb` |
| `etl` | `row_rejects` | `002_create_audit_tables.sql` | Quarantine: one row per rejected source row, `raw_data jsonb` preserved **exactly as read** |
| `etl` | `batch_manifest` | `manifest.setup_manifest_table()` **at pipeline runtime** | Batch idempotency/audit: hash, status (`PENDING`/`SUCCESS`/`FAILED`/`DUPLICATE`), row counts |

**Run order:** `001` → `002` → `003` via `psql`. `batch_manifest` is intentionally not in SQL — the pipeline creates it (noted in `002`'s header) — meaning a fresh database has the audit tables only after either a bootstrap *or* the first pipeline run, whichever comes first.

## 2. Raw-data storage: `bronze.supply_chain_raw`

- **Columns:** 5 provenance (`bronze_row_id` identity PK, `source_batch_id`, `source_identity`, `file_hash`, `row_number`, `loaded_at`) + all 24 source columns as `text`. *Rule 1 in the file: do not type these columns — the cast boundary is silver (ADR-001 §1).*
- **Indexes:**
  - `UNIQUE (source_identity)` — the replay guard. `COPY` cannot `ON CONFLICT`, so re-loading an already-present file raises a unique violation rather than silently doubling rows; `cli.py`'s manifest check normally prevents reaching that path. A replay is **loud, not silent** (ADR-001).
  - `(sku)` — key lookups from staging.
  - `(source_batch_id, loaded_at DESC)` — per-batch recency, which is what `stg_products`' latest-load-wins window sorts by.
- **Append-only:** the loader never merges or updates; "latest load wins per SKU" lives once, in `stg_products` (SPEC-001 Q5). Prior state stays recoverable from bronze.
- **Loader/DDL contract:** `load.py` carries `BRONZE_SOURCE_COLUMNS` (the same 24 names) and checks it against the DDL before COPY — an added DDL column the loader doesn't know is a startup failure, not silent schema drift.

## 3. Operational audit tables

**`etl.batch_manifest`** (the freshness source of truth — SPEC-001 Q8): `batch_id` PK, `pipeline_name`, `source_file`, `source_hash`, `started_at`/`finished_at`, `status`, `rows_read`/`rows_loaded`/`rows_rejected`, `error_message`. Partial index on `source_hash WHERE status = 'SUCCESS'` backs the duplicate check. Lifecycle: `PENDING` on start → `SUCCESS`/`FAILED`/`DUPLICATE` on completion; `FAILED` is written even when the main transaction rolls back. The published freshness metric is *time since last `SUCCESS` row here* — not a business timestamp, because none exists.

**`etl.row_rejects`** (quarantine): `file_hash` + `row_number` + `raw_data jsonb` (verbatim — no trimming, so the rejection cause is investigable) + `error_message` + `rejected_at`. Three indexes: `(file_hash, row_number)` for the operator query "what was wrong with line N of file X"; `rejected_at DESC`; and `UNIQUE (file_hash, row_number)` so a retried batch cannot double-count the quarantine. Table comment states the reconciliation identity: `rows_read == rows_loaded + count(etl.row_rejects)` per file.

**`etl.batch_runs` / `etl.batch_run_steps`**: run-level and step-level audit with status CHECKs and `ON DELETE CASCADE` from run to step. **No code writes to them yet** (`dags/` is empty) — created in anticipation of Airflow orchestration (SPEC-001 Q10 order puts the DAG later).

## 4. Known defects and gaps

1. **`bronze.sales_raw` still exists in `001`** (ADR-001 open item #4): `(order_id, customer, amount, order_date)` describes a different source. It is never read, but its presence invites a second staging model written against it. Delete or reconcile before first commit.
2. **Executed 2026-10-15:** scripts 001–003 ran successfully against a project-dedicated `supply_chain` database on the shared `localhost:5432` server — authorized in the 2026-10-15 design review. Contamination is avoided by database-level separation, not server-level abstention, superseding the earlier "another project's database" rejection (ADR-003-era alternative). Index creation and CHECK constraints verified by the successful run; see `docs/testing/dbt-test-plan.md` §Verification.
3. **`rows_read == rows_loaded + rows_rejected` is asserted** — `load.py` refuses to commit a mismatched batch (ADR-005) and `tests/test_load.py::TestFailureAtomicity` pins the behaviour. Both reconciliation identities are catalogued in `docs/testing/dbt-test-plan.md` §5.
4. **Retention unspecified:** `etl.row_rejects` grows unbounded (SPEC-001 §8 proposed 90 days, not implemented); raw CSV retention "indefinitely" is undocumented.

## 5. Verification performed

- Static review of all three scripts + `manifest.py` against the loader's expectations (table/column names match: `bronze.supply_chain_raw`, `etl.row_rejects`, `etl.batch_manifest` all agree between DDL and Python).
- All DDL is idempotent (`IF NOT EXISTS` / `CREATE UNIQUE INDEX IF NOT EXISTS`), so re-running bootstrap is safe.
- **Not verified:** execution against a database. That remains the first step of §1's run order on the first project-owned Postgres.
