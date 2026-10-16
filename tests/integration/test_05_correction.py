"""Scenario 5 — a previously loaded delivery is corrected
(integration-test-plan.md §5).

There is no Order entity in this domain (grain: one row per SKU), so
"a corrected order" is a re-delivery of the file with changed values:
different content hash, so idempotency does NOT skip it. Both source
batches stay in bronze; the corrected values are the current state.
Gold only reflects the correction after a subsequent dbt build
(manual stage 3).
"""

import pytest

from supply_chain_pipeline import cli

from .conftest import build_row

pytestmark = pytest.mark.integration


def test_corrected_redelivery_appends_new_source_batch(test_db, db_query, csv_file):
    original = csv_file([build_row(i) for i in range(1, 101)], "delivery.csv")
    corrected = csv_file(
        [build_row(i, price="77.77") for i in range(1, 101)],
        "delivery_corrected.csv",
    )

    assert cli.run_pipeline(file_path=str(original)) == 0
    assert cli.run_pipeline(file_path=str(corrected)) == 0

    assert db_query("SELECT count(*) FROM bronze.supply_chain_raw;") == [(200,)]
    assert db_query("SELECT count(DISTINCT source_batch_id) FROM bronze.supply_chain_raw;") == [
        (2,)
    ]
    # Latest delivery's values are present alongside the originals.
    assert db_query("SELECT count(*) FROM bronze.supply_chain_raw WHERE price = '77.77';") == [
        (100,)
    ]

    rows = db_query("SELECT status FROM etl.batch_manifest ORDER BY started_at;")
    assert rows == [("SUCCESS",), ("SUCCESS",)]
