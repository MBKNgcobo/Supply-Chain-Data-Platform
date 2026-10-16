# Unit test plan — Python ingestion pipeline

Status: implemented (106 tests, all passing). Date: 2026-10-15.
Companion decision record: [ADR-005](../decisions/ADR-005-validation-failure-semantics.md).
Governing spec: SPEC-001 §7 (quality rules), §10.7–§10.8.
Source contract: `docs/contracts/sources/supply_chain_data-csv.contract.yaml`.

## How to run

```bash
python -m pip install -e ".[dev]"
python -m pytest            # coverage report printed on every run (non-gating)
```

No database is required. Every test uses `FakeConnection` / `FakeCursor`
(`tests/conftest.py`), which record SQL statements, COPY payloads, commits,
and rollbacks, so a test asserts exactly what *would* have been written.
Fixtures under `tests/fixtures/` are synthetic and contract-shaped; the real
supply-chain CSVs in the repository root are never read by tests.

## The publish gate chain (how "invalid Gold" is prevented)

The acceptance criterion — *data-quality failures are visible and fail the
relevant stage rather than silently publishing invalid Gold tables* — rests
on a two-stage chain:

1. **Python gates bronze.** A failed stage (exit code 1/2/3) means bronze
   receives nothing from the file. The `test_cli.py` threshold-breach test
   asserts **no COPY was issued** for a condemned batch — dbt reads bronze
   without consulting the manifest, so "nothing in bronze" is the mechanism
   keeping a bad batch away from gold.
2. **dbt gates gold.** `dbt build` runs not_null/unique/relationship tests
   and the reconciliation test. The dbt-side suite — inventory, severity
   policy, promotion triggers, and live-verification evidence — is documented
   in [dbt-test-plan.md](dbt-test-plan.md).

There is no orchestration joining the two stages yet (SPEC-001 §10.13 CI
wiring is a separate task); today the chain is a documented procedure.

## Suite map

| Suite | Proves | Contract / spec reference |
| --- | --- | --- |
| `test_extract.py::TestFileLevelValidation` | missing file, wrong extension, empty file, garbage bytes, header-only file | fatal_checks: file_exists, csv_extension, non_empty, header_row_present, at_least_one_row (§7.1) |
| `test_extract.py::TestHeaderNormalisation` | snake_case rules, `Lead times`/`Lead time` alias, blank header → `unnamed_column_N`, duplicate headers fatal listing every collision | duplicate_header_names_absent (§7.1, §10.5); Q3 |
| `test_extract.py::TestSchemaValidation` | extractor column list == `BRONZE_SOURCE_COLUMNS` in order; required headers in either spelling | §3.1; bronze DDL 003 |
| `test_extract.py::TestRows` | positional read survives ragged rows; UTF-8 BOM tolerated | §7.1 |
| `test_extract.py::TestHashingAndDuplicates` | SHA-256 matches hashlib, stable across paths, changes on one byte; known hash flagged duplicate | idempotency.strategy: file_content_hash |
| `test_validate.py::TestBatchIdentityConstruction` | `source_identity = file_hash-row_number`; identical content → identical identity; 1-based true file positions | traceability of Reject / Source batch |
| `test_validate.py::TestMissingRequiredFields` | blank/whitespace/missing `sku` → reject with named reason, raw payload preserved | row_checks: sku_not_null |
| `test_validate.py::TestSkuUniquenessInFile` | keep-first-reject-rest; trimmed comparison; distinct skus pass | row_checks: sku_unique_in_file |
| `test_validate.py::TestNumericParsing` | unparseable → reject naming column + value; decimal-in-integer rejected; comma-grouped rejected; NaN/Infinity rejected; blank = null; no range guessed | row_checks: numeric_columns_parse |
| `test_validate.py::TestDefectRateBounds` | dormant by default (Q7); configured bounds reject out-of-range, accept boundaries; inverted bounds refused | row_checks: defect_rates_in_range (dormant) |
| `test_validate.py::TestRejectRateThreshold` | exactly 5.0% passes, 10% aborts with FAILED manifest, override works, non-positive refused | max_reject_rate_percent (ADR-005 §2) |
| `test_validate.py::TestFileLevelFailures` | extraction failure fatal; zero rows fatal; prior SUCCESS hash → DUPLICATE; offline mode | §7.2 batch abort rules |
| `test_load.py::TestHappyPathLoad` | COPY shape (provenance first, bronze order), `source_batch_id` == file hash, rejects jsonb-serialised, manifest SUCCESS after commit, unknown columns dropped with warning | reconciliation_required |
| `test_load.py::TestGuardsRefuseUnsafeLoads` | FAILED validation never COPY'd; DUPLICATE is a success no-op | ADR-005 §2 |
| `test_load.py::TestFailureAtomicity` | reconciliation mismatch → rollback + FAILED; COPY failure → rollback + FAILED | §9 "partially loaded batch must never commit" |
| `test_load.py::TestPersistRejects` | abort path commits rejects only; quarantine failure raises, never silently drops | §7.2 reject policy |
| `test_manifest.py` | idempotency query, PENDING→FAILED/SUCCESS transitions, offline mode, last-successful-run mapping | idempotency.store: etl.batch_manifest |
| `test_cli.py::TestExitCodes` | exit matrix 0/1/2/3/99 + duplicate skip; **threshold breach quarantines 4 rejects, issues zero COPYs** | §10.8; acceptance criterion |
| `test_cli.py::TestThresholdFlag` | `--max-reject-rate-percent` plumbing; duplicate-sku file passes at raised threshold, breaches at default | ADR-005 §2 |

## Acceptance criterion → named tests

- *"Failures are visible"*:
  `test_blank_sku_is_rejected_with_named_reason`,
  `test_unparseable_numeric_is_rejected_naming_column_and_value`,
  `test_over_threshold_records_failed_in_manifest`,
  `test_rejects_are_quarantined_and_batch_commits`.
- *"Stage fails, not silent publish"*:
  `test_threshold_breach_exits_two_quarantines_and_never_copies` (zero COPYs),
  `test_failed_validation_is_never_copied`,
  `test_reconciliation_mismatch_rolls_back_and_fails_manifest`,
  `test_duplicate_headers_fail_extraction_whole_file`.
- *"Replay is safe"*:
  `test_identical_file_revalidation_yields_identical_identities`,
  `test_already_processed_hash_exits_zero_as_noop`.

## Deliberate exclusions and recorded gaps

These absences are decisions or known limits, not oversights. Each was
settled in the 2026-10-15 design review (ADR-005).

1. **Date parsing: nothing to test.** The source has no temporal column
   (`has_temporal_column: false` in the source contract; SPEC-001 Q5). The
   only datetime usage is `utcnow()` timestamps on manifest/reject rows,
   which is recording, not parsing. Freshness can only ever be reported as
   "time since last successful load" until the source changes.
2. **Numeric casting is dbt's job; Python only checks parseability.**
   Bronze stays TEXT (ADR-001). `validate.NUMERIC_COLUMNS` mirrors the
   `stg_products` cast types and rejects what staging could not cast.
3. **Contract/staging type drift.** The contract marks `stock_levels`,
   `order_quantities`, `shipping_times`, `production_volumes`,
   `manufacturing_lead_time` as `target_type: numeric`, but `stg_products`
   casts them `::integer`. The check follows staging (what would actually
   fail). Flagged for SPEC-001 Q7 to close; this drift is a contract defect.
4. **The "decodable" fatal check cannot fire.** The encoding chain
   (`utf-8-sig → utf-8 → latin-1 → cp1252`) includes latin-1, which decodes
   *every* byte sequence, so `UnicodeDecodeError` is unreachable in practice.
   Garbage files are still caught — by the required-header check — but the
   contract should stop listing "decodable" as an independent gate until the
   chain changes. `test_binary_garbage_fails_via_required_headers` documents
   this rather than faking a decode failure.
5. **`availability` / `inspection_results` domain checks: not built.**
   Blocked on SPEC-001 Q7 — count-vs-flag and pass/fail-vs-score are
   undecided, so the *shape* of a domain check is unknown, not merely its
   bounds.
6. **`defect_rates_in_range` is built but dormant.** No default bounds —
   guessing would violate the withheld-never-guessed rule. Enabling it is a
   `BatchValidator(defect_rate_bounds=(low, high))` configuration change.
7. **`datetime.utcnow()` deprecation warnings** under Python 3.14 are
   pre-existing; migrating to timezone-aware timestamps touches manifest
   semantics and is deliberately not part of this change.
8. **Coverage is reported, never gated.** A threshold number chosen before
   the real file is profiled would be a guess. Current: extract 94%,
   load 96%, validate 88%, manifest 87%, cli 71% (uncovered cli lines are
   the real psycopg2 connection and argparse wiring).
9. **No CI workflow; Postgres integration tests implemented (unit stage still
   has none).** The "no Postgres integration tests" position recorded here was
   superseded on 2026-10-15 by [ADR-006](../decisions/ADR-006-integration-test-strategy.md):
   the end-to-end suite is specified in
   [integration-test-plan.md](integration-test-plan.md) and implemented in
   `tests/integration/` (12 tests, `-m integration` opt-in, dedicated
   `supply_chain_test` database). CI automation remains tracked separately (SPEC-001 §10.13).

