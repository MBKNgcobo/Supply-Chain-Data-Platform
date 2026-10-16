# ADR-008: Airflow orchestration — ordered DAG with transient-only retries

| Field | Value |
| --- | --- |
| Status | Accepted (implementation-level, reversible) |
| Date | 2026-10-15 |
| Relates to | SPEC-001 §10.13; ADR-005 (exit codes); ADR-007 (decoupling); SPEC-001 Q8 (schedule) |
| Scope | `dags/supply_chain_pipeline.py` |

## Context

ADR-007 decoupled ingestion and dbt at runtime and named the orchestrator as
the only place that knows both. No DAG existed, so ordering, retries,
timeouts, overlap prevention, and reporting were all unwritten.

## Decision

1. **Five tasks, linear chain:** `discover -> ingest -> transform ->
   validate -> report`. dbt order comes from its own `ref()` graph via a
   single `dbt build`; the DAG never re-encodes model order.
2. **Manual first:** `SCHEDULE = None`. Flip to `"@daily"` once batch
   discovery + incremental ingestion are reliable (Q8).
3. **No overlapping runs:** `max_active_runs=1`, `catchup=False`.
4. **Retries are transient-only.** Exits 3/99 and DB/dbt connection errors
   retry (2 retries, exponential backoff 5m→30m). Exits 1/2 (missing column,
   dup header, zero rows, reject-rate breach) and `dbt test` failures raise
   `AirflowFailException` / run with `retries=0` — a deterministic DQ error
   must never be retried indefinitely.
5. **Timeouts everywhere:** discover 5m, ingest 30m, transform 30m, validate
   20m, report 5m. A hung worker cannot block the next day.
6. **Report always runs** (`trigger_rule=all_done`): outcome, per-task
   states, ingest summary, manifest row counts, errors — in logs and XCom.

## Consequences

- Empty discovery skips (not fails); duplicate hash is a no-op exit 0.
- Failed/aborted batches load zero bronze rows, so dbt sees only the last
  SUCCESS batch (stale-safe per ADR-007).
- Run on Windows dev: Airflow 3.x DagBag import is broken in this venv
  (pre-existing `ObjectStoragePath` import error); DAG verified by
  `py_compile` + AST task-id check. Run `airflow dags test` on Linux/WSL.
