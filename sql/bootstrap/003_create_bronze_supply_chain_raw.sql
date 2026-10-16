-- 003_create_bronze_supply_chain_raw.sql
--
-- Bronze landing table for the single source file: supply_chain_data.csv.
--
-- WHY THIS FILE EXISTS
-- 001_create_schemas.sql creates bronze.sales_raw with the columns
-- (order_id, customer, amount, order_date). That schema does not describe this
-- source file, so no staging model can read it. This table is the contract-backed
-- replacement, taken from docs/contracts/sources/supply_chain_data-csv.contract.yaml
-- and docs/data-dictionary.md.
--
-- RULES FOR THIS TABLE
--   1. Every source column is TEXT. Casting is deferred to dbt staging so that no
--      parse decision is made before the data is observed. Do not type these columns.
--   2. Header names are normalised to snake_case by the loader, NOT by the database.
--      See the OPEN DELTA note at the bottom of this file.
--   3. Rows are appended, never updated. De-duplication to "one row per SKU" is the
--      job of stg_products, not of this table.
--   4. lead_times_1 / lead_times_2 are the two colliding source headers
--      ('Lead times' and 'Lead time'). They are kept separate and unresolved on
--      purpose -- see SPEC-001 section 11 Q3.
--
-- STATUS: DRAFT. This table does not match what load.py currently writes
-- (load.py targets a table called production_orders). Repointing the loader is a
-- prerequisite, not a follow-up. See docs/decisions/ADR-001-staging-layering.md.

CREATE SCHEMA IF NOT EXISTS bronze;

CREATE TABLE IF NOT EXISTS bronze.supply_chain_raw (
    -- provenance: who produced this row and when
    bronze_row_id    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_batch_id  text        NOT NULL,   -- sha256 of the source file
    source_identity  text        NOT NULL,   -- batch hash + '-' + row number
    file_hash        text        NOT NULL,
    row_number       integer     NOT NULL,
    loaded_at        timestamptz NOT NULL DEFAULT now(),

    -- source columns, all TEXT by rule 1
    product_type              text,
    sku                       text,
    price                     text,
    availability              text,
    number_of_products_sold   text,
    revenue_generated         text,
    customer_demographics     text,
    stock_levels              text,
    lead_times_1              text,
    order_quantities          text,
    shipping_times            text,
    shipping_carriers         text,
    shipping_costs            text,
    supplier_name             text,
    location                  text,
    lead_times_2              text,
    production_volumes        text,
    manufacturing_lead_time   text,
    manufacturing_costs       text,
    inspection_results        text,
    defect_rates              text,
    transportation_modes      text,
    routes                    text,
    costs                     text
);

-- source_identity is the loader's own row identity, so it is the only safe
-- uniqueness anchor. This index is also what makes a replayed batch detectable
-- instead of silently doubling the row count.
CREATE UNIQUE INDEX IF NOT EXISTS supply_chain_raw_source_identity_key
    ON bronze.supply_chain_raw (source_identity);

CREATE INDEX IF NOT EXISTS supply_chain_raw_sku_idx
    ON bronze.supply_chain_raw (sku);

CREATE INDEX IF NOT EXISTS supply_chain_raw_batch_idx
    ON bronze.supply_chain_raw (source_batch_id, loaded_at DESC);

-- HEADER NORMALISATION: RESOLVED. extract.py normalises source headers to the
-- snake_case names above before the COPY is built (see extract.normalise_header
-- and extract.build_column_map), and maps the two colliding lead-time headers to
-- lead_times_1 / lead_times_2. The loader COPYs only columns that exist in this
-- table, so a new upstream column is reported and skipped rather than failing the
-- load. See docs/decisions/ADR-001-staging-layering.md.
