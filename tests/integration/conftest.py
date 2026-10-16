"""Shared fixtures for the integration suite (ADR-006).

Every test here runs the real pipeline (cli.run_pipeline) against the
dedicated supply_chain_test PostgreSQL database -- real rows, real
commits, real constraints. The live supply_chain database is never
touched: the DB_NAME guard below refuses to run against anything else.

Infrastructure (integration-test-plan.md, ADR-006):
  - Session fixture creates supply_chain_test on demand and runs
    sql/bootstrap/001-003 once (schemas, etl.row_rejects,
    bronze.supply_chain_raw). etl.batch_manifest is created by the
    pipeline itself at runtime, so no fixture step is needed for it.
  - Per-test fixture drops and recreates the bronze and etl schemas, so
    a failure-path test can never pass on rows a previous test left
    behind. Cheap at this fixture size (100-row CSVs).
  - No silent skips: if PostgreSQL is unreachable the fixture raises and
    the suite errors loudly. A green run that proved nothing would
    violate the acceptance criterion.
"""

from __future__ import annotations

import csv
import os
from pathlib import Path

import pytest

TEST_DB_NAME = "supply_chain_test"
BOOTSTRAP_DIR = Path(__file__).resolve().parents[2] / "sql" / "bootstrap"

# Bronze column order (load.BRONZE_SOURCE_COLUMNS). The CSV builders below
# emit the contract's source spelling per column in this order; extraction
# normalises each header back to the bronze name.
BRONZE_COLUMNS: list[str] = [
    "product_type",
    "sku",
    "price",
    "availability",
    "number_of_products_sold",
    "revenue_generated",
    "customer_demographics",
    "stock_levels",
    "lead_times_1",
    "order_quantities",
    "shipping_times",
    "shipping_carriers",
    "shipping_costs",
    "supplier_name",
    "location",
    "lead_times_2",
    "production_volumes",
    "manufacturing_lead_time",
    "manufacturing_costs",
    "inspection_results",
    "defect_rates",
    "transportation_modes",
    "routes",
    "costs",
]

# Bronze column -> source header spelling (tests/conftest.SOURCE_HEADERS).
# The two colliding lead-time headers map to their distinct spellings.
SOURCE_HEADER_BY_COLUMN: dict[str, str] = {
    "product_type": "Product Type",
    "sku": "SKU",
    "price": "Price",
    "availability": "Availability",
    "number_of_products_sold": "Number of products sold",
    "revenue_generated": "Revenue generated",
    "customer_demographics": "Customer demographics",
    "stock_levels": "Stock levels",
    "lead_times_1": "Lead times",
    "order_quantities": "Order quantities",
    "shipping_times": "Shipping times",
    "shipping_carriers": "Shipping carriers",
    "shipping_costs": "Shipping costs",
    "supplier_name": "Supplier name",
    "location": "Location",
    "lead_times_2": "Lead time",
    "production_volumes": "Production volumes",
    "manufacturing_lead_time": "Manufacturing lead time",
    "manufacturing_costs": "Manufacturing costs",
    "inspection_results": "Inspection results",
    "defect_rates": "Defect rates",
    "transportation_modes": "Transportation modes",
    "routes": "Routes",
    "costs": "Costs",
}


def build_row(index: int, **overrides: str) -> dict[str, str]:
    """One valid contract-shaped row keyed by bronze column name.

    The index seeds sku (SKU-0001 ...) and varies a few numerics so rows
    are distinct but always parse under the stg_products casts
    (validate.NUMERIC_COLUMNS). Override any column to target one check
    without disturbing the rest, e.g. build_row(7, price="twelve").
    """
    row: dict[str, str] = {
        "product_type": "Electronics",
        "sku": f"SKU-{index:04d}",
        "price": f"{10 + (index % 90)}.99",
        "availability": str(100 + index),
        "number_of_products_sold": str(1000 + index),
        "revenue_generated": f"{10000 + index * 10}.50",
        "customer_demographics": "Male",
        "stock_levels": str(50 + (index % 200)),
        "lead_times_1": str(7 + (index % 20)),
        "order_quantities": str(100 + index),
        "shipping_times": str(1 + (index % 9)),
        "shipping_carriers": "FedEx",
        "shipping_costs": "12.75",
        "supplier_name": "Acme Supplies",
        "location": "Chicago",
        "lead_times_2": str(5 + (index % 15)),
        "production_volumes": str(400 + index),
        "manufacturing_lead_time": str(3 + (index % 10)),
        "manufacturing_costs": "1450.00",
        "inspection_results": "Pass",
        "defect_rates": "2.5",
        "transportation_modes": "Road",
        "routes": "Route-A",
        "costs": "3200.00",
    }
    row.update(overrides)
    return row


def write_csv(path: Path, rows: list[dict[str, str]]) -> Path:
    """Writes bronze-keyed rows to a source-spelled CSV file."""
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[SOURCE_HEADER_BY_COLUMN[c] for c in BRONZE_COLUMNS],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({SOURCE_HEADER_BY_COLUMN[c]: row[c] for c in BRONZE_COLUMNS})
    return path


@pytest.fixture(scope="session")
def _db_params() -> dict[str, str]:
    """Connection params for the admin database (to create the test DB)."""
    return {
        "dbname": "postgres",
        "user": os.getenv("DB_USER", "postgres"),
        "password": os.getenv("DB_PASSWORD", "postgres"),
        "host": os.getenv("DB_HOST", "localhost"),
        "port": os.getenv("DB_PORT", "5432"),
    }


def _connect(params: dict[str, str]):
    import psycopg2  # pyright: ignore[reportMissingModuleSource]  # local import: unit stage never needs the driver

    try:
        return psycopg2.connect(**params)
    except Exception as exc:
        pytest.fail(
            f"integration suite requires a reachable PostgreSQL at "
            f"{params['host']}:{params['port']} "
            f"(DB_USER={params['user']}, target db {TEST_DB_NAME}): {exc}. "
            f"Plain `pytest` runs the unit suite with no database; "
            f"`pytest -m integration` never silently skips.",
            pytrace=False,
        )


def _bootstrap(conn) -> None:
    conn.autocommit = True
    with conn.cursor() as cursor:
        # 001 still creates the legacy bronze.sales_raw WITHOUT
        # IF NOT EXISTS -- rerunning it after a schema drop hits
        # DuplicateTable on some code paths, so the legacy table is
        # pre-dropped first. (The sales_raw wart itself is out of scope
        # for this suite; the plan's isolation rule stands.)
        cursor.execute("DROP TABLE IF EXISTS bronze.sales_raw;")
        for script in (
            "001_create_schemas.sql",
            "002_create_audit_tables.sql",
            "003_create_bronze_supply_chain_raw.sql",
        ):
            cursor.execute((BOOTSTRAP_DIR / script).read_text(encoding="utf-8"))
    conn.autocommit = False


@pytest.fixture(scope="session")
def _test_database(_db_params) -> dict[str, str]:
    """Creates supply_chain_test once per session and bootstraps 001-003."""
    admin = _connect(_db_params)
    try:
        admin.autocommit = True
        with admin.cursor() as cursor:
            cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s;", (TEST_DB_NAME,))
            if cursor.fetchone() is None:
                cursor.execute(f'CREATE DATABASE "{TEST_DB_NAME}";')
    finally:
        admin.close()
    params = dict(_db_params, dbname=TEST_DB_NAME)
    conn = _connect(params)
    try:
        _bootstrap(conn)
    finally:
        conn.close()
    return params


@pytest.fixture
def test_db(_test_database, monkeypatch) -> dict[str, str]:
    """Resets bronze+etl schemas, points DB_* at the test DB.

    etl.batch_manifest is created by the pipeline itself at runtime, so
    the reset only needs the schemas gone -- bootstrap recreates them.
    DB_* is *forced* to the test database via monkeypatch (not merely
    asserted): whatever the operator's ambient DB_NAME is -- including
    the live supply_chain from normal development -- the pipeline under
    test can only ever connect to supply_chain_test. That is the guard;
    no ambient pre-export is required.
    """
    for var, value in (
        ("DB_NAME", TEST_DB_NAME),
        ("DB_USER", _test_database["user"]),
        ("DB_PASSWORD", _test_database["password"]),
        ("DB_HOST", _test_database["host"]),
        ("DB_PORT", _test_database["port"]),
    ):
        monkeypatch.setenv(var, value)
    conn = _connect(_test_database)
    try:
        conn.autocommit = True
        with conn.cursor() as cursor:
            cursor.execute("DROP SCHEMA IF EXISTS bronze CASCADE;")
            cursor.execute("DROP SCHEMA IF EXISTS etl CASCADE;")
        _bootstrap(conn)
    finally:
        conn.close()
    return _test_database


@pytest.fixture
def db_query(test_db):
    """Query helper against the test database; returns fetched rows."""

    def run(sql: str, params: tuple | None = None):
        conn = _connect(test_db)
        try:
            with conn.cursor() as cursor:
                cursor.execute(sql, params)
                if cursor.description is None:
                    return None
                return cursor.fetchall()
        finally:
            conn.close()

    return run


@pytest.fixture
def csv_file(tmp_path):
    """Factory: write_csv(rows, name) -> Path under tmp_path."""

    def make(rows, name: str = "batch.csv"):
        return write_csv(tmp_path / name, rows)

    return make
