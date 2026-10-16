"""
Unit tests for extract.py: file-level validation, schema/header validation,
file hashing, and duplicate-file identification.

Fatal file-level conditions must make extraction FAIL (success=False with a
named reason), because cli.py maps that to exit code 1 and no row ever
reaches bronze. See docs/testing/unit-test-plan.md and SPEC-001 §7.1.
"""

import hashlib
import shutil

import pytest
from conftest import FIXTURES_DIR

from supply_chain_pipeline.extract import (
    CSVExtractor,
    DuplicateHeaderError,
    build_column_map,
    normalise_header,
)
from supply_chain_pipeline.load import BRONZE_SOURCE_COLUMNS

HAPPY = FIXTURES_DIR / "happy_path.csv"


def extract(path, **kwargs):
    return CSVExtractor(**kwargs).extract(path)


class TestFileLevelValidation:
    def test_missing_file_fails_with_named_reason(self):
        result = extract(FIXTURES_DIR / "does_not_exist.csv")
        assert result.success is False
        assert "File not found" in result.error_message

    def test_wrong_extension_fails(self, tmp_path):
        wrong = tmp_path / "data.txt"
        wrong.write_text("a,b\n1,2\n", encoding="utf-8")
        result = extract(wrong)
        assert result.success is False
        assert "Invalid file format" in result.error_message

    def test_empty_file_fails(self):
        result = extract(FIXTURES_DIR / "empty.csv")
        assert result.success is False
        assert "CSV file is empty" in result.error_message
        # The hash is still computed before the emptiness check, so the
        # operator can see WHICH file was empty.
        assert len(result.file_hash) == 64

    def test_binary_garbage_fails_via_required_headers(self):
        """Bytes no UTF codec accepts still decode as latin-1 (by design --
        latin-1 accepts every byte), so the "decodable" fatal check cannot be
        the one that fires; the garbage headers fail the required-header check
        instead. The unfirable "decodable" check is recorded in
        docs/testing/unit-test-plan.md, not faked here."""
        result = extract(FIXTURES_DIR / "binary_garbage.csv")
        assert result.success is False
        assert "Missing required column headers" in result.error_message

    def test_header_only_file_extracts_zero_rows(self):
        # Zero data rows is NOT an extraction failure: extraction succeeded,
        # the file is merely empty of rows. validate.py turns this into a
        # fatal zero-rows batch failure.
        result = extract(FIXTURES_DIR / "header_only.csv")
        assert result.success is True
        assert result.total_rows == 0


class TestHeaderNormalisation:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Product Type", "product_type"),
            ("  Revenue generated ", "revenue_generated"),
            ("Defect rates (%)", "defect_rates"),
            ("SKU", "sku"),
            (None, ""),
            ("!!!", ""),
        ],
    )
    def test_normalise_header(self, raw, expected):
        assert normalise_header(raw) == expected

    def test_lead_time_collision_resolved_by_alias_not_by_suffix(self):
        columns = build_column_map(["Lead times", "Lead time"])
        assert columns == ["lead_times_1", "lead_times_2"]

    def test_blank_header_becomes_addressable_not_dropped(self):
        # A discarded column would shift every later column by one and
        # silently misalign the row.
        columns = build_column_map(["SKU", "", "Price"])
        assert columns == ["sku", "unnamed_column_2", "price"]

    def test_genuine_duplicate_header_raises(self):
        with pytest.raises(DuplicateHeaderError) as excinfo:
            build_column_map(["SKU", "Price", "price"])
        assert excinfo.value.duplicates == ["price"]

    def test_duplicate_header_error_lists_every_collision(self):
        with pytest.raises(DuplicateHeaderError) as excinfo:
            build_column_map(["Price", "price", "SKU", "sku", "Costs", " costs "])
        # Reported sorted, so one re-feed fixes every collision at once.
        assert excinfo.value.duplicates == ["costs", "price", "sku"]


class TestSchemaValidation:
    def test_happy_file_headers_map_to_bronze_columns_in_order(self):
        result = extract(HAPPY)
        assert result.success is True
        # The extractor's output column list IS the bronze contract list, in
        # the same order -- if either side drifts, this test names it.
        assert result.headers == BRONZE_SOURCE_COLUMNS

    def test_duplicate_headers_fail_extraction_whole_file(self):
        result = extract(FIXTURES_DIR / "duplicate_headers.csv")
        assert result.success is False
        assert "Duplicate source header" in result.error_message
        assert "price" in result.error_message

    def test_missing_required_header_fails(self, tmp_path):
        headers = ["Product Type", "Price"]  # no SKU
        path = tmp_path / "missing_sku_header.csv"
        path.write_text(",".join(headers) + "\nElectronics,29.99\n", encoding="utf-8")
        result = extract(path)
        assert result.success is False
        assert "Missing required column headers" in result.error_message
        assert "sku" in result.error_message

    def test_required_headers_accept_source_spelling(self, tmp_path):
        # The contract marks 'Product Type' and 'SKU' required; the caller may
        # pass either spelling.
        path = tmp_path / "source_spelling.csv"
        path.write_text("Product Type,SKU\nElectronics,S-1\n", encoding="utf-8")
        result = CSVExtractor(required_headers=["Product Type", "SKU"]).extract(path)
        assert result.success is True
        assert result.rows == [{"product_type": "Electronics", "sku": "S-1"}]


class TestRows:
    def test_happy_file_rows_extracted_positionally(self):
        result = extract(HAPPY)
        assert result.total_rows == 5
        assert result.rows[0]["sku"] == "ELEC-001"
        assert result.rows[0]["lead_times_1"] == "14"  # 'Lead times'
        assert result.rows[0]["lead_times_2"] == "10"  # 'Lead time'
        assert result.rows[2]["customer_demographics"] == ""  # blank survives

    def test_ragged_row_padded_not_truncated(self):
        # A short row must not be silently right-truncated into shifted
        # columns; missing fields become "" and the row survives.
        result = extract(FIXTURES_DIR / "ragged_row.csv")
        assert result.success is True
        assert result.total_rows == 2
        assert result.rows[1]["costs"] == ""
        assert result.rows[1]["sku"] == "ELEC-002"

    def test_utf8_bom_is_tolerated(self, tmp_path):
        path = tmp_path / "bom.csv"
        path.write_bytes("﻿Product Type,SKU\nElectronics,S-1\n".encode())
        result = extract(path)
        assert result.success is True
        assert result.rows[0]["sku"] == "S-1"


class TestHashingAndDuplicates:
    def test_hash_matches_hashlib_sha256_of_file_bytes(self):
        expected = hashlib.sha256(HAPPY.read_bytes()).hexdigest()
        assert extract(HAPPY).file_hash == expected

    def test_identical_content_hashes_identically_from_any_path(self, tmp_path):
        copy_a = tmp_path / "a.csv"
        copy_b = tmp_path / "b.csv"
        shutil.copyfile(HAPPY, copy_a)
        shutil.copyfile(HAPPY, copy_b)
        assert extract(copy_a).file_hash == extract(copy_b).file_hash

    def test_single_byte_change_changes_the_hash(self, tmp_path):
        original = HAPPY.read_bytes()
        changed = tmp_path / "changed.csv"
        changed.write_bytes(original.replace(b"29.99", b"29.98"))
        assert extract(HAPPY).file_hash != extract(changed).file_hash

    def test_known_hash_flags_duplicate_without_skipping_work(self):
        known = extract(HAPPY).file_hash
        result = CSVExtractor(existing_hashes={known}).extract(HAPPY)
        assert result.is_duplicate is True
        # Duplicate detection does not skip extraction: the rows are still
        # returned, and the cli/manifest layer decides what to do.
        assert result.total_rows == 5

    def test_unknown_hash_is_not_duplicate(self):
        assert extract(HAPPY).is_duplicate is False
