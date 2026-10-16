"""Scenario 3 — one malformed record (integration-test-plan.md §3).

Two sub-cases, opposite outcomes of the same sentence: below threshold
the row is quarantined and the batch commits; above threshold the stage
fails loudly and bronze receives nothing from this file. The
malformation is one unparseable NUMERIC_COLUMNS value
(numeric_columns_parse -- the reject names column and value).
"""

import pytest

from supply_chain_pipeline import cli

from .conftest import build_row

pytestmark = pytest.mark.integration


def _one_bad_row(n: int):
    rows = [build_row(i) for i in range(1, n + 1)]
    rows[n // 2] = build_row(n + 1000, price="twelve")
    return rows


def test_below_threshold_quarantines_and_commits(test_db, db_query, csv_file):
    # 1 of 100 = 1% <= 5%: exit 0, reconciliation 100 == 99 + 1.
    path = csv_file(_one_bad_row(100), "one_bad_of_100.csv")

    assert cli.run_pipeline(file_path=str(path)) == 0

    assert db_query("SELECT count(*) FROM bronze.supply_chain_raw;") == [(99,)]
    rejects = db_query("SELECT row_number, error_message FROM etl.row_rejects;")
    assert len(rejects) == 1
    assert rejects[0][0] == 51  # true file position, 1-based incl. header offset
    assert "price" in rejects[0][1] and "twelve" in rejects[0][1]

    rows = db_query("SELECT status, rows_read, rows_loaded, rows_rejected FROM etl.batch_manifest;")
    assert rows == [("SUCCESS", 100, 99, 1)]


def test_breach_aborts_with_zero_bronze_rows(test_db, db_query, csv_file):
    # 1 of 10 = 10% > 5%: exit 2, bronze provably empty on a real DB.
    path = csv_file(_one_bad_row(10), "one_bad_of_10.csv")

    assert cli.run_pipeline(file_path=str(path)) == 2

    assert db_query("SELECT count(*) FROM bronze.supply_chain_raw;") == [(0,)]
    assert db_query("SELECT count(*) FROM etl.row_rejects;") == [(1,)]

    rows = db_query(
        "SELECT status, rows_read, rows_loaded, rows_rejected, error_message"
        " FROM etl.batch_manifest;"
    )
    assert len(rows) == 1
    status, read, loaded, rejected, err = rows[0]
    assert (status, read, loaded, rejected) == ("FAILED", 10, 0, 1)
    assert "10.00%" in err and "5.00%" in err
