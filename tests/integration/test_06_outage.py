"""Scenario 6 — the database becomes unavailable during ingestion
(integration-test-plan.md §6).

Simulation: DB_PORT is overridden to a closed port (port 1), so
get_db_connection raises deterministically without touching the real
server. With no connection no manifest row can possibly be written --
batch status is *absent, not FAILED*. The connection error escapes to
cli.run_pipeline's unhandled path, which asserts exit 99 as *current*
behaviour; whether connection failure deserves its own exit code is a
recorded open question (plan exclusion 2), not an accident. Recovery
(full rerun once the DB returns) is proven in 8-i.
"""

import pytest

from supply_chain_pipeline import cli

from .conftest import TEST_DB_NAME, build_row

pytestmark = pytest.mark.integration


def test_unavailable_database_exits_99_with_no_writes(test_db, db_query, csv_file, monkeypatch):
    path = csv_file([build_row(i) for i in range(1, 101)], "happy_100.csv")
    monkeypatch.setenv("DB_PORT", "1")  # closed port: deterministic refusal

    assert cli.run_pipeline(file_path=str(path)) == 99

    # Nothing could be written. The manifest table itself was never
    # created (the connection failed before setup_manifest_table), so
    # absence -- not FAILED -- is the honest assertion; bronze and
    # quarantine stand empty from the fixture bootstrap.
    monkeypatch.setenv("DB_PORT", test_db["port"])
    assert db_query("SELECT to_regclass('etl.batch_manifest');") == [(None,)]
    assert db_query("SELECT count(*) FROM bronze.supply_chain_raw;") == [(0,)]
    assert db_query("SELECT count(*) FROM etl.row_rejects;") == [(0,)]


def test_pipeline_can_only_reach_test_database(test_db, db_query):
    # The fixture forces DB_* at supply_chain_test regardless of the
    # operator's ambient environment (which normally points at the live
    # supply_chain DB): the pipeline under test cannot reach live data.
    import os

    assert os.getenv("DB_NAME") == TEST_DB_NAME
    assert db_query("SELECT current_database();") == [(TEST_DB_NAME,)]
