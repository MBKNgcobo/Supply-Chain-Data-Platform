"""
Unit tests for validate.py: missing required fields, numeric parse checks,
sku uniqueness, the reject-rate threshold, dormant defect-rate bounds,
batch identity construction, and the reconciliation identity.

The acceptance criterion lives here: a data-quality failure must be VISIBLE
(rejected rows named with reason + position) and must FAIL the stage
(status FAILED, no valid records to load) rather than letting a bad batch
reach bronze and be published by dbt. See ADR-005 and
docs/testing/unit-test-plan.md.
"""

import pytest
from conftest import build_row, make_extraction

from supply_chain_pipeline.validate import BatchValidator

HASH = "a" * 64


def make_validator(db=None, **kwargs):
    return BatchValidator("test_pipeline", db_connection=db, **kwargs)


def row_check_validator(db=None, **kwargs):
    """A validator with the batch reject-rate gate raised out of the way.

    Row-level tests use tiny batches (one bad row among two or three), which
    the DEFAULT 5% threshold would abort before the row check's own behaviour
    is observable. The gate itself is tested separately in
    TestRejectRateThreshold; this helper keeps the two concerns apart.
    """
    kwargs.setdefault("max_reject_rate_percent", 100.0)
    return BatchValidator("test_pipeline", db_connection=db, **kwargs)


def good_rows(n, prefix="SKU"):
    return [build_row(sku=f"{prefix}-{i:03d}") for i in range(1, n + 1)]


class TestBatchIdentityConstruction:
    def test_source_identity_is_hash_rownumber(self):
        result = make_validator().validate_batch(
            make_extraction([build_row("S-1"), build_row("S-2")], file_hash=HASH),
            batch_id="batch-1",
        )
        assert result.valid_records[0].source_identity == f"{HASH}-1"
        assert result.valid_records[1].source_identity == f"{HASH}-2"

    def test_identical_file_revalidation_yields_identical_identities(self):
        # This is what makes a replay raise bronze's unique violation instead
        # of doubling rows: identity derives from content, not from the run.
        extraction = make_extraction([build_row("S-1")], file_hash=HASH)
        first = make_validator().validate_batch(extraction, batch_id="batch-1")
        second = make_validator().validate_batch(extraction, batch_id="batch-2")
        assert first.valid_records[0].source_identity == second.valid_records[0].source_identity

    def test_row_numbers_are_one_based_and_skip_rejected_positions(self):
        rows = [build_row("S-1"), build_row(sku=""), build_row("S-3")]
        result = row_check_validator().validate_batch(
            make_extraction(rows, file_hash=HASH), batch_id="batch-1"
        )
        assert result.valid_records[0].row_number == 1
        # Row 2 was rejected; the third file row keeps its true position 3.
        assert result.valid_records[1].row_number == 3
        assert result.valid_records[1].source_identity == f"{HASH}-3"


class TestMissingRequiredFields:
    def test_blank_sku_is_rejected_with_named_reason(self):
        result = row_check_validator().validate_batch(
            make_extraction([build_row("S-1"), build_row(sku="")]), batch_id="b"
        )
        assert result.status == "VALIDATED"
        assert result.rows_rejected == 1
        reject = result.rejected_records[0]
        assert reject.row_number == 2
        assert "sku" in reject.error_message
        assert reject.raw_data["sku"] == ""  # raw payload preserved

    @pytest.mark.parametrize("bad_sku", ["", "   ", None])
    def test_blankish_skus_rejected(self, bad_sku):
        rows = [build_row("S-1"), build_row(sku=bad_sku)]
        result = row_check_validator().validate_batch(make_extraction(rows), batch_id="b")
        assert result.rows_rejected == 1
        assert "sku" in result.rejected_records[0].error_message


class TestSkuUniquenessInFile:
    def test_first_occurrence_wins_later_rows_rejected(self):
        rows = [build_row("DUP", price="10.00"), build_row("DUP", price="99.99")]
        result = row_check_validator().validate_batch(
            make_extraction(rows, file_hash=HASH), batch_id="b"
        )
        assert result.rows_loaded == 1
        assert result.rows_rejected == 1
        assert result.valid_records[0].data["price"] == "10.00"  # first kept
        message = result.rejected_records[0].error_message
        assert "Duplicate sku 'DUP'" in message
        assert "first occurrence at row 1" in message

    def test_duplicate_is_compared_on_trimmed_sku(self):
        rows = [build_row("SKU-A1"), build_row("  SKU-A1  ")]
        result = row_check_validator().validate_batch(make_extraction(rows), batch_id="b")
        assert result.rows_rejected == 1

    def test_distinct_skus_all_pass(self):
        result = make_validator().validate_batch(make_extraction(good_rows(5)), batch_id="b")
        assert result.rows_loaded == 5
        assert result.rows_rejected == 0


class TestNumericParsing:
    def test_valid_values_pass(self):
        rows = [
            build_row(
                price="1234.56",
                availability="42",
                revenue_generated="0.10",
                number_of_products_sold="-7",
                costs="1000000",
            )
        ]
        result = make_validator().validate_batch(make_extraction(rows), batch_id="b")
        assert result.rows_rejected == 0

    def test_unparseable_numeric_is_rejected_naming_column_and_value(self):
        rows = [build_row("S-1"), build_row("S-2", price="twelve")]
        result = row_check_validator().validate_batch(
            make_extraction(rows, file_hash=HASH), batch_id="b"
        )
        assert result.rows_rejected == 1
        reject = result.rejected_records[0]
        assert reject.row_number == 2
        assert "'price'" in reject.error_message
        assert "'twelve'" in reject.error_message
        assert result.valid_records[0].data["sku"] == "S-1"

    def test_decimal_in_integer_column_is_rejected(self):
        # availability casts ::integer in stg_products; '12.5' would blow up
        # in dbt unless caught here.
        rows = [build_row("S-1", availability="12.5")]
        result = row_check_validator().validate_batch(make_extraction(rows), batch_id="b")
        assert result.rows_rejected == 1
        assert "availability" in result.rejected_records[0].error_message

    def test_comma_grouped_number_is_rejected(self):
        # '1,500' is not parseable by Postgres numeric either.
        rows = [build_row("S-1", revenue_generated="1,500")]
        result = row_check_validator().validate_batch(make_extraction(rows), batch_id="b")
        assert result.rows_rejected == 1

    @pytest.mark.parametrize("poison", ["NaN", "Infinity", "-Infinity"])
    def test_non_finite_values_are_rejected(self, poison):
        # Decimal('NaN') parses but would poison every downstream sum.
        rows = [build_row("S-1", price=poison)]
        result = row_check_validator().validate_batch(make_extraction(rows), batch_id="b")
        assert result.rows_rejected == 1

    def test_blank_is_null_not_reject(self):
        # Missing values must stay observable downstream (stg_products
        # header: no null-dropping), so blank is not a quality failure.
        rows = [build_row("S-1", price="", stock_levels=None)]
        result = make_validator().validate_batch(make_extraction(rows), batch_id="b")
        assert result.rows_rejected == 0
        assert result.rows_loaded == 1

    def test_negative_values_are_not_rejected_no_range_is_guessed(self):
        # No source contract range exists for price; a negative is legal data
        # until a range decision says otherwise (glossary: withheld, never
        # guessed).
        rows = [build_row("S-1", price="-3.50")]
        result = make_validator().validate_batch(make_extraction(rows), batch_id="b")
        assert result.rows_rejected == 0


class TestDefectRateBounds:
    def test_dormant_by_default_out_of_range_passes(self):
        # SPEC-001 Q7 (0-100 vs 0-1) is undecided, so no bounds are configured
        # and no range is guessed. 150 must pass untouched.
        rows = [build_row("S-1", defect_rates="150")]
        result = make_validator().validate_batch(make_extraction(rows), batch_id="b")
        assert result.rows_rejected == 0

    def test_configured_bounds_reject_out_of_range(self):
        validator = make_validator(defect_rate_bounds=(0, 100))
        rows = [build_row("S-1"), build_row("S-2", defect_rates="150")]
        result = validator.validate_batch(make_extraction(rows), batch_id="b")
        assert result.rows_rejected == 1
        assert "defect_rates" in result.rejected_records[0].error_message
        assert "[0, 100]" in result.rejected_records[0].error_message

    def test_configured_bounds_accept_boundary_values(self):
        validator = make_validator(defect_rate_bounds=(0, 100))
        rows = [
            build_row("S-1", defect_rates="0"),
            build_row("S-2", defect_rates="100"),
        ]
        result = validator.validate_batch(make_extraction(rows), batch_id="b")
        assert result.rows_rejected == 0

    def test_tighter_bounds_take_effect_immediately(self):
        validator = make_validator(defect_rate_bounds=(0, 1))
        rows = [
            build_row("S-1", defect_rates="0.5"),
            build_row("S-2", defect_rates="1.5"),
        ]
        result = validator.validate_batch(make_extraction(rows), batch_id="b")
        assert result.rows_rejected == 1

    def test_inverted_bounds_refused_at_construction(self):
        with pytest.raises(ValueError, match="exceeds upper bound"):
            make_validator(defect_rate_bounds=(100, 0))


class TestRejectRateThreshold:
    def test_clean_batch_passes(self, fake_db):
        validator = make_validator(db=fake_db)
        result = validator.validate_batch(make_extraction(good_rows(10)), batch_id="b")
        assert result.status == "VALIDATED"
        assert result.success is True

    def test_exactly_at_threshold_passes_boundary(self):
        # 1 of 20 rejected is exactly 5.0% -- NOT strictly greater, so it
        # passes. This pins the boundary semantics decided in ADR-005.
        rows = good_rows(19) + [build_row(sku="")]
        result = make_validator().validate_batch(make_extraction(rows), batch_id="b")
        assert result.rows_rejected == 1
        assert result.status == "VALIDATED"

    def test_over_threshold_aborts_batch(self, fake_db):
        # 2 of 20 = 10% > 5%: FAILED, zero valid records, rejects retained.
        rows = good_rows(18) + [build_row(sku=""), build_row(sku="")]
        validator = make_validator(db=fake_db)
        result = validator.validate_batch(make_extraction(rows), batch_id="b")
        assert result.status == "FAILED"
        assert result.success is False
        assert result.valid_records == []
        assert len(result.rejected_records) == 2
        assert result.rows_loaded == 0
        assert "10.00%" in result.error_message
        assert "5.00%" in result.error_message

    def test_over_threshold_records_failed_in_manifest(self, fake_db):
        rows = good_rows(18) + [build_row(sku=""), build_row(sku="")]
        validator = make_validator(db=fake_db)
        validator.validate_batch(make_extraction(rows), batch_id="b-threshold")
        updates = fake_db.find_executed("UPDATE etl.batch_manifest")
        assert updates, "manifest must record the aborted batch"
        _, params = updates[-1]
        assert "FAILED" in params
        assert "b-threshold" in params

    def test_threshold_can_be_raised_by_configuration(self):
        rows = good_rows(8) + [build_row(sku="")]  # 10% breach at default 5%
        validator = make_validator(max_reject_rate_percent=15.0)
        result = validator.validate_batch(make_extraction(rows), batch_id="b")
        assert result.status == "VALIDATED"

    @pytest.mark.parametrize("bad", [0, -1.0])
    def test_non_positive_threshold_refused(self, bad):
        with pytest.raises(ValueError, match="must be positive"):
            make_validator(max_reject_rate_percent=bad)


class TestFileLevelFailures:
    def test_extraction_failure_is_fatal(self, fake_db):
        extraction = make_extraction(success=False, error_message="File not found: x.csv")
        result = make_validator(db=fake_db).validate_batch(extraction, batch_id="b")
        assert result.status == "FAILED"
        assert "Fatal extraction failure" in result.error_message
        assert fake_db.find_params("FAILED"), "manifest must record FAILED"

    def test_zero_rows_is_fatal(self, fake_db):
        result = make_validator(db=fake_db).validate_batch(make_extraction([]), batch_id="b")
        assert result.status == "FAILED"
        assert "Zero rows" in result.error_message

    def test_already_processed_hash_yields_duplicate_status(self):
        from conftest import FakeConnection

        db = FakeConnection(fetchone_results=[("prior-batch",)])
        result = make_validator(db=db).validate_batch(
            make_extraction(good_rows(3), file_hash=HASH), batch_id="b"
        )
        assert result.status == "DUPLICATE"
        assert result.success is True
        assert result.valid_records == []

    def test_validator_works_offline_without_db(self):
        result = make_validator(db=None).validate_batch(make_extraction(good_rows(3)), batch_id="b")
        assert result.status == "VALIDATED"


class TestReconciliationIdentity:
    def test_success_path_satisfies_rows_read_identity(self):
        # Contract: rows_read == rows_loaded + rows_rejected for every batch.
        rows = good_rows(4) + [build_row(sku="")]
        result = row_check_validator().validate_batch(make_extraction(rows), batch_id="b")
        assert result.rows_read == result.rows_loaded + result.rows_rejected
