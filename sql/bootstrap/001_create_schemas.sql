CREATE SCHEMA IF NOT EXISTS bronze;
CREATE TABLE IF NOT EXISTS bronze.sales_raw (
    batch_id TEXT,
    ingest_time TIMESTAMPTZ DEFAULT now(),
    -- all source columns exactly as delivered
    order_id TEXT,
    customer TEXT,
    amount TEXT,   -- keep as TEXT to avoid type loss
    order_date TEXT
);

CREATE SCHEMA IF NOT EXISTS silver;
CREATE SCHEMA IF NOT EXISTS gold;

CREATE SCHEMA IF NOT EXISTS etl;
CREATE TABLE IF NOT EXISTS etl.batch_runs (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    pipeline_name   text NOT NULL,
    started_at      timestamptz NOT NULL DEFAULT now(),
    finished_at     timestamptz,
    status          text NOT NULL CHECK (status IN ('running', 'success', 'failed')),
    run_parameters  jsonb,
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS etl.batch_run_steps (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    batch_run_id    bigint NOT NULL REFERENCES etl.batch_runs(id) ON DELETE CASCADE,
    step_name       text NOT NULL,
    started_at      timestamptz NOT NULL DEFAULT now(),
    finished_at     timestamptz,
    status          text NOT NULL CHECK (status IN ('running', 'success', 'failed')),
    message         text,
    created_at      timestamptz NOT NULL DEFAULT now()
);

