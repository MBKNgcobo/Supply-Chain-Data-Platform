-- 002_create_audit_tables.sql
--
-- Audit and quarantine tables for the ingestion pipeline.
--
-- WHY THIS FILE EXISTS: load.py inserts rejected rows into etl.row_rejects, but
-- this script was empty, so the table did not exist. Any file containing a row
-- with a missing sku would fail the whole transaction at the quarantine step --
-- turning a quarantine into a pipeline outage. This creates the table the code
-- already expects.
--
-- etl.batch_runs and etl.batch_run_steps already exist in
-- 001_create_schemas.sql. The batch manifest table is created by the pipeline
-- itself (manifest.setup_manifest_table), so it is intentionally not duplicated
-- here.

CREATE SCHEMA IF NOT EXISTS etl;

-- One row per rejected source row. This is the only place a rejected value is
-- preserved, so raw_data must hold the row exactly as delivered -- no
-- normalisation, no trimming -- otherwise the cause of the rejection cannot be
-- investigated after the fact.
CREATE TABLE IF NOT EXISTS etl.row_rejects (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    file_hash      text        NOT NULL,   -- SHA-256 of the source file
    row_number     integer     NOT NULL,   -- 1-based line number within that file
    raw_data       jsonb       NOT NULL,   -- the row exactly as read
    error_message  text        NOT NULL,
    rejected_at    timestamptz NOT NULL DEFAULT now()
);

-- (file_hash, row_number) is how an operator looks up "what was wrong with line
-- N of file X", which is the only practical query against this table.
CREATE INDEX IF NOT EXISTS idx_row_rejects_file_row
    ON etl.row_rejects (file_hash, row_number);

CREATE INDEX IF NOT EXISTS idx_row_rejects_rejected_at
    ON etl.row_rejects (rejected_at DESC);

-- A row can only be rejected once per file, so this is a genuine duplicate guard.
-- Without it, a retried batch would append a second copy of every reject and
-- double-count the quarantine volume.
CREATE UNIQUE INDEX IF NOT EXISTS idx_row_rejects_identity
    ON etl.row_rejects (file_hash, row_number);

COMMENT ON TABLE etl.row_rejects IS
    'Quarantined source rows. Written by load.py for every row that failed validation. '
    'Not loaded into bronze. Reconcile against the manifest: rows_read == rows_loaded + count(etl.row_rejects) per file.';

