"""
Unit tests for manifest.py: idempotency detection, batch-run status
transitions, and offline (db=None) behaviour.

The manifest is the audit trail the acceptance criterion depends on: a
data-quality failure must leave a FAILED row an operator can find, and a
SUCCESS row must be the only thing that can ever mark a file as processed
(ADR-005, glossary: batch run vs source batch).
"""

from datetime import datetime

import pytest
from conftest import FakeConnection

from supply_chain_pipeline.manifest import BatchManifestManager

FULL_ROW = (
    "batch-1",
    "test_pipeline",
    "file.csv",
    "h" * 64,
    datetime(2026, 10, 15, 8, 0, 0),
    datetime(2026, 10, 15, 8, 0, 5),
    "SUCCESS",
    10,
    9,
    1,
    None,
)


class TestSetup:
    def test_creates_schema_and_table_then_commits(self, fake_db):
        BatchManifestManager(fake_db).setup_manifest_table()
        creates = fake_db.find_executed("CREATE TABLE IF NOT EXISTS etl.batch_manifest")
        assert creates
        assert fake_db.commits == 1

    def test_offline_mode_is_a_noop(self):
        BatchManifestManager(None).setup_manifest_table()  # must not raise

    def test_failure_rolls_back_and_raises(self, fake_db):
        fake_db.fail_execute_containing = "batch_manifest"
        with pytest.raises(RuntimeError, match="forced failure"):
            BatchManifestManager(fake_db).setup_manifest_table()
        assert fake_db.rollbacks == 1


class TestIdempotencyCheck:
    def test_success_row_for_hash_means_already_processed(self):
        db = FakeConnection(fetchone_results=[("batch-1",)])
        assert BatchManifestManager(db).is_already_processed("h" * 64) is True

    def test_no_row_means_not_processed(self):
        assert BatchManifestManager(FakeConnection()).is_already_processed("h" * 64) is False

    def test_offline_mode_means_not_processed(self):
        assert BatchManifestManager(None).is_already_processed("h" * 64) is False

    def test_query_error_raises_rather_than_guessing(self, fake_db):
        fake_db.fail_execute_containing = "batch_manifest"
        with pytest.raises(RuntimeError, match="forced failure"):
            BatchManifestManager(fake_db).is_already_processed("h" * 64)


class TestBatchRunLifecycle:
    def test_start_batch_inserts_pending_row(self, fake_db):
        manager = BatchManifestManager(fake_db)
        started = manager.start_batch("batch-1", "pipe", "f.csv", "h" * 64)
        inserts = fake_db.find_executed("INSERT INTO etl.batch_manifest")
        assert inserts
        sql, params = inserts[0]
        assert params[0] == "batch-1"
        assert "ON CONFLICT (batch_id)" in sql  # restart-safe
        assert isinstance(started, datetime)
        assert fake_db.commits == 1

    def test_start_batch_offline_returns_timestamp(self):
        assert isinstance(BatchManifestManager(None).start_batch("b", "p", "f", "h"), datetime)

    def test_complete_batch_updates_status_and_counters(self, fake_db):
        BatchManifestManager(fake_db).complete_batch(
            "batch-1", "FAILED", 10, 0, 10, "threshold breached"
        )
        updates = fake_db.find_executed("UPDATE etl.batch_manifest")
        assert updates
        _, params = updates[0]
        assert params[1] == "FAILED"
        assert params[2:6] == (10, 0, 10, "threshold breached")
        assert params[6] == "batch-1"
        assert fake_db.commits == 1


class TestLastSuccessfulRun:
    def test_row_maps_to_record(self):
        db = FakeConnection(fetchone_results=[FULL_ROW])
        record = BatchManifestManager(db).get_last_successful_run("test_pipeline")
        assert record.batch_id == "batch-1"
        assert record.status == "SUCCESS"
        assert record.rows_loaded == 9
        assert record.rows_rejected == 1

    def test_no_row_returns_none(self):
        assert BatchManifestManager(FakeConnection()).get_last_successful_run("p") is None

    def test_offline_returns_none(self):
        assert BatchManifestManager(None).get_last_successful_run("p") is None

    def test_error_returns_none_and_is_logged_not_raised(self, fake_db):
        fake_db.fail_execute_containing = "batch_manifest"
        assert BatchManifestManager(fake_db).get_last_successful_run("p") is None
