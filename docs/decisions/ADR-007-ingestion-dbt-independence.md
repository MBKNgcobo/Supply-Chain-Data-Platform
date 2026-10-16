# ADR-007: Ingestion and dbt are runtime-decoupled, contract-coupled via bronze

| Field | Value |
| --- | --- |
| Status | Accepted (implementation-level, reversible) |
| Date | 2026-10-15 |
| Relates to | ADR-001 (bronze TEXT, append-only); ADR-002 (one staging model); ADR-003 (gold contract-first); ADR-005 (exit codes); ADR-006 (test strategy) |
| Scope | `src/supply_chain_pipeline/{cli,extract,load}.py`, `sql/bootstrap/003_*.sql`, `dbt/models/staging/sources.yml`, `stg_products.sql` |

## Context

The loader (`cli --file …`) COPYs into `bronze.supply_chain_raw` (TEXT, append-only). Exactly one `source()` call exists (`stg_products.sql:43`); all downstream uses `ref('stg_products')`. `sources.yml` already states bronze is "written by src/supply_chain_pipeline, not by dbt". What was unwritten is which sense of "independent" holds: order-free, code-independent, or failure-independent.

## Decision

**1. Runtime-decoupled, contract-coupled via bronze — ordered execution.** `cli.py` never imports or shells to dbt; no dbt node ever calls the loader. The only shared contract is the bronze schema. Execution stays ordered: ingest, then `dbt build`. Independence means no code call across the boundary, not either-order running.

**2. Bronze DDL is the authority; the loader list is a drift check.** `003_create_bronze_supply_chain_raw.sql` is the single truth. `load.py:BRONZE_SOURCE_COLUMNS` is a checked copy that fails fast on drift — not a second contract. Canonical phrase: `runtime-decoupled, contract-coupled via bronze`.

**3. Failure isolation is one-way stale-safe.** Loader exit codes (ADR-005: 0/1/2/3/99) gate bronze only. A failed or aborted batch loads zero rows, so dbt sees only the last SUCCESS batch — stale, traceable via `source_batch_id`, never half a batch. A dbt failure leaves bronze intact; recovery is `dbt build --full-refresh` from bronze, never re-ingest.

**4. The orchestrator is the only place that knows both.** Ordering lives in the runbook / future Airflow DAG (SPEC-001 §10.13, ADR-006 §4), never in either tool. Neither detects, waits for, or retries the other.

**5. Contract change is DDL-first, four-file lockstep.** Edit `003_*.sql` first, then `extract.py:SOURCE_HEADER_ALIASES` + `load.py:BRONZE_SOURCE_COLUMNS` + `sources.yml` / `stg_products.sql` in the same change. Partial updates fail fast (loader drift check; dbt compile/test) rather than silently.

**6. PII boundary survives independence.** Loader keeps `customer_demographics` in bronze (faithful landing; SPEC-001 Q6 gender data). dbt keeps excluding it from gold (ADR-001 §6). Independence never authorises dropping upstream what gold excludes, nor re-admitting downstream what bronze retains.

## Consequences

- Proof is two separate verifications: (a) `cli --file …` populates bronze with dbt never invoked (pinned by `tests/` unit + integration, ADR-006); (b) `dbt build` passes on pre-populated bronze with the loader untouched (22 live dbt tests, `docs/testing/dbt-test-plan.md`).
- Reconciliation spans the boundary without coupling it: `rows_read == rows_loaded + rows_rejected` (loader refuses mismatch, ADR-005) and gold↔silver per-batch counts (dbt singular test); catalogued in `dbt-test-plan.md` §5.
- Grain, withheld measures, and gate unchanged: one row per `sku` (Q4), §12 open items stay withheld, gate stays blocked until owner assigned (Q1). No new consumer, so `dim_product` still carries no contract (ADR-003 rule).
- No GLOSSARY.md change: this is an implementation relation; the glossary is definitions-only.

## Rejected alternatives

- **Order-free (either side first).** Rejected: dbt over empty bronze passes uniqueness trivially (ADR-001) — green proving nothing. Order is a precondition, not a coupling.
- **Loader calls dbt on success.** Rejected: couples Python to dbt venv/profiles; duplicates CI wiring — refused in ADR-006 §4.
- **dbt reads the manifest for freshness.** Rejected: freshness is time-since-last-SUCCESS in the Airflow check (SPEC-001 Q8), not a dbt assertion on `loaded_at` (loader clock, not data age; `sources.yml` comment).
- **Casting in the loader.** Already rejected (ADR-001); ADR-005 adds only a parse check predicting staging failures.
