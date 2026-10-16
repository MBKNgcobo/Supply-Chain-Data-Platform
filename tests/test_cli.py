"""
End-to-end (in-process) tests for cli.run_pipeline: the exit-code matrix.

SPEC-001 §10.8 / ADR-005: clean file exits 0; every fatal condition exits
non-zero; the FAILED batch of a threshold breach leaves ZERO rows in bronze
(no COPY issued) with its rejects quarantined and the manifest FAILED -- the
mechanism by which a data-quality failure stops Gold from being published
silently (dbt reads bronze, so nothing in bronze means nothing to publish).
"""

import pytest
from conftest import FIXTURES_DIR, FakeConnection

from supply_chain_pipeline import cli

HAPPY = FIXTURES_DIR / "happy_path.csv"


@pytest.fixture
def db(monkeypatch):
    fake = FakeConnection()
    monkeypatch.setattr(cli, "get_db_connection", lambda: fake)
    return fake


class TestExitCodes:
    def test_clean_file_exits_zero_and_loads(self, db):
        exit_code = cli.run_pipeline(file_path=str(HAPPY))
        assert exit_code == 0
        assert len(db.copy_calls) == 1
        assert any("SUCCESS" in str(p) for _, p in db.find_executed("UPDATE etl.batch_manifest"))
        assert db.rollbacks == 0

    def test_missing_file_exits_one(self, db):
        assert cli.run_pipeline(file_path=str(FIXTURES_DIR / "nope.csv")) == 1
        assert db.copy_calls == []

    def test_duplicate_header_file_exits_one(self, db):
        exit_code = cli.run_pipeline(file_path=str(FIXTURES_DIR / "duplicate_headers.csv"))
        assert exit_code == 1
        assert db.copy_calls == []

    def test_zero_row_file_exits_two_with_failed_manifest(self, db):
        exit_code = cli.run_pipeline(file_path=str(FIXTURES_DIR / "header_only.csv"))
        assert exit_code == 2
        assert any("FAILED" in str(p) for _, p in db.find_executed("UPDATE etl.batch_manifest"))
        assert db.copy_calls == []

    def test_threshold_breach_exits_two_quarantines_and_never_copies(self, db):
        # 4 of 10 rows rejected = 40% > 5%. THE acceptance-criterion test:
        # visible (rejects quarantined, manifest FAILED) and fatal (nothing
        # reaches bronze, so dbt cannot publish this batch).
        exit_code = cli.run_pipeline(file_path=str(FIXTURES_DIR / "threshold_40_percent.csv"))
        assert exit_code == 2
        assert db.copy_calls == [], "no row of a breached batch may reach bronze"
        assert len(db.find_executed("INSERT INTO etl.row_rejects")) == 4
        assert any("FAILED" in str(p) for _, p in db.find_executed("UPDATE etl.batch_manifest"))

    def test_already_processed_hash_exits_zero_as_noop(self, monkeypatch):
        fake = FakeConnection(fetchone_results=[("prior-batch",)])
        monkeypatch.setattr(cli, "get_db_connection", lambda: fake)
        exit_code = cli.run_pipeline(file_path=str(HAPPY))
        assert exit_code == 0
        assert fake.copy_calls == []

    def test_load_failure_exits_three_rolls_back_and_fails_manifest(self, db):
        db.fail_copy = True
        exit_code = cli.run_pipeline(file_path=str(HAPPY))
        assert exit_code == 3
        assert db.rollbacks == 1
        assert any("FAILED" in str(p) for _, p in db.find_executed("UPDATE etl.batch_manifest"))

    def test_unhandled_error_exits_ninety_nine(self, db, monkeypatch):
        def boom(self):
            raise RuntimeError("manifest setup exploded")

        monkeypatch.setattr(cli.BatchManifestManager, "setup_manifest_table", boom)
        assert cli.run_pipeline(file_path=str(HAPPY)) == 99


class TestThresholdFlag:
    def test_duplicate_sku_file_passes_with_raised_threshold(self, db):
        # duplicate_sku.csv rejects 1 of 3 rows (33%): above the 5% default,
        # below a deliberately raised threshold. Also proves the keep-first
        # duplicate-sku rule end to end.
        exit_code = cli.run_pipeline(
            file_path=str(FIXTURES_DIR / "duplicate_sku.csv"),
            max_reject_rate_percent=50.0,
        )
        assert exit_code == 0
        assert len(db.copy_calls) == 1
        assert len(db.find_executed("INSERT INTO etl.row_rejects")) == 1

    def test_same_file_at_default_threshold_breaches(self, db):
        exit_code = cli.run_pipeline(file_path=str(FIXTURES_DIR / "duplicate_sku.csv"))
        assert exit_code == 2
        assert db.copy_calls == []

    def test_unparseable_numeric_file_breaches_default(self, db):
        # 1 of 3 rows has price='twelve' -- 33% > 5%: the numeric parse check
        # fires and the batch aborts.
        exit_code = cli.run_pipeline(file_path=str(FIXTURES_DIR / "unparseable_numeric.csv"))
        assert exit_code == 2
        assert db.copy_calls == []


class TestPositiveFloatArgparse:
    @pytest.mark.parametrize("value", ["0", "-1", "abc"])
    def test_non_positive_or_non_numeric_refused(self, value):
        with pytest.raises(cli.argparse.ArgumentTypeError):
            cli._positive_float(value)

    def test_positive_accepted(self):
        assert cli._positive_float("2.5") == 2.5
