"""Scenario 4 — a business key is duplicated (integration-test-plan.md §4).

Two sub-cases, opposite mechanisms behind one operator sentence.
The business key is sku (GLOSSARY: one SKU, one row in current-state
datasets). 4-a: a repeated sku inside one file -- keep-first reject,
batch still commits below threshold. 4-b: the same skus across two
deliveries -- not a reject case at all; both batches append to bronze
and latest-load-wins resolves downstream in stg_products (dbt's
assertion, manual stage 3).
"""

import pytest

from supply_chain_pipeline import cli

from .conftest import build_row

pytestmark = pytest.mark.integration


def test_duplicate_sku_in_file_keeps_first(test_db, db_query, csv_file):
    # SKU-0042 at data rows 42 and 60: 1% reject rate, commits SUCCESS.
    rows = [build_row(i) for i in range(1, 101)]
    rows[59] = build_row(42)  # same sku, different row
    path = csv_file(rows, "dup_sku_1of100.csv")

    assert cli.run_pipeline(file_path=str(path)) == 0

    assert db_query("SELECT count(*) FROM bronze.supply_chain_raw;") == [(99,)]
    assert db_query("SELECT count(*) FROM bronze.supply_chain_raw WHERE sku = 'SKU-0042';") == [
        (1,)
    ]
    rejects = db_query("SELECT error_message FROM etl.row_rejects;")
    assert len(rejects) == 1
    assert "SKU-0042" in rejects[0][0]
    assert "row 42" in rejects[0][0]

    rows_manifest = db_query(
        "SELECT status, rows_read, rows_loaded, rows_rejected FROM etl.batch_manifest;"
    )
    assert rows_manifest == [("SUCCESS", 100, 99, 1)]


def test_same_sku_across_deliveries_appends_both(test_db, db_query, csv_file):
    # Two clean 100-row files, distinct hashes, overlapping SKUs.
    path_a = csv_file([build_row(i) for i in range(1, 101)], "delivery_a.csv")
    path_b = csv_file([build_row(i, price="99.99") for i in range(1, 101)], "delivery_b.csv")

    assert cli.run_pipeline(file_path=str(path_a)) == 0
    assert cli.run_pipeline(file_path=str(path_b)) == 0

    assert db_query("SELECT count(*) FROM bronze.supply_chain_raw;") == [(200,)]
    assert db_query("SELECT count(*) FROM etl.row_rejects;") == [(0,)]
    assert db_query("SELECT count(DISTINCT source_batch_id) FROM bronze.supply_chain_raw;") == [
        (2,)
    ]
    assert db_query("SELECT count(*) FROM bronze.supply_chain_raw WHERE sku = 'SKU-0001';") == [
        (2,)
    ]
    statuses = db_query("SELECT DISTINCT status FROM etl.batch_manifest;")
    assert statuses == [("SUCCESS",)]
