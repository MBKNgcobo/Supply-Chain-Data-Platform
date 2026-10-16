# ADR-005: Validation failure semantics — quarantine, threshold abort, and what survives

| Field | Value |
| --- | --- |
| Status | Accepted (implementation-level, reversible) |
| Date | 2026-10-15 |
| Relates to | SPEC-001 §7.1–§7.3, §9, §10.5, §10.7, §10.8; source contract `quality` block |
| Scope | `src/supply_chain_pipeline/{extract,validate,load,cli}.py`, `tests/` |

## Context

The pipeline had one row-level check (blank `sku`) and one file-level
idempotency check. Everything else the source contract requires was either
absent or soft: `sku_unique_in_file`, `numeric_columns_parse`, and
`defect_rates_in_range` were listed as required row checks but unimplemented;
the reject-rate threshold existed only as "proposed, not agreed"; row-count
reconciliation (`rows_read == rows_loaded + rows_rejected`) was not asserted
anywhere; duplicate headers were resolved by silently suffixing column names
although SPEC-001 §7.1/§10.5 mark them fatal.

The deeper problem was semantic, not cosmetic. A batch with rejects **succeeded**
and committed its valid rows. That was defensible while nothing downstream
depended on the result, but dbt reads bronze **without consulting the
manifest**: any row committed to bronze is published to gold by the next
`dbt build`, regardless of how many of its siblings were quarantined. So the
real question was never "should rejects fail the batch" — it was "what is
allowed to survive an abort". Three positions were available, and each loses
something:

1. Roll back everything on abort — but then the rejects themselves vanish
   without a `etl.row_rejects` record, violating SPEC-001 §7.2 ("a reject must
   never be dropped without a record").
2. Commit valid rows and mark the batch FAILED — but bronze would contain the
   condemned batch, and dbt would publish it anyway. Silence by another name.
3. Commit rejects only, load nothing, mark FAILED — bronze receives nothing
   from the file, every rejected row is recorded, and the failure is loud.

## Decision

**1. Below the threshold, rejects quarantine and the batch commits.**
Valid rows load, rejected rows land in `etl.row_rejects` with raw payload,
row number, and reason, and the manifest records the counts. This is the
existing reject-handling policy, now bounded.

**2. Above the threshold, the batch ABORTS: rejects persisted, valid rows
never loaded, manifest FAILED.** Default threshold 5.0% (`>5%` fails,
exactly 5% passes), operator-tunable via `cli.py --max-reject-rate-percent`.
A non-positive threshold is refused at the constructor and at argparse —
it would silently disable the gate. The abort path
(`BatchLoader.persist_rejects`) commits the rejects in their own
transaction; the file remains in `data/incoming/`, and because the manifest
duplicate check honours only `SUCCESS`, re-feeding the fixed file needs no
manual cleanup.

**3. `sku_unique_in_file` is a row-level reject: first occurrence wins.**
Later rows carrying the same trimmed `sku` are rejected with a message
naming the first occurrence's row number. `stg_products` would resolve
duplicates downstream by latest-load-wins, but the contract requires the
collision to be visible per row, not absorbed.

**4. Numeric parsing is checked, never performed, in Python.**
A non-blank value in a `NUMERIC_COLUMNS` entry must parse as the type
`stg_products` casts it to (`integer` or numeric); otherwise the row is
rejected. Blank stays null — missing values must remain observable
downstream. Bronze stays TEXT (ADR-001 unchanged). The column-type map
mirrors the staging casts, which are authoritative over the contract's
`target_type` where the two disagree.

**5. Genuine duplicate headers (after normalisation and aliasing) are
fatal.** `extract.py` raises `DuplicateHeaderError` and extraction fails;
the file is rejected, not reshaped. The `Lead times`/`Lead time` pair is
unaffected — they normalise to different names and are resolved by the
alias map.

**6. Reconciliation is asserted in code before commit.**
`load_batch` raises (→ rollback, manifest FAILED) if
`rows_read != rows_loaded + rows_rejected`, so a batch whose counters do not
add up can never be marked SUCCESS (SPEC-001 §10.7).

**7. `defect_rates_in_range` ships dormant.** The check exists with *required*
bounds — no default, because SPEC-001 Q7 has not decided 0–100 vs 0–1, and
the glossary rule is withheld, never guessed. Enabling it is configuration,
not code. `availability` / `inspection_results` domain checks are not built
at all: their shape is undecided, not merely their bounds.

**8. Batch identity: two terms, two meanings.** `batch-<uuid4>` is a *batch
run* — one execution, the manifest's primary key. The *source batch* is the
delivery, identified by the SHA-256 content hash; bronze's `source_batch_id`
column carries the hash (as `003_create_bronze_supply_chain_raw.sql` already
declared). The UUID never reaches bronze. GLOSSARY.md records both terms.

**Exit-code contract** (SPEC-001 §10.8, now pinned by `tests/test_cli.py`):
0 = success or duplicate skip; 1 = extraction fatal; 2 = validation fatal
(zero rows, threshold breach, idempotency error); 3 = load failure
(rolled back); 99 = unhandled.

## Consequences

- A threshold-breaching file produces: manifest `FAILED` with counts and rate
  in `error_message`, N rows in `etl.row_rejects`, **zero** rows in bronze.
  dbt therefore has nothing to publish from that file — the acceptance
  criterion ("fail, rather than silently publishing invalid Gold tables") is
  enforced by absence, not by downstream goodwill.
- The Python exit code gates **bronze**; `dbt build`'s tests gate **gold**.
  That is the whole publish chain today; there is no orchestration joining
  them yet (CI wiring is a separate task, SPEC-001 §10.13).
- Small files now interact differently with the gate than large ones: one
  bad row in a 10-row file is 10% and aborts. This is arithmetically
  unavoidable for any percentage threshold; the CLI flag is the release
  valve, and the failure is visible either way.
- The test suite (`tests/`, 106 tests) pins every path named here, using a
  fake connection — no test needs Postgres. `docs/testing/unit-test-plan.md`
  maps each suite to the check it proves and records what remains unprovable
  (the "decodable" fatal check cannot fire while latin-1 stays in the
  encoding chain) or blocked (Q7).
- `datetime.utcnow()` deprecation warnings under Python 3.14 are pre-existing
  and untouched; migrating to timezone-aware timestamps is a separate change
  with manifest-format implications.

## Rejected alternatives

- **Any-reject-fails (threshold = 0).** Rejected: quarantine exists precisely
  to tolerate row-level noise; one bad row among thousands should not halt a
  delivery, and the rejected rows are already fully recorded.
- **Load valid rows, still mark FAILED.** Rejected: dbt publishes from bronze
  without consulting the manifest, so the "failed" batch would reach
  analysts. A failure that still ships is not a failure.
- **Full rollback with no quarantine.** Rejected: loses the reject records,
  violating SPEC-001 §7.2; the operator would have the file but not the
  diagnosis.
- **Guessing defect-rate bounds (0–100 is plausible).** Rejected: SPEC-001 Q7
  is open; a plausible guess that consumers depend on is expensive to reverse
  (glossary: withheld, never guessed).
- **Casting numerics in the loader.** Already rejected by ADR-001; this ADR
  adds only a parse *check*, which predicts staging cast failures without
  duplicating the cast rules in Python.
- **Suffixing duplicate headers and continuing.** Rejected: once two source
  columns normalise to one bronze name, no mapping is provably right;
  keeping either copy risks mislabelling the other copy's values silently.

