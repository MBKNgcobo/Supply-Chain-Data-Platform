"""Scenario 8 — the pipeline is rerun after a failure
(integration-test-plan.md §8).

Four enumerated sub-cases:
  8-i   outage then rerun succeeds with no debris;
  8-ii  unfixed threshold-breach file fails identically on rerun --
        determinism is the anti-pattern catcher (a failure that
        "accidentally" succeeds on retry hides the original problem);
  8-iii corrected file after a breach succeeds, FAILED history retained;
  8-iv  dbt rerun after a transformation failure -- manual stage 3,
        documented in the plan, not executed here (ADR-006 decision 4).

Note on 8-ii mechanics: re-quarantining the same (file_hash,
row_number) is refused by row_rejects' unique index, which cli.py logs
without changing exit code 2; the manifest still gains a second FAILED
row (PENDING -> FAILED is recorded per batch run).
"""

import pytest

from supply_chain_pipeline import cli

from .conftest import build_row

pytestmark = pytest.mark.integration


def _bad_of_10():
    rows = [build_row(i) for i in range(1, 11)]
    rows[5] = build_row(1000, price="twelve")
    return rows


def _failed_manifest_rows(db_query):
    return db_query(
        "SELECT status, rows_read, rows_loaded, rows_rejected"
        " FROM etl.batch_manifest ORDER BY started_at;"
    )


def test_rerun_after_outage_recovers_cleanly(test_db, db_query, csv_file, monkeypatch):
    path = csv_file([build_row(i) for i in range(1, 101)], "happy_100.csv")
    monkeypatch.setenv("DB_PORT", "1")
    assert cli.run_pipeline(file_path=str(path)) == 99
    monkeypatch.setenv("DB_PORT", test_db["port"])

    assert cli.run_pipeline(file_path=str(path)) == 0
    assert db_query("SELECT count(*) FROM bronze.supply_chain_raw;") == [(100,)]
    assert db_query(
        "SELECT status, rows_read, rows_loaded, rows_rejected FROM etl.batch_manifest;"
    ) == [("SUCCESS", 100, 100, 0)]


def test_rerun_of_unfixed_file_fails_identically(test_db, db_query, csv_file):
    path = csv_file(_bad_of_10(), "one_bad_of_10.csv")

    assert cli.run_pipeline(file_path=str(path)) == 2
    assert cli.run_pipeline(file_path=str(path)) == 2

    assert db_query("SELECT count(*) FROM bronze.supply_chain_raw;") == [(0,)]
    # Quarantine keeps exactly the first attempt's row: the unique
    # (file_hash, row_number) index refuses the duplicate, which the CLI
    # logs without changing the exit code.
    assert db_query("SELECT count(*) FROM etl.row_rejects;") == [(1,)]
    assert _failed_manifest_rows(db_query) == [
        ("FAILED", 10, 0, 1),
        ("FAILED", 10, 0, 1),
    ]


def test_rerun_of_corrected_file_succeeds(test_db, db_query, csv_file):
    bad = csv_file(_bad_of_10(), "one_bad_of_10.csv")
    fixed = csv_file([build_row(i) for i in range(1, 11)], "fixed_10.csv")

    assert cli.run_pipeline(file_path=str(bad)) == 2
    assert cli.run_pipeline(file_path=str(fixed)) == 0

    assert db_query("SELECT count(*) FROM bronze.supply_chain_raw;") == [(10,)]
    assert _failed_manifest_rows(db_query) == [
        ("FAILED", 10, 0, 1),
        ("SUCCESS", 10, 10, 0),
    ]
