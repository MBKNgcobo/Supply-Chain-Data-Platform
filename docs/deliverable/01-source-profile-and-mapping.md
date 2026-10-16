# Deliverable 1 — Source profile and initial warehouse mapping

| Field | Value |
| --- | --- |
| Status | **PARTIAL — profile is contract-derived, not measured.** No source CSV exists in this repository (`data/` is empty), so no profiling run has happened. The *mapping* is complete and implemented; the *profile* is the documented type answers from SPEC-001 Q7 plus explicit unknowns. |
| Date | 2026-10-14 |
| Sources of truth | `docs/source-data-contract.md`, `docs/contracts/sources/supply_chain_data-csv.contract.yaml` (DRAFT, `profiling_done: false`), `docs/data-dictionary.md`, SPEC-001 §3/§11 |

## 1. Source identity

| Attribute | Value |
| --- | --- |
| System of record | Kaggle `harshsingh2209/supply-chain-analysis` |
| File | `supply_chain_data.csv` |
| Delivery | Manual drop into `data/incoming/` — no API, no schedule (cadence: `manual`) |
| Format | CSV, encoding expected `utf-8-sig`, tolerated: `utf-8`, `latin-1`, `cp1252` |
| Idempotency | SHA-256 file hash vs `etl.batch_manifest`; duplicate → status `DUPLICATE`, no reload |
| Business origin | Fashion/beauty startup; makeup product supply chain |
| Licence | Kaggle public; internal redistribution verified (SPEC-001 Q9) |

## 2. Profile as it actually stands

**Measured:** nothing. `profiling_done: false` in the source contract, and the contract itself says *"Do not treat this contract as a gate until profiling_done is true."* Every `type` in it is `string` (unprofiled).

**Known from answered questions (SPEC-001 Q7, published in `docs/data-dictionary.md`)** — 24 columns with declared types: 16 typed columns (e.g. `price NUMERIC(12,2)`, `revenue_generated NUMERIC(14,2)`, `availability INTEGER`, `defect_rates NUMERIC(10,4)`) and the rest VARCHAR.

**Structural hazards (settled facts, not guesses):**

| Hazard | Detail | Handling |
| --- | --- | --- |
| Duplicate headers | `Lead times` and `Lead time` both present | Positional read in `extract.build_column_map` → bronze `lead_times_1` / `lead_times_2`; kept separate, SPEC-001 Q3 open |
| No temporal column | `has_temporal_column: false` | No date dimension, no trend; freshness = time since last `SUCCESS` in `etl.batch_manifest` (Q8: daily 06:00 UTC) |
| PII column | `customer_demographics` holds gender data (Q6) | Stays in bronze/silver; **excluded from every gold model** (ADR-001 §6) — absence is the exclusion |
| Key | `sku` claimed unique (Q4 answered) | unique + not_null asserted in `stg_products` and both marts |
| `availability` | INTEGER settled, **semantics open**: count vs 0/1 flag | No boolean predicate or aggregate anywhere until decided |
| `inspection_results` | VARCHAR(20), pass/fail vs score **unconfirmed** | Stays text |
| `defect_rates` | Range **unverified**: 0–1 or 0–100 | Superset test 0–100 at severity warn only |
| `costs` / `revenue_generated` | Definitions **undefined** (shipping inclusion; gross vs net, SPEC-001 §12) | Passed through unmodified; derived measures withheld |

**Unverified even at type level** (profiling would settle): null rates, value ranges, whether dimension columns are single-valued, row count, encoding of the actual file.

## 3. Initial mapping: source → bronze → silver → gold

Every source column maps end to end. **B** = TEXT in bronze (cast boundary is silver), **S** = typed in `stg_products`, **G** = published column.

| Source header | Bronze column | Silver type (`stg_products`) | Gold destination |
| --- | --- | --- | --- |
| Product Type | `product_type` | text | `fct_sku_performance`, `dim_product` |
| SKU | `sku` | text (PK) | `fct_sku_performance`, `dim_product` |
| Price | `price` | numeric(12,2) | `fct_sku_performance` |
| Availability | `availability` | integer ⚠ semantics open | `fct_sku_performance` |
| Number of products sold | `number_of_products_sold` | integer | `fct_sku_performance` |
| Revenue generated | `revenue_generated` | numeric(14,2) ⚠ definition open | `fct_sku_performance` |
| Customer demographics | `customer_demographics` | text (provisional PII) | **none — excluded from gold** |
| Stock levels | `stock_levels` | integer | `fct_sku_performance` |
| Lead times | `lead_times_1` | integer ⚠ Q3 open | `fct_sku_performance` |
| Order quantities | `order_quantities` | integer | `fct_sku_performance` |
| Shipping times | `shipping_times` | integer ⚠ unit unverified | `fct_sku_performance` |
| Shipping carriers | `shipping_carriers` | text | `fct_sku_performance`, `dim_product` |
| Shipping costs | `shipping_costs` | numeric(12,2) | `fct_sku_performance` |
| Supplier name | `supplier_name` | text | `fct_sku_performance`, `dim_product` |
| Location | `location` | text ⚠ meaning unverified | `fct_sku_performance`, `dim_product` |
| Lead time | `lead_times_2` | integer ⚠ Q3 open | `fct_sku_performance` |
| Production volumes | `production_volumes` | integer | `fct_sku_performance` |
| Manufacturing lead time | `manufacturing_lead_time` | integer | `fct_sku_performance` |
| Manufacturing costs | `manufacturing_costs` | numeric(12,2) | `fct_sku_performance` |
| Inspection results | `inspection_results` | text ⚠ semantics open | `fct_sku_performance` |
| Defect rates | `defect_rates` | numeric(10,4) ⚠ range unverified | `fct_sku_performance` |
| Transportation modes | `transportation_modes` | text | `fct_sku_performance`, `dim_product` |
| Routes | `routes` | text | `fct_sku_performance`, `dim_product` |
| Costs | `costs` | numeric(14,2) ⚠ definition open | `fct_sku_performance` |

**Loader provenance columns** (added by the loader, not in the source): bronze gets `bronze_row_id`, `source_batch_id`, `source_identity`, `file_hash`, `row_number`, `loaded_at` → silver carries all six (renamed `bronze_row_id` → `source_row_id`) → gold carries `source_batch_id` only (contract schema).

## 4. Drift and open items found while mapping

1. **Source contract vs data dictionary:** `supply_chain_data-csv.contract.yaml` declares `target_type: numeric` for `stock_levels`, `order_quantities`, `shipping_times`, `production_volumes`, `manufacturing_lead_time`; `docs/data-dictionary.md` types them INTEGER and `stg_products` casts `::integer`. The data dictionary is the stated authority for the cast boundary; the source contract should be regenerated from a profiling run (its own header says so).
2. **Profiling cannot start** until a real `supply_chain_data.csv` is in `data/` — this is a gate condition for gold publication.
3. **Row-check gap:** the contract requires `sku_unique_in_file`, `numeric_columns_parse`, `defect_rates_in_range` as row checks; only `sku_not_null` is implemented in the loader. `sku` uniqueness is asserted downstream in dbt instead — acceptable for the warehouse, but it means the *file-level* check is unmet as written.
4. **`duplicate_header_names_absent` fatal check** listed in the contract is not implemented (SPEC-001 §10 item 5). The loader instead detects and resolves collisions positionally (`extract.build_column_map`), which is stronger than the named check for the known `Lead times`/`Lead time` collision — but the contract and the code describe different mechanisms, and one of them should give.
5. **`max_reject_rate_percent: 5.0`** is marked "proposed, not agreed" and is enforced nowhere; the pipeline currently quarantines rejects without a threshold.

---

*Mapping implemented in: `src/supply_chain_pipeline/extract.py` (header normalisation + alias map), `sql/bootstrap/003_create_bronze_supply_chain_raw.sql` (bronze), `dbt/models/staging/stg_products.sql` (cast boundary), `dbt/models/marts/*` (gold). Decisions: ADR-001, ADR-002, ADR-003.*

