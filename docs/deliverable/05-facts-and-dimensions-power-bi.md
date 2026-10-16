# Deliverable 5 — Documented facts and dimensions for the selected business questions

| Field | Value |
| --- | --- |
| Status | **DESIGN-LEVEL — publish gate CLOSED** (`gold-fct_sku_performance.contract.yaml` → `current_gate_state: blocked`; owner UNASSIGNED, SPEC-001 draft, profiling not done). Code-complete, documented, **not publishable**. |
| Date | 2026-10-14 |
| Full document | `docs/gold-warehouse.md` — this file is the deliverable summary; that one is the complete design + consumption guide + architecture review |
| Relates to | SPEC-001 §2; ADR-002; ADR-003; ADR-004 |

## 1. Business questions supported (verbatim SPEC-001 §2)

1. **Which** product types / suppliers / shipping carriers / transportation modes are **underperforming** on revenue, cost, stock, and defect rate?
2. **Which SKUs drive that result?** — drill-through answering "why".

No other question is designed for.

## 2. The datasets

### Fact: `fct_sku_performance` (gold, table)

- **Grain:** one row per `sku` — current state of the latest successful load (SPEC-001 Q4/Q5). Type 1; no history beyond what bronze retains.
- **Shape:** denormalized — six dimension labels + all measures on one row, per the dataset contract (ADR-003: strict star rejected; re-opened against this very consumer and re-affirmed, ADR-004).
- **24 columns** = key (`sku`) + labels (`product_type`, `supplier_name`, `shipping_carriers`, `transportation_modes`, `location`, `routes`) + measures (price, revenue, costs ×3, quantities ×4, `defect_rates`, lead times ×4, `availability`, `inspection_results`) + `source_batch_id`.
- **Time:** none. No date column in source → no date dimension, no trend/period-over-period — by design (SPEC-001 A4).
- **PII:** `customer_demographics` physically absent (Q6 answered: gender data; ADR-001 §6) — absence *is* the exclusion.
- **Tests:** unique + not_null on `sku`; not_null on `source_batch_id`; relationships to `dim_product`; gold↔silver reconciliation singular test.

### Dimension: `dim_product` (gold, table)

- **Grain:** one row per `sku`; `sku` + the six text labels + `source_batch_id`.
- Built from the same silver rows as the fact, not joined into it; agreement enforced by the fact's `relationships` test.
- **No contract yet — by rule:** ADR-003 defers one until a consumer other than that test appears; Power BI does not import it (ADR-004), so the rule has not fired.

### Deferred: `dim_supplier`, `dim_carrier`

Not needed for the questions: group-by on the fact answers them. **Build trigger** (recorded so the deferral isn't mistaken for an oversight): a consumer needs entity attributes the fact can't carry (supplier rating, contract terms) **or** a second fact needs a conformed join (ADR-002 decision 3 names them as the destination).

## 3. Straightforward Power BI consumption (ADR-004)

- **Entry point: the fact alone** — single-table import, zero relationships, Import mode. `dim_product` is documented but not imported; its labels duplicate the fact's exactly, so the join would return nothing new and would obligate a contract for no gain.
- **Label table** (full version in `docs/gold-warehouse.md` §3.2): every column carries a recommended display name and a status — `publishable` / `label-pending` (value usable, caption blocked by an open decision) / `do-not-use-in-measures` (`lead_times_1`+`lead_times_2`, `availability`, `inspection_results` — semantics unsettled).
- **Measures — direct aggregations only:** SUM/COUNT of single columns; plus `AVG(defect_rates)` captioned exactly **"Unweighted mean of SKU defect rates"** — the caption states the method because no weighting has been approved.
- **Withheld measures**, each listed with its blocking definition: margin and all unit economics (SPEC-001 §12), volume-weighted defect rate (no approved weighting), availability-based % (Q7 flag-vs-count), inspection pass rate (Q7), any lead-time aggregate (Q3), all trend measures (no date, permanently unavailable).
- **Drill-through:** aggregate page → SKU detail *within the same table*, key = `sku`, keep-all-filters on; `source_batch_id` in a tooltip traces figures to one file delivery.
- **Freshness statement for the report footer:** "reflects the most recent successful load of a manually supplied CSV file; not a live feed."

## 4. What stands between this design and publication

1. **Owner unassigned** (SPEC-001 Q1) — blocks everything; smallest safe next step per the architecture review.
2. **No source CSV** → no profiling → gate condition unmeetable; tests pass trivially over empty tables.
3. **SPEC-001 still draft**; five metric semantics still open (revenue, costs, defect range, availability, lead-time collision).

Design decisions recorded in ADR-004 (and ADR-002/003); the architecture review, risk register, and next-step analysis live in `docs/gold-warehouse.md` §4.
