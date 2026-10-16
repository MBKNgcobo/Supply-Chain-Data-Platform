"""
Unit tests for load.py: the COPY payload shape, reject persistence, the
reconciliation gate, rollback-on-failure, and persist_rejects (the abort
path).

The load phase is the last place a bad batch can be stopped before bronze --
and therefore before dbt publishes anything. These tests assert both what IS
written on success and, more importantly, what is NOT written when a gate
fires: no COPY, no commit, manifest FAILED. See ADR-005.
"""

from conftest import build_row, make_row_reject, make_validation_result

from supply_chain_pipeline.load import (
    BRONZE_SOURCE_COLUMNS,
    BRONZE_TABLE,
    PROVENANCE_COLUMNS,
    BatchLoader,
)

HASH = "b" * 64


def make_loader(db=None):
    return BatchLoader(db_connection=db)


class TestHappyPathLoad:
    def test_copy_targets_bronze_with_provenance_columns_first(self, fake_db):
        loader = make_loader(fake_db)
        result = loader.load_batch(make_validation_result(file_hash=HASH))
        assert result.success is True
        assert len(fake_db.copy_calls) == 1
        copy_sql, payload = fake_db.copy_calls[0]
        assert f"COPY {BRONZE_TABLE}" in copy_sql
        header = payload.splitlines()[0].split(",")
        assert header[: len(PROVENANCE_COLUMNS)] == PROVENANCE_COLUMNS
        assert header[len(PROVENANCE_COLUMNS) :] == BRONZE_SOURCE_COLUMNS

    def test_copy_row_carries_file_hash_as_source_batch_id(self, fake_db):
        # source_batch_id IS the file hash (003_create_bronze DDL, ADR-005
        # glossary split): the uuid batch run id never reaches bronze.
        loader = make_loader(fake_db)
        loader.load_batch(make_validation_result(file_hash=HASH))
        _, payload = fake_db.copy_calls[0]
        first_data_row = payload.splitlines()[1].split(",")
        assert first_data_row[0] == HASH  # source_batch_id
        assert first_data_row[1] == f"{HASH}-1"  # source_identity
        assert first_data_row[2] == HASH  # file_hash
        assert first_data_row[3] == "1"  # row_number

    def test_rejects_are_quarantined_and_batch_commits(self, fake_db):
        rejected = [make_row_reject(row_number=2, error_message="bad sku")]
        loader = make_loader(fake_db)
        result = loader.load_batch(make_validation_result(rejected=rejected, file_hash=HASH))
        assert result.success is True
        assert result.rows_quarantined == 1
        inserts = fake_db.find_executed("INSERT INTO etl.row_rejects")
        assert inserts, "rejects must be written to quarantine"
        sql, params = inserts[0]
        assert "jsonb" in sql  # json.dumps payload, not str() -- see ADR-001
        assert fake_db.commits == 2  # main transaction + manifest SUCCESS update
        assert fake_db.rollbacks == 0

    def test_manifest_marked_success_after_commit(self, fake_db):
        loader = make_loader(fake_db)
        loader.load_batch(make_validation_result(file_hash=HASH))
        updates = fake_db.find_executed("UPDATE etl.batch_manifest")
        assert updates
        assert any("SUCCESS" in str(params) for _, params in updates)

    def test_unknown_source_column_is_dropped_with_warning(self, fake_db, caplog):
        rows = [build_row(mystery_column="surprise")]
        loader = make_loader(fake_db)
        result = loader.load_batch(make_validation_result(valid_rows=rows))
        assert result.success is True
        _, payload = fake_db.copy_calls[0]
        header = payload.splitlines()[0]
        assert "mystery_column" not in header
        assert any("mystery_column" in r.getMessage() for r in caplog.records)

    def test_no_valid_rows_still_quarantines_rejects(self, fake_db):
        rejected = [make_row_reject(row_number=i) for i in range(1, 4)]
        loader = make_loader(fake_db)
        result = loader.load_batch(make_validation_result(valid_rows=[], rejected=rejected))
        assert result.success is True
        assert fake_db.copy_calls == []  # nothing to COPY
        assert len(fake_db.find_executed("INSERT INTO etl.row_rejects")) == 3


class TestGuardsRefuseUnsafeLoads:
    def test_failed_validation_is_never_copied(self, fake_db):
        # A FAILED validation (e.g. threshold breach) must not reach COPY --
        # dbt reads bronze without consulting the manifest, so one committed
        # row would be published (ADR-005).
        result_obj = make_validation_result(status="FAILED")
        result_obj.error_message = "Reject-rate threshold breached"
        loader = make_loader(fake_db)
        result = loader.load_batch(result_obj)
        assert result.success is False
        assert "threshold breached" in result.error_message
        assert fake_db.copy_calls == []
        assert fake_db.commits == 0

    def test_duplicate_status_skips_load_as_success_noop(self, fake_db):
        loader = make_loader(fake_db)
        result = loader.load_batch(make_validation_result(status="DUPLICATE"))
        assert result.success is True
        assert "duplicate" in result.error_message.lower()
        assert fake_db.copy_calls == []
        assert fake_db.commits == 0


class TestFailureAtomicity:
    def test_reconciliation_mismatch_rolls_back_and_fails_manifest(self, fake_db):
        # Tamper the counters so rows_read != loaded + rejected: a row has
        # "vanished". The batch must fail, not ship.
        validation = make_validation_result(file_hash=HASH)
        validation.rows_loaded = 99  # lie: only 1 record is actually present
        loader = make_loader(fake_db)
        result = loader.load_batch(validation)
        assert result.success is False
        assert "reconciliation" in result.error_message.lower()
        assert fake_db.rollbacks == 1
        # Exactly one commit survives: the manifest FAILED update, written
        # after the rollback so the failure is recorded. The data
        # transaction itself never committed.
        assert fake_db.commits == 1
        assert any(
            "FAILED" in str(p) for _, p in fake_db.find_executed("UPDATE etl.batch_manifest")
        )

    def test_copy_failure_rolls_back_and_fails_manifest(self, fake_db):
        fake_db.fail_copy = True
        loader = make_loader(fake_db)
        result = loader.load_batch(make_validation_result(file_hash=HASH))
        assert result.success is False
        assert "forced COPY failure" in result.error_message
        assert fake_db.rollbacks == 1
        assert any(
            "FAILED" in str(p) for _, p in fake_db.find_executed("UPDATE etl.batch_manifest")
        )


class TestPersistRejects:
    def test_commits_rejects_without_any_copy(self, fake_db):
        rejected = [make_row_reject(row_number=i) for i in range(1, 3)]
        validation = make_validation_result(valid_rows=[], rejected=rejected)
        validation.status = "FAILED"
        loader = make_loader(fake_db)
        count = loader.persist_rejects(validation)
        assert count == 2
        assert fake_db.copy_calls == []  # bronze untouched
        assert fake_db.commits == 1  # quarantine committed
        assert len(fake_db.find_executed("INSERT INTO etl.row_rejects")) == 2

    def test_no_rejects_is_a_noop(self, fake_db):
        loader = make_loader(fake_db)
        assert loader.persist_rejects(make_validation_result()) == 0
        assert fake_db.commits == 0

    def test_without_connection_returns_zero(self):
        assert make_loader(None).persist_rejects(make_validation_result()) == 0

    def test_quarantine_failure_raises_rather_than_silently_dropping(self, fake_db):
        fake_db.fail_execute_containing = "etl.row_rejects"
        rejected = [make_row_reject(row_number=1)]
        validation = make_validation_result(valid_rows=[], rejected=rejected)
        loader = make_loader(fake_db)
        import pytest

        with pytest.raises(RuntimeError, match="forced failure"):
            loader.persist_rejects(validation)
        assert fake_db.rollbacks == 1
