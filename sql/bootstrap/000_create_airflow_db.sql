-- 000_create_airflow_db.sql
--
-- Creates a second database for Airflow metadata so the scheduler's tables
-- never collide with the supply_chain warehouse. The postgres image only
-- creates $POSTGRES_DB on its own; this runs via /docker-entrypoint-initdb.d
-- on FIRST init only (empty pgdata), so a fresh `docker compose up` gets both
-- databases and restarts are no-ops.
--
-- CREATE DATABASE cannot run inside a transaction block, so the \gexec trick
-- below generates the statement conditionally instead of using IF NOT EXISTS
-- (which Postgres does not support for databases).
SELECT 'CREATE DATABASE "airflow_db"'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'airflow_db')\gexec
