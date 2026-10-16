# Gold warehouse: facts, dimensions, and Power BI consumption guide

| Field | Value |
| --- | --- |
| Status | **DESIGN-LEVEL — the publish gate is CLOSED** (`gold-fct_sku_performance.contract.yaml` → `publish_gate.current_gate_state: blocked`; owner UNASSIGNED per SPEC-001 Q1; SPEC-001 draft; profiling not done). Code complete is not publishable. Nothing here may be consumed until the gate opens. |
| Date | 2026-10-14 |
| Relates to | SPEC-001 §2, §4, §10, §11, §12; ADR-001; ADR-002; ADR-003; ADR-004; ADR-007 |
| Scope | This document only. Machine-readable records stay where they are: dataset contract in `docs/contracts/datasets/`, column docs in `dbt/models/marts/*.yml`, consumers in `dbt/models/marts/exposures.yml`. |

## 1. Business questions supported

Stated verbatim as the selected question set (SPEC-001 §2, *Definition of done*):

1. **Which** product types / suppliers / shipping carriers / transportation modes are **underperforming** on revenue, cost, stock, and defect rate?
2. **Which SKUs drive that result?** — the drill-through that answers "why".

No other question is designed for. A dataset that answers an undesigned question by accident is not a feature.

## 2. Datasets

### 2.1 Fact: `fct_sku_performance`

| Field | Value |
| --- | --- |
| Grain | One row per `sku` — current state of the most recent successful load (SPEC-001 Q4: SKU unique) |
| Shape | Denormalized: six dimension labels and all measures on one row (ADR-003 decision 2 — the strict star was rejected and stays rejected) |
| History | Type 1 overwrite keyed on `sku` (SPEC-001 Q5); prior state recoverable from bronze via `source_batch_id` |
| Time | **None.** The source has no date column (SPEC-001 A4). There is no date dimension, no pseudo-date, and no trend or period-over-period reading — by design, not by omission |
| Provenance | `source_batch_id` — SHA-256 of the source file that produced the row; also the grouping key of the reconciliation test |
| Contract | `docs/contracts/datasets/gold-fct_sku_performance.contract.yaml` (DRAFT, gate blocked) |
| Tests | unique + not_null (`sku`), not_null (`source_batch_id`), relationships to `dim_product.sku`, `dbt/tests/assert_*` singular tests |

Columns (24, contract order): `sku`, `product_type`, `supplier_name`, `shipping_carriers`, `transportation_modes`, `location`, `routes`, `price`, `revenue_generated`, `costs`, `shipping_costs`, `manufacturing_costs`, `number_of_products_sold`, `order_quantities`, `stock_levels`, `production_volumes`, `defect_rates`, `lead_times_1`, `lead_times_2`, `manufacturing_lead_time`, `shipping_times`, `availability`, `inspection_results`, `source_batch_id`.

Deliberately **absent**: `customer_demographics` — SPEC-001 Q6 answered (holds gender data); excluded from every published model per ADR-001 §6. Its absence from the schema *is* the exclusion; it is not selectable.

### 2.2 Dimension: `dim_product`

| Field | Value |
| --- | --- |
| Grain | One row per `sku` |
| Columns | `sku` + the six text labels (`product_type`, `supplier_name`, `location`, `shipping_carriers`, `transportation_modes`, `routes`) + `source_batch_id` |
| Built | From the same silver rows as the fact, not joined into it — agreement between the two is enforced by the fact's `relationships` test, not by construction (ADR-003 decision 2) |
| Contract | **None yet, by rule.** ADR-003: a contract is written when a consumer other than the fact's relationships test appears. Power BI does not import this table (§3.1), so the rule has not fired |

### 2.3 Dimensions deferred: `dim_supplier`, `dim_carrier`

SPEC-001 §4 proposes them; ADR-002 decision 3 names them as the destination for a future supplier/carrier split. **They are not built, and question 1 does not require them**: the denormalized fact already carries `supplier_name`, `shipping_carriers`, and `transportation_modes` as groupable columns.

**Build trigger — both recorded so the deferral cannot be mistaken for an oversight.** Build either dimension when one of these is true:

- a consumer needs an entity attribute the fact cannot carry (supplier location, rating, contract terms); or
- a second fact appears that must conformed-join to the same entity.

Absent either trigger, the dimensions would be a second copy of a name column.

### 2.4 Freshness and the missing time dimension

Freshness cannot be measured on business data (no business timestamp exists). It is measured as *time since the last `SUCCESS` row in `etl.batch_manifest`* (daily 06:00 UTC target, SPEC-001 Q8).

Statement to publish to every consumer:

> This dataset reflects the most recent successful load of a manually supplied CSV file. It is not a live feed and cannot be guaranteed current.

## 3. Power BI consumption guide

### 3.1 Entry point: the fact alone

Power BI imports **`fct_sku_performance` as a single table, zero relationships** (ADR-004).

- Do **not** import `dim_product`. Its six labels duplicate the fact's exactly; the join can only return labels already present, and it would make `dim_product` a consumed dataset, obligating a contract (ADR-003 rule) for no analytical gain.
- Do **not** create relationships between the fact and anything else. There is nothing to relate to.
- Import mode: Import (the source is a static file refreshed daily; DirectQuery buys nothing).

### 3.2 Label table

Every field, its recommended display name, and its status. Status meanings:

- **`publishable`** — safe as a visual field and/or in measures under the given caption.
- **`label-pending`** — value is usable for grouping/filtering, but a caption implying a *definition* must not ship until the listed decision closes.
- **`do-not-use-in-measures`** — forbidden as an aggregate input entirely, for the stated reason.

| Column | Display name | Status | Reason / blocking decision |
| --- | --- | --- | --- |
| `sku` | SKU | publishable | Drill-through key (§3.4) |
| `product_type` | Product type | publishable | As delivered; no category mapping applied |
| `supplier_name` | Supplier | publishable | As delivered |
| `shipping_carriers` | Shipping carrier | publishable | As delivered |
| `transportation_modes` | Transport mode | publishable | As delivered |
| `location` | Location | label-pending | Source string; meaning unverified |
| `routes` | Route | publishable | As delivered |
| `price` | Unit price | publishable | Row context only — never sum across SKUs |
| `revenue_generated` | Revenue *(definition pending)* | label-pending | Gross vs net UNDEFINED (SPEC-001 §12) |
| `costs` | Cost *(definition pending)* | label-pending | Includes shipping? UNDEFINED (SPEC-001 §12) |
| `shipping_costs` | Shipping cost | publishable | Own column; no definition pending |
| `manufacturing_costs` | Manufacturing cost | publishable | Own column; no definition pending |
| `number_of_products_sold` | Units sold | publishable | |
| `order_quantities` | Order quantity | publishable | |
| `stock_levels` | Stock on hand | publishable | |
| `production_volumes` | Production volume | publishable | |
| `defect_rates` | Defect rate (per SKU) | label-pending | Range UNVERIFIED — 0–1 vs 0–100 (SPEC-001 Q7). Per-SKU values and comparisons are safe; the caption must not imply a unit |
| `lead_times_1` | *(no caption approved)* | do-not-use-in-measures | First colliding header `Lead times`; Q3 open — never sum or coalesce with `lead_times_2` |
| `lead_times_2` | *(no caption approved)* | do-not-use-in-measures | Second colliding header `Lead time`; Q3 open — same rule |
| `manufacturing_lead_time` | Manufacturing lead time | publishable | |
| `shipping_times` | Shipping time | label-pending | Unit (days/hours) unverified |
| `availability` | *(no caption approved)* | do-not-use-in-measures | Count vs 0/1 flag undecided (SPEC-001 Q7) — no boolean predicate or aggregate anywhere |
| `inspection_results` | *(no caption approved)* | do-not-use-in-measures | Pass/fail vs numeric score unconfirmed (SPEC-001 Q7) |
| `source_batch_id` | Source batch | publishable | Provenance filter; traces every figure to one file delivery |

`customer_demographics` does not appear because it does not exist in the schema (§2.1).

### 3.3 Measures

**The measure catalogue now lives in `docs/metric-definitions.md`** — canonical
for every metric's formula, required source fields, aggregation grain,
additivity, DAX rendering, and limitations: the supported measures (its §3),
the withheld list (its §4), and the columns that may never be aggregated
(its §5). The Gold-layer exposure for supported measures is the fact table
itself — measure columns in `gold.fct_sku_performance` aggregated exactly as
this catalogue defines, rendered in Power BI as the DAX in its §3 (ADR-004
pattern). No dbt semantic-layer twin exists: on dbt 1.12.5 it requires a
DAY-grain time spine plus a time dimension the source has no date column for
(SPEC-001 A4) — see the catalogue's §6.

This guide keeps the consumption side: per-column captions and status remain
in §3.2 above; drill-through is §3.4; the don'ts are §3.5. The catalogue owns
the captions of the ten supported metrics, so a caption change lands in both
documents **in the same PR**.

The mean-defect-rate caption — load-bearing because it states the aggregation
method — and its full reasoning now live with the metric itself
(`docs/metric-definitions.md` §3.10).

**Withheld measures moved with them.** The withheld table — each measure
listed with the definition it is blocked on, so nobody re-derives it by hand —
is now `docs/metric-definitions.md` §4, and the rule that governs it lives
there too:

- A derived measure appears in a report only when its blocking definition
  closes **and** `docs/metric-definitions.md` is updated in the same change.

### 3.4 Drill-through

Question 2 is answered **within the same table** — no second fact, no finer grain (the source has no events to grain on):

```
Supplier / carrier / mode / product-type aggregate page
        │  user selects a group
        ▼
SKU detail page  ← drill-through, key = [sku], keep-all-filters ON
```

The SKU page shows the row's measures and labels; `source_batch_id` in a tooltip lets the reader trace the figures to one file delivery.

### 3.5 Don'ts

1. Don't relate the fact to `dim_product` (§3.1).
2. Don't build date intelligence — there is no date (§2.4).
3. Don't sum `price` across rows; don't sum or coalesce `lead_times_1`/`lead_times_2`.
4. Don't predicate on `availability` or `inspection_results`.
5. Don't publish any withheld measure under a guessed definition (§3.3).
6. Don't present the dataset as live — carry the §2.4 statement in the report footer.


## 4. Architecture review (data-architect persona)

Reviewed against the repository state on 2026-10-14. Required evidence used: SPEC-001 (problem statement), `gold-fct_sku_performance.contract.yaml` + source contract (boundary contracts), the written flow in ADR-001/002/003 (architecture), `exposures.yml` + this document (consumers), SPEC-001 §9 (replay/backfill).

### 4.1 Strengths — preserve these

- **Grain is stated, tested, and traceable.** One row per `sku`, asserted by `unique`/`not_null`; every row carries `source_batch_id`, so any figure traces to one file delivery.
- **Contracts precede code.** The stale contract was corrected *before* the model was written (ADR-003 decision 1); contract↔model consistency was machine-checked, and that check found four type drifts a human review had missed.
- **The publish gate is real.** Code-complete is distinguished from publishable everywhere it matters; the gate's unmet conditions are named, not implied.
- **PII exclusion is structural, not advisory.** `customer_demographics` is absent from the gold schema; absence is enforceable, flags are not (ADR-003 decision 3).
- **Undefined semantics are quarantined, not guessed.** The label table (§3.2) and withheld-measures list (§3.3) turn SPEC-001 §12's open questions into an enforced boundary rather than a comment nobody reads.
- **Layer responsibilities are clean.** Bronze survives, silver conforms once, gold publishes only — no table mixes raw survival with business-facing curation (ADR-001/002).
- **Ingestion and dbt are runtime-decoupled, contract-coupled via bronze (ADR-007).** The loader never calls dbt and dbt never calls the loader; the bronze DDL is the only shared contract. Run order is ingest → `dbt build`, sequenced by the runbook / future DAG alone. Failure is one-way stale-safe: a failed batch loads zero rows so gold stays on the last SUCCESS batch; a dbt failure rebuilds from bronze, never re-ingests.

### 4.2 Risks — ordered by operational impact

| # | Risk | Impact |
| --- | --- | --- |
| 1 | No owner anywhere (SPEC-001 Q1, UNASSIGNED). The gate cannot open and nobody can approve a metric definition | Blocks publication entirely; every other risk below is downstream of this |
| 2 | ~~No source CSV in `data/`; nothing is loaded, so all tests pass trivially over empty tables~~ **Resolved 2026-10-15:** `Supply_chain_data.zip` extracted to `data/supply_chain_data.csv` and loaded into the project-dedicated `supply_chain` database (shared localhost server); live `dbt build` executed — evidence in `docs/testing/dbt-test-plan.md` §Verification | Behaviour on populated data is now observed, not unproven (closes the ADR-003 deferral); profiling still cannot start — owner still unassigned (risk 1) |
| 3 | Five column semantics undefined (revenue, costs, defect range, availability, lead-time collision) | Withholds margin and unit economics from v1; invites consumers to re-derive them by hand if the report looks "incomplete" |
| 4 | `dim_product` and the fact are two independent projections of `stg_products` that agree only via one relationships test | Safe today (one source, one test); becomes a real divergence risk the day silver gains a second source |
| 5 | This guide documents a design no consumer has yet validated (exposures exist but are `maturity: low`, gate closed) | The guide may not match how analysts actually query; cheap to correct while the gate is closed, expensive after publication |
| 6 | Replay-from-bronze is blocked (bronze schema ≠ profiled source, SPEC-001 §9) | A full rebuild — the recovery path a published warehouse needs — has no working path today |

### 4.3 Missing contracts, ownership, lineage, recovery

- **Ownership:** none assigned — blocks the gate (risk 1). Exposure `owner.name` is UNASSIGNED for the same reason; both must be named together when the time comes.
- **Contract:** `dim_product` has none — correct under the ADR-003 rule; re-evaluated only if §3.1's entry point changes.
- **Contracts elsewhere:** `dim_supplier`/`dim_carrier` deferred with recorded triggers (§2.3) — no phantom contracts for tables that don't exist (ADR-003 rejected that explicitly).
- **Lineage:** column-level source→bronze→silver→gold lineage not implemented (SPEC-001 §8); row-level provenance via `source_batch_id` is implemented and tested.
- **Recovery:** rebuild-from-bronze blocked until bronze matches the profiled source schema (SPEC-001 §9, ADR-001 open item 4); live `dbt build` executed 2026-10-15 against the populated `supply_chain` database — the `--full-refresh` rebuild claim is still unverified.
- **Reconciliation:** both identities are asserted and catalogued in [dbt-test-plan.md](testing/dbt-test-plan.md) §5 — gold↔silver per-batch counts (dbt singular test) and `rows_read == rows_loaded + rows_rejected` (`load.py` refuses to commit a mismatch, ADR-005; pinned by `tests/test_load.py`).

### 4.4 Smallest safe next step

**Assign the dataset owner (SPEC-001 Q1).** One name closes the single condition that blocks every other gate item — no profiling can be approved, no metric definition settled, no exposure matured without them. Immediately after: put the real CSV in `data/` and run the pipeline once, so the profiling gate condition and the "tests pass trivially" risk (2) both become measurable. Everything in §3's label table that is `label-pending` or `do-not-use-in-measures` is then resolvable against real values instead of remaining documented ignorance.

---

*Design decisions in this document are recorded in ADR-001 through ADR-004; open questions are numbered against SPEC-001 §11. If code and this document disagree, the disagreement is a defect — same rule as `GLOSSARY.md`.*
