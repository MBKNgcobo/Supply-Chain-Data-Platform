# Integration test plan — end-to-end pipeline against a real database

Status: **implemented 2026-10-15** — 12 pytest tests in `tests/integration/`
(scenarios 1–6, 8; scenario 7 and 8-iv remain documented manual stage-3
steps), wired per ADR-006: `@pytest.mark.integration`, dedicated
`supply_chain_test` database, opt-in `pytest -m integration`.
Date: 2026-10-15.
Companion decision record: [ADR-006](../decisions/ADR-006-integration-test-strategy.md).
Companion suites: [unit-test-plan.md](unit-test-plan.md) (106 tests, no database),
[dbt-test-plan.md](dbt-test-plan.md) (22 tests, live warehouse).
Governing spec: SPEC-001 §7 (quality rules), §7.3 (reconciliation), §7.4 (testing),
§10.7–§10.8. Failure semantics: [ADR-005](../decisions/ADR-005-validation-failure-semantics.md).

This plan specifies the third test layer: the Python pipeline executed against a
**real PostgreSQL database**, so the publish-gate claims that unit tests can only
assert *would-have-been-written* (fake connection) are proven as *actually-happened*
rows. Every scenario below lists the four things an integration test must pin:
expected result, database changes, batch status, and whether the next run recovers.

## How to run

```bash
# Stage 1 — unit suite (unchanged: no database required)
python -m pytest

# Stage 2 — integration suite (opt-in; requires reachable PostgreSQL)
export DB_NAME=supply_chain_test DB_USER=postgres DB_PASSWORD=... DB_HOST=localhost
python -m pytest -m integration

# Stage 3 — warehouse (runs against the LIVE supply_chain database, not the test one)
export DBT_DATABASE=supply_chain DBT_USER=postgres DBT_PASSWORD=... DBT_HOST=localhost
cd dbt && dbt build
```

The two env-var families (`DB_*` for the loader, `DBT_*` for dbt) have different
names and different defaults; the integration stage points `DB_*` at
**`supply_chain_test`** and must never point at the live `supply_chain` database.
Scenarios 7 and 8-iv are dbt-stage events and are executed **manually** as part of
stage 3, not from pytest — see [ADR-006](../decisions/ADR-006-integration-test-strategy.md)
decision 4 and the scenarios below for the exact steps and expected results.

## Test infrastructure (ADR-006 decisions)

| Concern | Decision |
| --- | --- |
| Database | Dedicated `supply_chain_test` on the same localhost server; created on demand. The live `supply_chain` database is never touched by these tests. |
| Bootstrap | Session-scoped fixture runs `sql/bootstrap/001–003` (schemas, `etl.row_rejects`, `bronze.supply_chain_raw`). `etl.batch_manifest` is created by the pipeline itself at runtime (`manifest.setup_manifest_table()`), so no fixture step is needed for it. |
| Isolation | **Drop-and-recreate the `bronze` and `etl` schemas before every test.** At this fixture size recreation is cheap, and a failure-path test must never pass because the previous scenario left rows behind. |
| Execution | Marked `@pytest.mark.integration`; the marker is registered in `pyproject.toml`. Plain `pytest` keeps its no-database promise; a missing database makes `pytest -m integration` fail loudly — **never silently skip** (a green run that proved nothing violates the acceptance criterion). |
| Fixtures | Synthetic, contract-shaped, under `tests/fixtures/` — the real CSVs in the repository root are never read by tests (same convention as the unit suite). The happy-path and below-threshold fixtures are **100 rows** so the 5% reject-rate arithmetic is exact: 1 reject = 1% (passes), 1 reject in a 10-row file = 10% (breaches). |
| dbt | Not driven from pytest. Scenarios 7 and 8-iv are documented manual steps in stage 3 (subprocess + isolated-venv coupling belongs with CI wiring, SPEC-001 §10.13). |

## The publish gate chain (integration half)

The acceptance criterion — *data-quality failures are visible and cause the relevant
pipeline stage to fail rather than silently publishing invalid Gold tables* — is a
two-stage chain; the unit half and dbt half are documented in the companion plans.
What this layer adds is **real-database proof**:

1. **Python gates bronze — proven by querying bronze, not by inspecting a log.**
   Scenario 3-b's abort is asserted with `SELECT count(*) FROM bronze.supply_chain_raw`
   returning **0** after a threshold breach, on a real database.
2. **dbt gates gold — proven by exit code and unchanged table state.**
   Scenario 7 asserts `dbt build` exits non-zero, names the failing test, and leaves
   the previous gold row counts untouched (no rebuild happened).
3. **Visibility is asserted, not assumed:** reject reasons in `etl.row_rejects`
   naming column + value or first-occurrence row, `error_message` on `FAILED`
   manifest rows carrying counts and rate, and dbt's failing-test name on stdout.

## Scenario matrix — 8 scenarios, 13 sub-cases

Terminology follows [GLOSSARY.md](../../GLOSSARY.md): a *batch run* is one execution,
a *source batch* is one delivery identified by the file's SHA-256; `bronze` is
`bronze.supply_chain_raw`, quarantine is `etl.row_rejects`, manifest is
`etl.batch_manifest`. Exit codes: 0 success/duplicate no-op, 1 extraction fatal,
2 validation fatal, 3 load failure, 99 unhandled ([ADR-005](../decisions/ADR-005-validation-failure-semantics.md)).
Default reject-rate threshold: 5.0% (`>5%` aborts, exactly 5% passes).

### 1. A valid file loads correctly

Fixture: `happy_path_100.csv` (100 contract-shaped rows, 0 defects).

| Field | Expected |
| --- | --- |
| Expected result | Exit **0**. `rows_read == rows_loaded + rows_rejected` (100 == 100 + 0) — the reconciliation identity holds on a real commit. |
| Database changes | `bronze.supply_chain_raw` gains **100** rows, each carrying `source_batch_id` = file hash and a unique `source_identity` (`hash-rownumber`); `etl.row_rejects` gains **0** rows. |
| Batch status | One manifest row: **SUCCESS**, `rows_read=100, rows_loaded=100, rows_rejected=0`, `error_message` null. |
| Next-run recovery | Not a failure; but re-feeding the same file is scenario 2 (no-op). |

### 2. The same file is submitted twice

Second submission of the exact bytes of scenario 1.

| Field | Expected |
| --- | --- |
| Expected result | Second run exits **0** as a no-op: `is_already_processed` matches the prior `SUCCESS` hash (`cli.py:102`) and returns before any write. |
| Database changes | **Zero.** No new manifest row, no bronze rows, no rejects. `bronze` still has exactly the scenario-1 rows. |
| Batch status | Unchanged: the **first** run's `SUCCESS` row remains the only record of this source batch. The second execution leaves no manifest trace. |
| Next-run recovery | N/A — nothing to recover from. This is the idempotency guarantee (`idempotency.strategy: file_content_hash`). |

**Finding (recorded, deferred — not a defect of this plan):** the source contract
declares `idempotency.duplicate_status_value: DUPLICATE` and
`docs/deliverable/01-source-profile-and-mapping.md` states *"duplicate → status
`DUPLICATE`, no reload"*, but the CLI never persists a `DUPLICATE` manifest row —
it exits before registering the batch. The `DUPLICATE` status in
`validate.py:275` is unreachable via the CLI path. This test asserts the
**observable truth** (zero DB changes); reconciling contract+deliverable with code
(either record a `DUPLICATE` row or drop the claim) is a separate change, recorded
in [ADR-006](../decisions/ADR-006-integration-test-strategy.md) consequences.

### 3. One malformed record is encountered

Two sub-cases, because the 5% threshold turns file size into opposite outcomes.
Malformation: one row with an unparseable value in a `NUMERIC_COLUMNS` column
(`numeric_columns_parse` check — the reject message names column and value).

**3-a. 1 malformed row of 100 (1% ≤ 5% — below threshold)**

| Field | Expected |
| --- | --- |
| Expected result | Exit **0**. Reconciliation holds: 100 == 99 + 1. |
| Database changes | `bronze` gains **99** rows (the malformed row never lands); `etl.row_rejects` gains **1** row with raw payload preserved exactly, `row_number` = true file position, `error_message` naming column + value. |
| Batch status | **SUCCESS** with `rows_read=100, rows_loaded=99, rows_rejected=1`. |
| Next-run recovery | None needed. A corrected re-delivery is a new source batch (scenario 5). |

**3-b. 1 malformed row of 10 (10% > 5% — threshold breach)**

| Field | Expected |
| --- | --- |
| Expected result | Exit **2**. The stage fails loudly. |
| Database changes | `bronze` gains **0** rows from this file — asserted by `SELECT count(*) FROM bronze.supply_chain_raw` returning **0** against the real database (zero COPYs proven as real rows, not a fake's statement log). `etl.row_rejects` gains **1** row (an aborted batch still quarantines its rejects — SPEC-001 §7.2). |
| Batch status | **FAILED** with counts and rate in `error_message`, e.g. `Reject-rate threshold breached: 1 of 10 rows rejected (10.00% > 5.00%). Batch aborted; no rows will be loaded to bronze.` |
| Next-run recovery | **Yes.** The manifest duplicate check honours only `SUCCESS`, so re-feeding a fixed file needs no manual cleanup. Re-feeding the *unfixed* file fails identically (8-ii). Operator may instead raise `--max-reject-rate-percent` (must be positive — enforced at argparse). |

### 4. A business key is duplicated

Two sub-cases — the same operator sentence, opposite mechanisms.
The business key is `sku` (GLOSSARY: one SKU, one row in current-state datasets).

**4-a. Duplicate `sku` inside one file** — fixture: `duplicate_sku_1of100.csv`
(one SKU appearing twice among 100 rows; 1 reject = 1%, below threshold).

| Field | Expected |
| --- | --- |
| Expected result | Exit **0**. First occurrence wins; the later row is rejected (`sku_unique_in_file`). |
| Database changes | `bronze` gains **99** rows — exactly one row for the duplicated SKU. `etl.row_rejects` gains **1** row whose `error_message` names the duplicated SKU **and the row number of the first occurrence**. |
| Batch status | **SUCCESS** with `100/99/1`. |
| Next-run recovery | N/A. The collision is visible per row, not absorbed (ADR-005 decision 3). |

**4-b. Same `sku` across two deliveries** — two clean 100-row files with
different hashes sharing SKUs.

| Field | Expected |
| --- | --- |
| Expected result | Both runs exit **0**. This is not a reject case at all. |
| Database changes | `bronze` gains **200** rows — two rows per shared SKU, distinct `source_batch_id`; `etl.row_rejects` gains **0** rows. Downstream, `stg_products` resolves to one row per SKU via latest-load-wins (*Current state*) — that assertion is dbt's, run manually in stage 3. |
| Batch status | Two manifest rows, both **SUCCESS** — two batch runs of two source batches, never confused (GLOSSARY *Batch run* / *Source batch*). |
| Next-run recovery | N/A. Re-feeding either file unchanged is a scenario-2 no-op. |

### 5. A previously loaded order is corrected

Restated in the domain's vocabulary: **a corrected re-delivery of previously
loaded data.** The source has no Order entity — the grain is one row per SKU —
so a "correction" is a new file whose bytes differ from an already-loaded
delivery (changed values for existing SKUs), therefore a different SHA-256.
Fixture: `delivery_a.csv` (100 rows), then `delivery_a_corrected.csv`
(same 100 SKUs, changed values in at least one column).

| Field | Expected |
| --- | --- |
| Expected result | Second run exits **0** and *does load* — the hash differs, so idempotency must **not** skip it. |
| Database changes | `bronze` gains **100 more** rows (200 total): both source batches preserved, distinguishable by `source_batch_id` (old hash vs new hash). `etl.row_rejects` gains **0** rows. After a subsequent manual `dbt build` (stage 3), `stg_products` shows the **corrected** values for the shared SKUs (latest-load-wins) and gold reflects them; gold is **unchanged** until that dbt run happens. |
| Batch status | Two manifest rows, both **SUCCESS** — the correction is a new batch run of a new source batch, not an edit of the old one. |
| Next-run recovery | N/A (success path). Re-feeding the corrected file afterwards is a scenario-2 no-op; the original delivery can never be re-loaded by accident, and truth for both versions remains recoverable from bronze. |

### 6. The database becomes unavailable during ingestion

Simulation: override `DB_HOST`/`DB_PORT` to a closed port (e.g. `127.0.0.1:1`)
via environment for one run — deterministic, no Docker, no touching the real
server. Connection refusal raises in `get_db_connection` (`cli.py:45-47`).

| Field | Expected |
| --- | --- |
| Expected result | Exit **99** — asserted as *current* behaviour: connection failure surfaces through the unhandled path. Whether a connection failure deserves its own exit code is a recorded open question (below), not an accident. |
| Database changes | **Zero — and necessarily so.** With the database unreachable, **no manifest row can be written**: batch status is *absent*, not `FAILED`. Nothing was loaded, nothing quarantined, nothing marked. The pre-bootstrapped schemas exist but are untouched. |
| Batch status | **No manifest row exists** for this batch run. The run's `batch-<uuid>` identity was generated but never persisted. |
| Next-run recovery | **Yes, fully.** When the database returns, the same file runs clean (exit 0): the duplicate check finds no `SUCCESS` row, no partial state exists to clean up, and reconciliation holds from zero. |

### 7. A transformation test fails after data is loaded (manual — stage 3)

dbt-stage scenario; executed manually per ADR-006 decision 4. Steps: load a
clean delivery (scenario 1) and run `dbt build` once to establish a passing
baseline; then inject a failure that Python cannot catch but dbt must — e.g.
`UPDATE bronze.supply_chain_raw SET sku = NULL WHERE bronze_row_id = <one row>;`
— and run `dbt build` again.

| Field | Expected |
| --- | --- |
| Expected result | `dbt build` exits **non-zero**; output names the failing error-severity test (`source_not_null_bronze_supply_chain_raw_sku`); silver's failure stops every downstream model in the build order — **gold is not rebuilt**. |
| Database changes | Gold row counts **unchanged from the passing baseline** (still present, still the last-known-good rows). The injected bronze defect remains (tests observe, they do not repair). |
| Batch status | Unchanged — dbt failures are not manifest events; the Python batch stays `SUCCESS` (it legitimately loaded what it was given). The failure lives in `dbt build`'s exit code and test output, per dbt-test-plan's publish-gate chain. |
| Next-run recovery | **Yes.** Fix the data (restore the null), rerun `dbt build` → exit 0, gold rebuilt. **Stale-last-known-good is explicitly acceptable** (agreed decision): the gate blocks *new* publishes; consumers keep reading the previous passing gold until an operator fixes and republishes. Freshness is "time since last SUCCESS", the position already recorded in dbt-test-plan — this plan does not require gold invalidation or truncation. |

### 8. The pipeline is rerun after a failure

Four enumerated sub-cases covering both stages:

**8-i. After the outage (scenario 6), database restored, same file rerun.**
Exit **0**; 100 bronze rows; one `SUCCESS` manifest row; full recovery with no
debris — the outage left nothing behind because nothing could be written.

**8-ii. After a threshold breach (3-b), the *unfixed* file rerun.**
Exit **2** again — **deterministically and identically**: no `SUCCESS` row exists,
so idempotency does not skip it, and it must not partially load on retry.
Bronze still **0**; `etl.row_rejects` still **1** — the unique
`(file_hash, row_number)` index refuses the second quarantine attempt, which
`cli.py` logs without changing the exit code; the manifest gains a second
**FAILED** row (`PENDING` → `FAILED` is recorded per batch run: a new batch run
of the same source batch). A failure that "accidentally" succeeds on retry would
hide the original problem — that is the anti-pattern this sub-case exists to catch.

**8-iii. After a threshold breach (3-b), a *corrected* file rerun.**
Exit **0**; 10 bronze rows (or the corrected file's count); new manifest row
**SUCCESS**; the earlier **FAILED** rows remain as history — replay after
failure needs no manual cleanup (ADR-005 decision 2).

**8-iv. After a dbt test failure (scenario 7), data/model fixed, `dbt build` rerun
(manual — stage 3).**
Exit **0**; gold rebuilt from the now-passing data; the failure and its fix are
visible in dbt's run history (`--store-failures` artifacts from the failed run
remain the diagnosis record).

## Acceptance criterion → scenarios

*Data-quality failures are visible and cause the relevant pipeline stage to fail
rather than silently publishing invalid Gold tables.*

- **"The stage fails"** — proven twice against a real database: **3-b** (validation
  stage: exit 2, `SELECT count(*) FROM bronze...` returns **0** — absence is the
  mechanism keeping a condemned batch away from dbt) and **7** (transformation
  stage: `dbt build` non-zero, gold not rebuilt). **6** adds the infrastructure
  failure path (exit 99, nothing written because nothing could be).
- **"Failures are visible"** — **3-a/3-b** (`etl.row_rejects` reasons naming
  column + value; manifest `error_message` naming counts and rate), **4-a**
  (reject names the duplicated SKU and the first occurrence's row), **7** (dbt
  names the failing test), **8-ii** (the second `FAILED` manifest row keeps the
  original failure observable on replay).
- **"Rather than silently publishing"** — **3-b** proves bronze receives nothing
  (so the next `dbt build` has nothing new to publish), **7** proves gold is not
  rebuilt when a test fails, and the **stale-last-known-good** decision
  (scenario 7) makes explicit what consumers *do* see: the last passing build,
  never a fresh invalid one.
- **"Recovery"** — **8-i/8-iii/8-iv** prove every recorded failure recovers on
  the next run without manual cleanup; **8-ii** proves recovery never
  *masks* the failure.

## Test execution

The straightforward development workflow, in order:

```bash
python -m pytest        # stage 1: unit suite, no database
python -m pytest -m integration   # stage 2: this plan, supply_chain_test
cd dbt && dbt build   # stage 3: warehouse, live supply_chain DB
```

Exit codes of all three stages are the pass signal; there is no orchestration
joining them yet (SPEC-001 §10.13 CI wiring is a separate task) — the chain is a
documented manual procedure, exactly as in the two companion plans.

## Deliberate exclusions and recorded gaps

1. **Implemented 2026-10-15.** The suite lives in `tests/integration/`
   (12 tests, all passing against a real `supply_chain_test` database):
   `test_01_happy_path.py` → scenario 1; `test_02_resubmit.py` → scenario 2;
   `test_03_malformed.py` → 3-a/3-b; `test_04_duplicate_key.py` → 4-a/4-b;
   `test_05_correction.py` → scenario 5; `test_06_outage.py` → scenario 6
   plus the test-database guard test; `test_08_rerun.py` → 8-i/8-ii/8-iii.
   Scenarios 7 and 8-iv remain manual stage-3 steps (exclusion 5). Fixtures
   are generated in-test (`build_row`/`write_csv` in
   `tests/integration/conftest.py`) rather than stored CSVs — identical
   100-row determinism, no fixture files to drift.
2. **Connection failure has no dedicated exit code.** Scenario 6 asserts exit 99
   as current behaviour; whether it deserves its own code (e.g. 4) is an open
   question deferred with the contract's `DUPLICATE` finding — both change the
   pinned exit-code contract of SPEC-001 §10.8 / ADR-005 and must be settled
   together.
3. **The `DUPLICATE` manifest row discrepancy** (scenario 2 finding) is recorded,
   not resolved here: contract + deliverable-01 claim a persisted `DUPLICATE`
   status; the CLI writes none. Reconciling them is a separate change.
4. **No Docker, no CI, no DAG.** Test-database provisioning assumes the operator's
   existing localhost Postgres (ADR-006); CI automation is SPEC-001 §10.13.
5. **No dbt execution from pytest** (ADR-006 decision 4): scenarios 7 and 8-iv
   are manual stage-3 steps; sub-case 4-b's staging assertion likewise.
6. **Warn-severity dbt tests are out of the failure scenarios by design** — a
   warn does not fail the stage (dbt-test-plan §Severity); only error-severity
   failures are in scope for "the stage fails".

## Where each proof lives

| Layer | Suite | Database | Plan |
| --- | --- | --- | --- |
| Component behaviour (fakes) | 106 unit tests | none | [unit-test-plan.md](unit-test-plan.md) |
| End-to-end Python stages (real rows) | this plan, `-m integration` | `supply_chain_test` | this file |
| Warehouse models/tests (real rows) | 22 dbt tests | live `supply_chain` | [dbt-test-plan.md](dbt-test-plan.md) |

No assertion is duplicated across layers deliberately: unit tests pin *what would
be written*, this plan pins *what actually lands*, dbt pins *what gets published*.

