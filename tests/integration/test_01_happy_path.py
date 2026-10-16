"""Scenario 1 — a valid file loads correctly (integration-test-plan.md §1).

Proves the happy path as real rows: exit 0, 100 bronze rows carrying
the file hash as source_batch_id plus unique source_identity values,
zero quarantine rows, one SUCCESS manifest row with the reconciliation
identity 100 == 100 + 0, and error_message null.
"""

import pytest

from supply_chain_pipeline import cli

from .conftest import build_row

pytestmark = pytest.mark.integration


def test_valid_file_loads_correctly(test_db, db_query, csv_file):
    path = csv_file([build_row(i) for i in range(1, 101)], "happy_100.csv")

    assert cli.run_pipeline(file_path=str(path)) == 0

    assert db_query("SELECT count(*) FROM bronze.supply_chain_raw;") == [(100,)]
    assert db_query("SELECT count(*) FROM etl.row_rejects;") == [(0,)]
    assert db_query("SELECT count(DISTINCT source_identity) FROM bronze.supply_chain_raw;") == [
        (100,)
    ]
    assert db_query("SELECT count(DISTINCT source_batch_id) FROM bronze.supply_chain_raw;") == [
        (1,)
    ]

    rows = db_query(
        "SELECT status, rows_read, rows_loaded, rows_rejected, error_message"
        " FROM etl.batch_manifest;"
    )
    assert len(rows) == 1
    status, read, loaded, rejected, err = rows[0]
    assert (status, read, loaded, rejected, err) == ("SUCCESS", 100, 100, 0, None)
    assert read == loaded + rejected
