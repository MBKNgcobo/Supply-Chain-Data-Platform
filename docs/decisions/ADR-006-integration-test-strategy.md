# ADR-006: Integration test strategy — dedicated test database, per-test schema recreation, opt-in execution

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-10-15 |
| Relates to | SPEC-001 §7.4 (testing), §10.13 (CI wiring, unchanged); ADR-005 (failure semantics this suite proves); `docs/testing/integration-test-plan.md` (the specification this strategy supports) |
| Scope | `tests/` (new `integration` suite), `pyproject.toml` (marker), test-database provisioning; supersedes the "no Postgres integration tests" position recorded in `docs/testing/unit-test-plan.md` gap 9 |

## Context

Two test layers exist and are documented: 106 Python unit tests that use a
`FakeConnection` and therefore assert only what *would* have been written, and 22
dbt tests live-verified against the populated `supply_chain` warehouse. Between
them sits an unproven claim: that the Python pipeline, against a **real**
Postgres, actually produces the row-level outcomes the acceptance criterion
rests on — a threshold breach leaving bronze with zero rows, a reconciliation
identity holding across a real commit, rejects landing with their payloads
intact. `unit-test-plan.md` gap 9 recorded *"No CI workflow, no Postgres
integration tests. Out of scope by decision"* — that position is reversed by this
ADR, because the acceptance criterion ("data-quality failures are visible and
cause the relevant pipeline stage to fail rather than silently publishing
invalid Gold tables") cannot be fully demonstrated by fakes alone.

The decisions below are test-infrastructure decisions, but they are not
trivial: they determine what every future integration test may assume, how the
suite behaves on a machine without a database (a silent skip would defeat the
acceptance criterion entirely), and which database a test is allowed to touch —
the live `supply_chain` database currently holds live-verified evidence that a
careless test could destroy.

## Decision

**1. Dedicated test database `supply_chain_test`, same server, never the live
database.** A session-scoped fixture creates it on demand and runs
`sql/bootstrap/001–003`. (`etl.batch_manifest` needs no fixture step — the
pipeline creates it at runtime via `setup_manifest_table()`.) The loader's
`DB_*` env vars point at `supply_chain_test` for the integration stage; dbt's
`DBT_*` vars continue to point at the live `supply_chain` database in stage 3.
Tests may never be configured against the live database.

**2. Drop-and-recreate the `bronze` and `etl` schemas before every test.**
Isolation beats speed at this scale (100-row fixtures, ~13 sub-cases): a
failure-path test that passes because the previous scenario left rows behind is
worse than a slow test. Table-definition drift is also caught, since every test
re-runs the bootstrap DDL.

**3. Execution is opt-in via the `integration` marker — and never silently
skips.** Plain `python -m pytest` keeps its existing no-database promise (unit
suite unchanged). `python -m pytest -m integration` requires a reachable
Postgres and **fails** if it is absent. A green run that skipped every
integration test would be a lie of exactly the kind the acceptance criterion
forbids; a missing database must surface as command failure, not as success.

**4. dbt-stage scenarios are documented manual steps, not pytest subprocesses.**
Scenarios 7 and 8-iv of the plan (a dbt test failing after load; the recovery
rerun) are executed in stage 3 (`cd dbt && dbt build`) with their expected
results specified in the plan. Driving dbt from pytest would couple the suite to
the isolated-venv/profiles toolchain note in `dbt-test-plan.md` and preempt the
CI wiring task (SPEC-001 §10.13), where subprocess orchestration belongs.

**5. The plan's scenario matrix is the specification; implementation is a
separate task.** `docs/testing/integration-test-plan.md` (8 scenarios, 13
sub-cases, each with expected result / database changes / batch status /
recovery) is written and reviewed first; fixtures, session fixture, tests, and
marker registration follow it exactly.

## Consequences

- The acceptance criterion gains its real-database half: plan scenario 3-b
  proves zero-row bronze by querying Postgres, not by reading a fake's SQL log;
  this is the sharpest new evidence the third layer adds.
- The unit suite's "no database required" guarantee survives untouched; CI (when
  SPEC-001 §10.13 lands) can select stages explicitly.
- Two pinned-behaviour discrepancies are carried forward as recorded findings,
  not silently absorbed: (a) the source contract's `duplicate_status_value:
  DUPLICATE` / deliverable-01's *"duplicate → status DUPLICATE"* claim versus the
  CLI writing **no** manifest row on re-feed (plan scenario 2); (b) connection
  failure surfacing as exit 99 with no dedicated code (plan scenario 6). Both
  change the SPEC-001 §10.8 / ADR-005 exit-code contract and must be settled
  together, in a separate change.
- `unit-test-plan.md` gap 9 is superseded by this ADR and reworded accordingly.
- The strategy assumes an operator-provided localhost Postgres; it does not
  provision infrastructure. Docker-based ephemeral databases and CI service
  containers remain possible later without changing decisions 2–5.
- New tests must be marked correctly: an integration test committed without the
  marker breaks decision 3's guarantee (it would run — or fail — in the
  database-free unit stage).

## Rejected alternatives

- **Use the live `supply_chain` database with per-test cleanup.** Rejected: it
  holds the live-verified 22-test evidence from 2026-10-15; a bug in a cleanup
  path could destroy it, and tests would race any other session on the server.
  Tests must be free to fail without endangering real data.
- **Docker Compose ephemeral Postgres.** Rejected: no container tooling exists
  in the repository, so it would add infrastructure to the critical path of
  `pytest` for a suite of ~13 tests. The operator's existing server already
  provides everything decision 1 needs; CI can revisit this when SPEC-001
  §10.13 wires the suite into a runner (a service container there is the right
  home for it).
- **Transaction roll-back isolation (each test in a rolled-back transaction).**
  Rejected: the pipeline manages its own transactions and commits
  (`BatchLoader`, `BatchManifestManager`), and DDL behaviour inside rolled-back
  blocks is not a foundation worth reasoning about; it also hides exactly the
  committed-state effects these tests exist to prove.
- **Auto-skip when the database is unreachable (plain `pytest` runs everything,
  skipping without one).** Rejected: a silently skipped integration suite
  produces green runs that proved nothing — the failure mode the acceptance
  criterion names. Absence of the database must be loud.
- **Invoking `dbt build` from pytest for full single-command coverage.** Rejected:
  couples the Python suite to dbt's venv/profiles environment and duplicates the
  CI-wiring task's scope; the manual stage keeps each layer's exit code as its
  own honest signal (decision 4).
- **Reuse the unit suite's fake-connection style and call it integration.**
  Rejected: it would prove nothing the 106 unit tests do not already prove — the
  entire point of this layer is real rows, real commits, real constraints.
