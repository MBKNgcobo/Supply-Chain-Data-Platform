"""Scenario 2 — the same file is submitted twice (integration-test-plan.md §2).

Second submission of the exact bytes is a no-op: cli.py:102 matches the
prior SUCCESS hash and returns 0 before any write. Asserts observable
truth -- zero DB changes, no second manifest row.

Recorded finding (deferred, not resolved here): the source contract
declares idempotency.duplicate_status_value DUPLICATE and
docs/deliverable/01 says "duplicate -> status DUPLICATE, no reload",
but the CLI never persists a DUPLICATE row -- it exits before
registering the batch. validate.py's DUPLICATE status is unreachable
via the CLI path. Reconciling contract/deliverable with code is a
separate change.
"""

import pytest

from supply_chain_pipeline import cli

from .conftest import build_row

pytestmark = pytest.mark.integration


def test_same_file_twice_is_noop(test_db, db_query, csv_file):
    path = csv_file([build_row(i) for i in range(1, 101)], "happy_100.csv")

    assert cli.run_pipeline(file_path=str(path)) == 0
    assert cli.run_pipeline(file_path=str(path)) == 0

    assert db_query("SELECT count(*) FROM bronze.supply_chain_raw;") == [(100,)]
    assert db_query("SELECT count(*) FROM etl.row_rejects;") == [(0,)]
    rows = db_query("SELECT status FROM etl.batch_manifest;")
    assert rows == [("SUCCESS",)], (
        "the first run's SUCCESS row must remain the only record; "
        "no DUPLICATE row is persisted by the CLI"
    )
