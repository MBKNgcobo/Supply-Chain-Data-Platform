# dbt test plan — warehouse (silver/gold)

Status: implemented and **live-verified** (22 data tests, `dbt build` exit 0
against a populated warehouse). Date: 2026-10-15.
Companion decision record: [ADR-005](../decisions/ADR-005-validation-failure-semantics.md)
(Python-side failure semantics; this file is the dbt-side half).
Python ingestion suite: [unit-test-plan.md](unit-test-plan.md).
End-to-end (real-database) suite: [integration-test-plan.md](integration-test-plan.md)
— its scenarios 7, 8-iv, and 4-b's staging assertion are dbt-stage steps executed
manually in the workflow below (ADR-006 decision 4).
Governing spec: SPEC-001 §7 (quality rules), §7.3 (reconciliation), §7.4 (testing).
Dataset contract: `docs/contracts/datasets/gold-fct_sku_performance.contract.yaml`.

## How to run

```bash
# Database: project-dedicated `supply_chain` on the shared localhost:5432 server.
# Two env-var families must point at it — the loader (DB_*) and dbt (DBT_*) have
# different names and different defaults (pipeline_db vs supply_chain); set both.
export DB_NAME=supply_chain DB_USER=postgres DB_PASSWORD=... DB_HOST=localhost
export DBT_DATABASE=supply_chain DBT_USER=postgres DBT_PASSWORD=... DBT_HOST=localhost

python -m supply_chain_pipeline.cli --file data/supply_chain_data.csv   # populates bronze
cd dbt && dbt build                                                    # run + test everything
```

Toolchain note (2026-10-15): dbt-core 1.12.5 / dbt-postgres 1.11.0, run from an
isolated venv (`~/venvs/dbt-1.12`) because the user-site install was clobbered
by a later dbt 2.0.8 (whose adapter set no longer includes Postgres except
experimentally). `~/.dbt/profiles.yml` is copied from `dbt/profiles.yml.example`
with env-var values — never commit a filled copy.

## The publish gate chain (dbt half)

The acceptance criterion — *data-quality failures are visible and fail the
relevant stage rather than silently publishing invalid Gold tables* — is a
two-stage chain; the Python half is documented in [unit-test-plan.md](unit-test-plan.md).
The dbt half:

1. `dbt build` runs **every** model's data tests interleaved with the run
   (`dbt build` order: a failing error-severity test stops the downstream of the
   model it guards). Gold marts are only built after silver's tests pass.
2. Any error-severity failure → non-zero exit → the operator does not promote/
   publish. **Today this is a documented manual stage**: no CI workflow and no
   DAG exist yet (`.github/workflows/` and `dags/` are empty). CI wiring is
   SPEC-001 §10.13, tracked separately; nothing here claims automated
   enforcement that does not exist.
3. Visibility: `dbt build` output names each failing test, and
   `--store-failures` can persist failing rows to the warehouse for triage.

## Suite map — 22 tests

`dbt ls --resource-type test` on 2026-10-15 reports exactly these, grouped by
where they guard:

| # | Layer | Test | Severity | Asserts |
| --- | --- | --- | --- | --- |
| 1 | source | `source_not_null_bronze_supply_chain_raw_sku` | error | bronze `sku` never null |
| 2 | source | `source_not_null_bronze_supply_chain_raw_source_batch_id` | error | bronze provenance present |
| 3 | source | `source_not_null_bronze_supply_chain_raw_source_identity` | error | row identity present |
| 4 | source | `source_unique_bronze_supply_chain_raw_source_identity` | error | `hash-rownumber` unique per source row |
| 5 | staging | `unique_stg_products_sku` | error | grain: one row per sku |
| 6 | staging | `not_null_stg_products_sku` | error | business key never null |
| 7 | staging | `not_null_stg_products_source_batch_id` | error | provenance carried through the cast boundary |
| 8 | staging | `not_null_stg_products_price` | **warn** | records null-price rate (see §Severity) |
| 9 | staging | `not_null_stg_products_defect_rates` | **warn** | records null-defect-rate (see §Severity) |
| 10 | intermediate | `unique_int_sku_inventory_sku` | error | projection preserves grain |
| 11 | intermediate | `not_null_int_sku_inventory_sku` | error | projection preserves key |
| 12 | intermediate | `not_null_int_sku_inventory_source_batch_id` | error | provenance carried |
| 13 | marts (dim) | `unique_dim_product_sku` | error | dim grain: one row per sku |
| 14 | marts (dim) | `not_null_dim_product_sku` | error | dim key never null |
| 15 | marts (dim) | `not_null_dim_product_source_batch_id` | error | provenance to the delivery |
| 16 | marts (fact) | `unique_fct_sku_performance_sku` | error | fact grain: one row per sku (SPEC-001 Q4) |
| 17 | marts (fact) | `not_null_fct_sku_performance_sku` | error | fact key never null |
| 18 | marts (fact) | `not_null_fct_sku_performance_source_batch_id` | error | provenance; also the reconciliation grouping key |
| 19 | marts (fact) | `relationships_fct_sku_performance_sku → dim_product.sku` | error | every fact row has its dimension |
| 20 | singular | `assert_fct_sku_performance_reconciles_to_silver` | error | per-`source_batch_id` counts match (§Reconciliation) |
| 21 | singular | `assert_order_quantities_non_negative` | error | business rule: a quantity's sign is settled; nulls pass |
| 22 | singular | `assert_defect_rates_within_accepted_range` | **warn** | superset 0–100 bounds (see §Severity) |

## Severity policy — what binds the acceptance criterion

The acceptance criterion binds **error-severity tests only**: those fail
`dbt build` and therefore stop publication. Warn-severity tests are
**visibility checks for semantics that are deliberately undecided** — they
record what the data looks like without asserting a guess (withheld-never-guessed,
ADR-001 §7 / ADR-005). Each warn test has a named promotion trigger:

| Warn test | Why warn today | Promotion trigger |
| --- | --- | --- |
| `not_null_stg_products_price` | A null price is expected in some deliveries; the null *rate* is what needs watching, not a binary pass | Profiling done + owner decides null price is fatal → severity error |
| `not_null_stg_products_defect_rates` | Nulls stay observable downstream (ADR-001 §2) | Same as above |
| `assert_defect_rates_within_accepted_range` | 0–1 vs 0–100 encoding undecided (SPEC-001 Q7); bounds are the deliberate superset | Q7's range answered → set real bounds and severity error |

`accepted_values` is a **deliberate gap**, not an omission: the schema has no
order-status (or any categorical with a documented permitted set), so there is
nothing to assert without inventing a value set — which the withheld-never-guessed
rule forbids. Unblock trigger: profiling completes (or a Q7-style question
answers a column's value domain); at that point add `accepted_values` on the
named column with the documented set.



## Reconciliation — both identities, by stage

| Identity | Stage it belongs to | Where asserted | Status |
| --- | --- | --- | --- |
| `rows_read == rows_loaded + rows_rejected` | **load** (Python, before commit) | `load.py` refuses to commit a mismatched batch; pinned by `tests/test_load.py::TestFailureAtomicity` (ADR-005) | asserted |
| `count(gold.fct_sku_performance) == count(stg_products)` per `source_batch_id` | **dbt publish** (gold vs silver) | singular test `assert_fct_sku_performance_reconciles_to_silver` | asserted |

"Intentionally excluded records" has **no counter** to reconcile: the only
exclusion in this system is the column-level `customer_demographics` exclusion
(ADR-001 §6) — its *absence from gold's schema* is the exclusion, and it is a
structural property of the contract, not a row count.

## Acceptance criterion → named tests (dbt half)

- *"Failures are visible"*: every test names its failing rows when run with
  `--store-failures`; the three singular tests document *what they prove /
  when they fail* in their file headers.
- *"Stage fails, not silent publish"*: any error-severity row of the suite map
  (rows 1–7, 10–21) exits `dbt build` non-zero. Documented manual stage today;
  CI (SPEC-001 §10.13) will make it mechanical.
- *"Reconciliation passes before publish"*: row 20 (dbt side) + the `load.py`
  identity (Python side) — both must hold for a gold table to be considered
  published-eligible; the contract's `publish_gate.requires_reconciliation_passing`
  names the same condition.

## Deliberate exclusions and recorded gaps

1. **`accepted_values`: none exists** — no column has a documented permitted
   set (see §Severity). Blocked on profiling; named above.
2. **`int_sku_inventory` re-tests grain deliberately** (rows 10–12) rather
   than "inheriting" staging's tests: a projection can silently drop rows or
   fan out, and its tests are cheap. This is the opposite of an omission.
3. **`availability` / `inspection_results` domain checks: not built.** Same
   Q7 block as the Python side — count-vs-flag and pass/fail-vs-score are
   undecided; the *shape* of the check is unknown, not merely its bounds.
4. **Freshness: not a dbt test.** The source has no timestamp column; honest
   freshness is "time since last SUCCESS in `etl.batch_manifest`", which
   belongs to the orchestration check, not a dbt freshness assertion
   (`sources.yml` documents this in-file).
5. **No CI, no DAG.** The gate is a documented manual stage (see §Publish gate
   chain); SPEC-001 §10.13 tracks the automation.
6. **Contract↔model drift is checked by convention.** The 24-column
   consistency script (deliverable 04) is not wired into `dbt build`; a new
   contract check must be added to the `checks:` list by hand — done for
   `non_negative` on 2026-10-15.

## Verification (live, 2026-10-15)

Sequence executed end to end, in order:

1. `sql/bootstrap/001–003` run against a fresh `supply_chain` database —
   schemas bronze/silver/gold/etl + tables created.
2. `Supply_chain_data.zip` extracted to `data/supply_chain_data.csv`.
3. `python -m supply_chain_pipeline.cli --file data/supply_chain_data.csv`
   → **exit 0; 100 rows loaded, 0 quarantined** (so the row-count identity
   held on the success path).
4. `dbt parse` → exit 0. `dbt ls --resource-type test` → **22 data tests**.
5. `dbt build` → **exit 0. PASS=26 WARN=0 ERROR=0 NO-OP=2** (28 nodes =
   4 models + 22 tests + 2 exposures). `gold.dim_product` and
   `gold.fct_sku_performance` each materialised with **100 rows**.

Observed on real data: no negative `order_quantities` (row 21 passed); the
three warn-severity tests reported **PASS, not WARN** — the real CSV has no
null `price`, no null `defect_rates`, and all defect rates inside 0–100. The
"null price expected in some deliveries" comment in `stg_products.yml` is
therefore *not* borne out by this delivery; the warn severity stays until
profiling decides, per the promotion table above.

The ADR-001 empty-warehouse caveat ("a test over an empty warehouse proves
nothing") is now retired **for this populated database**: these 22 results
are real. It still applies to any fresh database until a CSV is loaded.

Remaining unverified, unchanged: the `--full-refresh` rebuild claim, and
behaviour under a *changed* source file (only one delivery has ever been
loaded — a second delivery exercises latest-load-wins and the dedup path). The
changed-source-file case is now specified end to end as scenario 5 of
[integration-test-plan.md](integration-test-plan.md) (planned); both remain
unverified until those steps are executed.
