# ADR-003: Gold is contract-first, denormalized, and not yet live-verified

| Field | Value |
| --- | --- |
| Status | Accepted (design-level; verified by `dbt parse` + `dbt compile` only) |
| Date | 2026-10-14 |
| Relates to | SPEC-001 §4, §5, §9, §11 Q1/Q4/Q5/Q6/Q7/Q8; ADR-001; ADR-002 |
| Scope | `dbt/models/marts/*`, `dbt/tests/*`, `docs/contracts/datasets/gold-fct_sku_performance.contract.yaml`, `docs/data-dictionary.md`, `GLOSSARY.md` |

## Context

`dbt/models/marts/` was empty, while `gold-fct_sku_performance.contract.yaml`
already specified a table that did not exist and referenced a `dim_product`
that did not exist either (ADR-001 open item #5, ADR-002 risk R4 — both still
open at the start of this session).

The contract had also **drifted from decisions recorded elsewhere**:

1. It declared `availability` as `text / encoding_pending`. SPEC-001 Q7 is
   answered — `docs/data-dictionary.md` publishes it as INTEGER and
   `stg_products` already casts it.
2. It declared `customer_demographics` with `excluded_from_bi: pending_q6`.
   Q6 is answered: the column holds gender data, and ADR-001 §6 excludes it
   from every published model.
3. It typed the stock/count measures — `stock_levels`, `order_quantities`,
   `production_volumes`, `number_of_products_sold`, `lead_times_1`,
   `lead_times_2`, `manufacturing_lead_time`, `shipping_times` — as `numeric`,
   against the data dictionary's INTEGER. (Four of these were spotted in
   planning; the other four surfaced only during post-write read-back, which
   is the reason decision 1 puts the contract first and Verification includes
   a machine consistency check.)

Two further conditions shaped this ADR. ADR-002 risk R5 (no `exposures`) was
still open: the contract named two consumers that dbt could not see. And
`int_sku_inventory` sat in `intermediate/` with no consumer — the state
ADR-002 says models should not be pre-created in.

Finally, the session's own verification problem: this project owns no
reachable warehouse. `compose.yaml` is 0 bytes, `data/` holds no source file,
and the Postgres listening on localhost:5432 is `jobfinder-db` — a different
project's database. The definition of done chosen for the design (live
`dbt build --full-refresh` from empty, including SPEC-001 §9's rebuild from
bronze) therefore could not be executed, and the scope was set honestly to
`dbt parse` + `dbt compile` instead, with this gap recorded — the precedent
ADR-002 set when it recorded "Not verified: `dbt ls` / `dbt build`".

## Decision

**1. Contract first, then code.** The stale contract was corrected before any
model SQL was written, so contract and model could not disagree on day one.
Implementing against a file known to be wrong, or fixing the file afterward,
were both rejected. The contract remains the approval record; the gate it
declares stays **blocked** (owner unassigned, SPEC-001 draft, profiling not
done). Code complete is not publishable.

**2. The fact is denormalized, exactly as the contract specifies.**
`fct_sku_performance` carries dimensions and measures on one row at one row
per sku; `dim_product` sits alongside as the labelling target of the
contract's `relationships` test, built from the same silver rows rather than
joined in. The strict-star alternative (fact = key + measures only) would
have rewritten the approval record to satisfy an aesthetic preference before
any consumer existed.

**3. `customer_demographics` is removed from the gold schema entirely.** Its
absence from the table *is* the exclusion: a column that physically exists in
a published dataset is one `SELECT *` away from leaking regardless of flags.
The contract records the absence and the reason; staging continues to carry
the column. Neither gold model selects it.

**4. All five contract quality checks became dbt tests.** `unique` /
`not_null` / `relationships` at error severity in the model YAML;
`accepted_range` on `defect_rates` as a singular test at `severity: warn`,
using the superset bounds 0–100 — the only range that catches unambiguous
garbage under both candidate encodings (0–1 fraction or 0–100 percentage)
without encoding the Q7 guess; and the reconciliation rule as a singular test
asserting per-`source_batch_id` row-count equality between gold and silver,
which catches silent row loss and fan-out — the two failure modes a
uniqueness test cannot see.

**5. Both gold models read `ref('stg_products')` directly.** No new
intermediate: there is one upstream model, and a pass-through layer with no
logic in it is the pre-named-structure habit ADR-002 rejects.
`int_sku_inventory` is **kept**, recorded as unconsumed, and reviewed when a
consumer appears.

**6. `dim_product` gets no dataset contract yet.** Its only consumer is the
fact's relationships test, inside an already-blocked gate. A contract is
written when a second consumer appears.

**7. `exposures.yml` declares both contract consumers**
(`powerbi_operational_reporting`, `procurement_analysts`) against
`fct_sku_performance`, closing ADR-002 risk R5 — dbt lineage now reaches past
gold to the reports that depend on it.

**8. Verification: project-level done is the live full-refresh rebuild;
this session verified parse and compile only.** The gap is recorded here, not
silently absorbed: no live warehouse proof has been run, and the first
`dbt build` may still surface issues static compilation cannot see (constraint
application, empty-table trivial passes, `generate_schema_name` against real
schemas).

**9. Documentation split.** Model YAML is canonical for datasets; contract
YAML for grain/gates/ownership; `GLOSSARY.md` for domain terms;
`docs/data-dictionary.md` demoted to a reference of the source file's
delivered types — the authority for the cast boundary, not for any dataset.

Closed by this ADR: ADR-001 open item #5 (`dim_product` missing), ADR-002 R4
(same), ADR-002 R5 (no exposures).

## Consequences

- The gold publish gate remains closed regardless of the code being finished;
  opening it needs an owner, an approved spec, and profiling — none of which
  this ADR provides.
- The reconciliation test's interpretation ("gold is a straight projection of
  silver") is only valid while that stays true. If gold ever aggregates, the
  test must be restated, not deleted.
- Both exposures carry `owner: UNASSIGNED` — the same defect as the contract's
  unassigned owner, only quieter.
- The `defect_rates` range test warns rather than fails; it is promoted to
  error severity when Q7's range question is answered.
- The first live `dbt build` is unproven. Until it runs against a warehouse
  this project owns, every test may still be passing trivially over empty
  tables (the standing ADR-001 caveat).

## Not addressed in this session

| # | Item | Status |
| --- | --- | --- |
| 1 | `bronze.sales_raw` — wrong-schema table still created by `001_create_schemas.sql` | OPEN (ADR-001 #4) |
| 2 | Dataset contract owner unassigned | OPEN (SPEC-001 Q1, blocking) |
| 3 | `compose.yaml`, `pyproject.toml`, `.gitignore`, CI, `dags/`, `dashboards/` empty or missing | OPEN (SPEC-001 Q10 order) |
| 4 | No source CSV in `data/` — nothing is loaded; tests pass trivially over empty tables | OPEN |
| 5 | Live `dbt build --full-refresh` / rebuild-from-bronze | OPEN — pending a warehouse this project owns |
| 6 | `int_sku_inventory` has no consumer | KEPT by decision 5 — revisit when one appears |

## Rejected alternatives

- **Implement against the stale contract, fix it afterward.** Rejected: the
  contract's own header forbade implementing against it, and building against
  a file known to be wrong guarantees a disagreeing pair on day one.
- **Strict star schema: fact = key + measures only, contract rewritten.**
  Rejected: rewrites the approval record for aesthetics before any consumer
  has expressed a need; denormalized fact + dimension satisfies Power BI's
  star requirement either way.
- **Keep `customer_demographics` in gold with `excluded_from_bi: true`.**
  Rejected: publishes PII into the published layer on purpose; flags are
  advisory, absence is enforceable.
- **Leave `excluded_from_bi: pending_q6`.** Rejected: makes an answered
  question look unanswered, which is worse than either alternative.
- **Soften the acceptance criterion to compile-only.** Rejected: quietly
  lowers the bar; the live rebuild remains the definition of done, deferred
  and recorded instead.
- **Delete `int_sku_inventory`.** Rejected: working, documented code with a
  plausible future consumer; flagging it costs one table row, deleting costs a
  rebuild.
- **Write a `dim_product` contract now.** Rejected: a second blocked gate to
  maintain with no owner to process it.
- **Validate against the Postgres on localhost:5432.** Rejected: that is
  `jobfinder-db`, another project's database. Creating bronze/silver/gold
  schemas there would be cross-project contamination, not validation.

## Verification

Static, against the repository on 2026-10-14, with dbt-core 1.12.5 and
dbt-postgres 1.11.0 (the adapter was **not** installed at session start and
was installed during it; nothing about the profile or credentials was
committed — validation used a throwaway profile in `%TEMP%`):

- `dbt parse` — exit 0, full parse, no deprecation warnings after nesting the
  `relationships` test arguments under `arguments:` (dbt 1.12 deprecation).
- `dbt compile --no-populate-cache` — exit 0. `--no-populate-cache` was
  required because no warehouse this project owns is reachable; the flag
  works around the connection attempt only, not around any model — none of
  them use introspective SQL.
- 25 compiled artifacts under `dbt/target/compiled/`: 4 models, 19 generic
  test SQL files, 2 singular test SQL files.
- `dbt ls` — 4 models, 21 data tests, 1 source, 2 exposures, including
  `unique_fct_sku_performance_sku`,
  `relationships_fct_sku_performance_sku__sku__ref_dim_product_`,
  `unique_dim_product_sku`, and both `assert_*` singular tests.
- Contract↔model consistency script — 24 columns in each, names and types
  agree. Its first run failed on the four `numeric`-vs-`integer` drifts found
  in read-back; it passes after correction.

Not verified: execution of any SQL against a database; `dbt test` results
against real data; constraint enforcement at materialisation time; behaviour
on a populated warehouse. See decision 8 and the Not Addressed table.





