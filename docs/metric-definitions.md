# Metric definitions: the supported measure catalogue

## 1. Status, date, scope

| Field | Value |
| --- | --- |
| Status | **DESIGN-LEVEL — the publish gate is CLOSED** (`docs/contracts/datasets/gold-fct_sku_performance.contract.yaml` → `publish_gate.current_gate_state: blocked`; owner UNASSIGNED, SPEC-001 Q1; profiling not done). *Supported* does **not** mean publishable: every metric here inherits the dataset's gate. Code complete was never publishable (ADR-003). |
| Date | 2026-10-14 |
| Relates to | SPEC-001 §2, §11 Q3/Q7, §12; ADR-003; ADR-004; `docs/gold-warehouse.md` §3; `GLOSSARY.md` (*Measure*, *Metric*, *Direct aggregation*, *Withheld measure*) |
| Scope | Canonical home for **metric formulas, required source fields, aggregation grain, additivity, and limitations**. No dbt semantic-layer twin exists (see §6, limitation 1). `docs/gold-warehouse.md` §3.3 is a pointer to this document for measures; its §3.2 label table and Power BI consumption guidance remain its own. The dataset contract governs schema, not metrics, and is deliberately not edited alongside this file. |

## 2. How to read this catalogue

**Status vocabulary** — exactly three values, mapping 1:1 onto
`docs/gold-warehouse.md` §3.2's field-guide statuses. No fourth status may
creep in.

| Status | Meaning |
| --- | --- |
| `supported` | The formula is a settled *direct aggregation* (glossary): one column, one aggregate function, no meaning added. Listed in §3. **Still gated** — publication requires the dataset's publish gate to open. |
| `withheld` | Deliberately not published because a definition it depends on is undecided. Named with its blocker (§4) so nobody re-derives it by hand — the exact failure SPEC-001 §1 exists to remove. |
| `prohibited` | Forbidden as an aggregate input, for the stated reason (§5). Some are pending decisions; `price` is a category error no decision will change. |

**Naming rule (catalogue metric names).** `total_*` for sums, `sku_count` for the
distinct count, `avg_*_unweighted` for the mean — the aggregation is visible
in the name. The **name** is the compatibility surface: the contract's change
policy (`additive_by_default`; redefinition of a published formula is breaking)
applies to it. The **label** carries the display caption verbatim, including
*(definition pending)*. When a blocking definition closes, the label changes;
the name does not.

**Caption ownership.** This document owns the captions of the ten supported
metrics (§3). `docs/gold-warehouse.md` §3.2 keeps its field-guide captions for
all 24 columns. The two overlap on the measure columns by decision: **a
caption change lands in both files in the same PR.**

**Shared grain, restated per metric in §3** because it is load-bearing at the
metric level: atomic grain *one row per `sku`*; supported group-bys are the
six label columns plus `source_batch_id`; **time grain: none** — the source
has no date column (SPEC-001 A4), so trend and period-over-period are
permanently unavailable, not pending.

**Shared limitation.** Every fact column is nullable in the contract and no
profiling has run (it is a gate condition). SQL `SUM`/`AVG` and DAX
`SUM`/`AVERAGE` skip nulls silently: totals would understate rather than fail.
Treat figures as provisional until profiling closes.

## 3. Supported measures

All ten are *direct aggregations* (glossary): one column through one
aggregate function over `gold.fct_sku_performance` (grain: one row per
`sku` — current state of the most recent successful load). No metric here
combines columns; combining is what §4 withholds. The `Metric name` field
is the stable catalogue identifier (naming rule, §2) — DAX labels may
change, names do not.

### 3.1 `total_revenue` — Revenue (definition pending)

| Field | Value |
| --- | --- |
| Status | `supported` (dataset publish gate closed) |
| Metric name | `total_revenue` |
| Formula (SQL) | `SUM(revenue_generated)` |
| DAX | `Revenue (definition pending) = SUM('fct_sku_performance'[revenue_generated])` |
| Caption | Revenue *(definition pending)* |
| Required source fields | `revenue_generated` — gold measure column; source header `Revenue Generated`, type `NUMERIC(14,2)` per `docs/data-dictionary.md`. Carried with `sku` (grain key) and `source_batch_id` (provenance). |
| Aggregation grain | Atomic: one row per `sku`. Group by: `product_type`, `supplier_name`, `shipping_carriers`, `transportation_modes`, `location`, `routes`, `source_batch_id`. Time grain: **none** — SPEC-001 A4. |
| Additivity | Additive — safe to sum across any grouping. |
| Limitations | Gross vs net **UNDEFINED** (SPEC-001 §12): the caption must keep *(definition pending)*; never net this against any cost and never feed margin — that combination is withheld (§4). |

### 3.2 `total_costs` — Cost (definition pending)

| Field | Value |
| --- | --- |
| Status | `supported` (dataset publish gate closed) |
| Metric name | `total_costs` |
| Formula (SQL) | `SUM(costs)` |
| DAX | `Cost (definition pending) = SUM('fct_sku_performance'[costs])` |
| Caption | Cost *(definition pending)* |
| Required source fields | `costs` — gold measure column; source header `Costs`, type `NUMERIC(14,2)` per `docs/data-dictionary.md`. Carried with `sku` and `source_batch_id`. |
| Aggregation grain | Atomic: one row per `sku`. Group by: `product_type`, `supplier_name`, `shipping_carriers`, `transportation_modes`, `location`, `routes`, `source_batch_id`. Time grain: **none** — SPEC-001 A4. |
| Additivity | Additive — safe to sum across any grouping. |
| Limitations | Includes shipping? **UNDEFINED** (SPEC-001 §12): do not add `shipping_costs`/`manufacturing_costs` into this total (double-count risk), do not derive cost per unit from it, and do not express shipping cost as a % of it — all withheld in §4. |

### 3.3 `total_shipping_costs` — Shipping cost

| Field | Value |
| --- | --- |
| Status | `supported` (dataset publish gate closed) |
| Metric name | `total_shipping_costs` |
| Formula (SQL) | `SUM(shipping_costs)` |
| DAX | `Shipping cost = SUM('fct_sku_performance'[shipping_costs])` |
| Caption | Shipping cost |
| Required source fields | `shipping_costs` — gold measure column; source header `Shipping Costs`, type `NUMERIC(12,2)` per `docs/data-dictionary.md`. Carried with `sku` and `source_batch_id`. |
| Aggregation grain | Atomic: one row per `sku`. Group by: `product_type`, `supplier_name`, `shipping_carriers`, `transportation_modes`, `location`, `routes`, `source_batch_id`. Time grain: **none** — SPEC-001 A4. |
| Additivity | Additive — safe to sum across any grouping. |
| Limitations | Do not add to `costs` and do not express as a % of total cost until the `costs` definition closes — overlap unknown, double-count risk (SPEC-001 §12, §4). |

### 3.4 `total_manufacturing_costs` — Manufacturing cost

| Field | Value |
| --- | --- |
| Status | `supported` (dataset publish gate closed) |
| Metric name | `total_manufacturing_costs` |
| Formula (SQL) | `SUM(manufacturing_costs)` |
| DAX | `Manufacturing cost = SUM('fct_sku_performance'[manufacturing_costs])` |
| Caption | Manufacturing cost |
| Required source fields | `manufacturing_costs` — gold measure column; source header `Manufacturing Costs`, type `NUMERIC(12,2)` per `docs/data-dictionary.md`. Carried with `sku` and `source_batch_id`. |
| Aggregation grain | Atomic: one row per `sku`. Group by: `product_type`, `supplier_name`, `shipping_carriers`, `transportation_modes`, `location`, `routes`, `source_batch_id`. Time grain: **none** — SPEC-001 A4. |
| Additivity | Additive — safe to sum across any grouping. |
| Limitations | Do not add to `costs` until that definition closes — overlap unknown (SPEC-001 §12, §4). |

### 3.5 `total_units_sold` — Units sold

| Field | Value |
| --- | --- |
| Status | `supported` (dataset publish gate closed) |
| Metric name | `total_units_sold` |
| Formula (SQL) | `SUM(number_of_products_sold)` |
| DAX | `Units sold = SUM('fct_sku_performance'[number_of_products_sold])` |
| Caption | Units sold |
| Required source fields | `number_of_products_sold` — gold measure column; source header `Number of Products Sold`, type `INTEGER` per `docs/data-dictionary.md`. Carried with `sku` and `source_batch_id`. |
| Aggregation grain | Atomic: one row per `sku`. Group by: `product_type`, `supplier_name`, `shipping_carriers`, `transportation_modes`, `location`, `routes`, `source_batch_id`. Time grain: **none** — SPEC-001 A4. |
| Additivity | Additive — safe to sum across any grouping. |
| Limitations | Source describes it as units sold "in a certain time period"; no date column exists (SPEC-001 A4), so the period is unknown and unverifiable per row. Summing across SKUs assumes those unknown periods are comparable — the arithmetic is safe, any *per-period* caption is not. |

### 3.6 `total_order_quantity` — Order quantity

| Field | Value |
| --- | --- |
| Status | `supported` (dataset publish gate closed) |
| Metric name | `total_order_quantity` |
| Formula (SQL) | `SUM(order_quantities)` |
| DAX | `Order quantity = SUM('fct_sku_performance'[order_quantities])` |
| Caption | Order quantity |
| Required source fields | `order_quantities` — gold measure column; source header `Order Quantities`, type `INTEGER` per `docs/data-dictionary.md`. Carried with `sku` and `source_batch_id`. |
| Aggregation grain | Atomic: one row per `sku`. Group by: `product_type`, `supplier_name`, `shipping_carriers`, `transportation_modes`, `location`, `routes`, `source_batch_id`. Time grain: **none** — SPEC-001 A4. |
| Additivity | Additive — safe to sum across any grouping. |
| Limitations | The source describes this column as an economic order quantity; whether it is a policy value or an observed quantity is unverified (no profiling). The sum is arithmetic over SKUs, not a count of orders placed. No time period exists (SPEC-001 A4). |

### 3.7 `total_stock_on_hand` — Stock on hand

| Field | Value |
| --- | --- |
| Status | `supported` (dataset publish gate closed) |
| Metric name | `total_stock_on_hand` |
| Formula (SQL) | `SUM(stock_levels)` |
| DAX | `Stock on hand = SUM('fct_sku_performance'[stock_levels])` |
| Caption | Stock on hand |
| Required source fields | `stock_levels` — gold measure column; source header `Stock Levels`, type `INTEGER` per `docs/data-dictionary.md`. Carried with `sku` and `source_batch_id`. |
| Aggregation grain | Atomic: one row per `sku`. Group by: `product_type`, `supplier_name`, `shipping_carriers`, `transportation_modes`, `location`, `routes`, `source_batch_id`. Time grain: **none** — SPEC-001 A4. |
| Additivity | Additive — safe to sum across any grouping. |
| Limitations | Snapshot at load time with **no as-of timestamp**: cannot be aged, trended, or compared across loads from this dataset; prior states are recoverable only from bronze via `source_batch_id`. |

### 3.8 `total_production_volume` — Production volume

| Field | Value |
| --- | --- |
| Status | `supported` (dataset publish gate closed) |
| Metric name | `total_production_volume` |
| Formula (SQL) | `SUM(production_volumes)` |
| DAX | `Production volume = SUM('fct_sku_performance'[production_volumes])` |
| Caption | Production volume |
| Required source fields | `production_volumes` — gold measure column; source header `Production Volumes`, type `INTEGER` per `docs/data-dictionary.md`. Carried with `sku` and `source_batch_id`. |
| Aggregation grain | Atomic: one row per `sku`. Group by: `product_type`, `supplier_name`, `shipping_carriers`, `transportation_modes`, `location`, `routes`, `source_batch_id`. Time grain: **none** — SPEC-001 A4. |
| Additivity | Additive — safe to sum across any grouping. |
| Limitations | Source says "in a certain time-period" with no period supplied (SPEC-001 A4) — the same period-comparability caveat as §3.5: arithmetic safe, *per-period* labelling not. |

### 3.9 `sku_count` — SKUs

| Field | Value |
| --- | --- |
| Status | `supported` (dataset publish gate closed) |
| Metric name | `sku_count` |
| Formula (SQL) | `COUNT(DISTINCT sku)` |
| DAX | `SKUs = DISTINCTCOUNT('fct_sku_performance'[sku])` |
| Caption | SKUs |
| Required source fields | `sku` — gold grain key; source header `SKU`, type `VARCHAR(20)` per `docs/data-dictionary.md`. Provenance: `source_batch_id`. |
| Aggregation grain | Atomic: one row per `sku`. Group by: `product_type`, `supplier_name`, `shipping_carriers`, `transportation_modes`, `location`, `routes`, `source_batch_id`. Time grain: **none** — SPEC-001 A4. |
| Additivity | **Not summable across pre-aggregated groups.** `COUNT(DISTINCT sku)` is correct at any grain when recomputed over the underlying rows; adding group subtotals double-counts SKUs that appear under more than one group (overlapping filters). Always recompute, never add subtotals. |
| Limitations | Counts current-state SKUs of the loaded batch(es); filter `source_batch_id` to pin one delivery. `sku` is unique + not_null by test, so unfiltered the count equals the row count. |

### 3.10 `avg_defect_rate_unweighted` — Unweighted mean of SKU defect rates

| Field | Value |
| --- | --- |
| Status | `supported` (dataset publish gate closed) |
| Metric name | `avg_defect_rate_unweighted` |
| Formula (SQL) | `AVG(defect_rates)` |
| DAX | `Unweighted mean of SKU defect rates = AVERAGE('fct_sku_performance'[defect_rates])` |
| Caption | **Unweighted mean of SKU defect rates** — load-bearing; states the aggregation method because no approved weighting exists |
| Required source fields | `defect_rates` — gold measure column; source header `Defect Rates`, type `NUMERIC(10,4)` per `docs/data-dictionary.md`. Carried with `sku` and `source_batch_id`. |
| Aggregation grain | Atomic: one row per `sku`. Group by: `product_type`, `supplier_name`, `shipping_carriers`, `transportation_modes`, `location`, `routes`, `source_batch_id`. Time grain: **none** — SPEC-001 A4. |
| Additivity | **Non-additive.** Averaging group averages double-counts: every SKU must carry equal weight, so the mean is recomputed from rows at each grouping level (in DAX: `AVERAGE` evaluated in the visual's filter context — never `AVERAGEX` over a pre-aggregated summary). |
| Limitations | Unweighted by design: every SKU counts equally; weighting by `production_volumes` is a business definition nobody has approved (weighted variant withheld, §4). Encoding 0–1 vs 0–100 **UNVERIFIED** (SPEC-001 Q7): cross-SKU comparison is safe — any single encoding cancels — but labelling the value a percentage is **not** until the encoding is confirmed. Keep the caption verbatim. |

## 4. Withheld measures

Deliberately not published — each listed with the definition it is blocked on,
so nobody re-derives it by hand (the exact failure SPEC-001 §1 exists to
remove):

| Withheld measure | Blocked by |
| --- | --- |
| Margin (`revenue − costs`) | Revenue gross-vs-net **and** costs-includes-shipping (SPEC-001 §12) |
| Cost per unit / unit economics of any kind | `costs` definition (SPEC-001 §12) |
| Shipping cost as % of total cost | risks double-counting if `costs` already includes shipping (SPEC-001 §12) |
| Volume-weighted defect rate | Weighting decision — none approved (§3.10) |
| "% available" / in-stock rate from `availability` | Count vs flag undecided (SPEC-001 Q7) |
| Inspection pass rate from `inspection_results` | Pass/fail vs score unconfirmed (SPEC-001 Q7) |
| Any aggregate combining `lead_times_1` + `lead_times_2` | The columns may be the same measure or different ones (SPEC-001 Q3) |
| Any trend, growth, or period-over-period measure | No date column exists (SPEC-001 A4) — permanently unavailable, not pending |

**Rule:** a derived measure appears in a report only when its blocking
definition closes **and** this document is updated in the same change. No
withheld measure exists as a declared artifact anywhere — not as YAML,
not as a view, not as commented-out SQL: declaring a metric is declaring
publishable intent, and
phantom declarations were rejected outright for contracts (ADR-003), for the
same reason here.

## 5. Prohibited columns

Forbidden as input to any aggregated measure. All five remain visible as
row-level fields under `docs/gold-warehouse.md` §3.2's captions — *prohibited*
here means "never an aggregate input", not "hidden".

| Column | Why it may never be an aggregate input | Blocking decision |
| --- | --- | --- |
| `price` | Unit price — row context only; summing across SKUs totals unrelated units and has no defined meaning | Category error, not a pending definition: no decision changes it (`gold-warehouse.md` §3.5) |
| `lead_times_1` | First colliding header (`Lead times`); may be the same measure as `lead_times_2` or a different one | SPEC-001 Q3 open — never sum or coalesce with `lead_times_2` |
| `lead_times_2` | Second colliding header (`Lead time`) | SPEC-001 Q3 open — same rule |
| `availability` | Count vs 0/1 flag undecided — any predicate or aggregate encodes the guess | SPEC-001 Q7 |
| `inspection_results` | Pass/fail vs numeric score unconfirmed | SPEC-001 Q7 |

## 6. Cross-references and catalogue limitations

| What | Where |
| --- | --- |
| Consumption guide: per-column captions, entry point, drill-through, don'ts | `docs/gold-warehouse.md` §3 |
| Dataset contract: grain, schema, quality checks, publish gate | `docs/contracts/datasets/gold-fct_sku_performance.contract.yaml` |
| Source headers and delivered types | `docs/data-dictionary.md` |
| Terms: *Measure*, *Metric*, *Direct aggregation*, *Withheld measure* | `GLOSSARY.md` |
| Gold-layer exposure: measure columns live in | `gold.fct_sku_performance` (model `dbt/models/marts/fct_sku_performance.sql`, column docs `dbt/models/marts/fct_sku_performance.yml`); aggregations are defined here and rendered as the DAX in §3 |
| Open decisions blocking §4 | SPEC-001 §11 (Q3, Q7) and §12 |

**Limitations of this catalogue itself — stated, not assumed:**

- **No dbt semantic-layer twin — structurally impossible on dbt 1.12.5.** The semantic layer requires a DAY (or smaller) time-spine model plus an `agg_time_dimension` on every measure; the source has no date column by design (SPEC-001 A4: no pseudo-date). A spine was trialled and removed — it would be a fabrication, not a model. The Gold-layer exposure is therefore the fact table itself plus this catalogue plus the DAX in §3 (ADR-004 pattern).
- **No automated catalogue↔gold consistency check (v1).** The `Metric name` and `Required source fields` columns make drift against `dbt/models/marts/fct_sku_performance.yml` checkable, but nothing checks them yet; consistency is
  review-enforced by the "same PR" rule in §2. Named follow-up once
  SPEC-001 Q10's test harness exists (pyproject → pytest → CI, in that
  order). Do not read this document as machine-verified against the model YAML.
- **Nothing here is validated on populated data.** No source CSV has been
  loaded; tests pass over empty tables (`gold-warehouse.md` §4.2, risk 2).
  Formulas are defined here, not executed anywhere in the repo — `dbt parse`
  proves the warehouse still builds cleanly, not that the numbers are right.
- **The publish gate is closed.** Metric publication is as blocked as dataset
  publication: owner UNASSIGNED (SPEC-001 Q1), profiling not done. A metric
  in this document is a definition, not a go-live.
- **Power BI reads the fact table, not a metric artifact.** The report imports
  `gold.fct_sku_performance` alone (ADR-004), so the DAX column in §3 — not
  any YAML — is what the report
  renders. Both derive from the formula here; if they ever disagree, this
  document wins, and both are defects.