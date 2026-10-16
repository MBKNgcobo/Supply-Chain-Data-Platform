import logging
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, Field

from supply_chain_pipeline.extract import BatchExtractionResult

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("validate")


# Columns whose non-blank values must PARSE as the type dbt staging will cast
# them to (dbt/models/staging/stg_products.sql, per ADR-001). The check
# predicts cast failures; it never casts. Bronze stays TEXT (ADR-001).
#
# A blank or missing value is NOT a reject: it becomes NULL downstream, and
# the count of missing values must stay observable (stg_products header).
#
# NOTE: the types here mirror the stg_products casts, which disagree with
# docs/contracts/sources/supply_chain_data-csv.contract.yaml for a few
# columns the contract marks `target_type: numeric` where staging casts
# ::integer (stock_levels, order_quantities, shipping_times,
# production_volumes, manufacturing_lead_time). The check follows staging,
# because staging is what would actually fail. Recorded in
# docs/testing/unit-test-plan.md as contract/staging drift for SPEC-001 Q7.
NUMERIC_COLUMNS: dict[str, str] = {
    "availability": "integer",
    "price": "numeric",
    "number_of_products_sold": "integer",
    "revenue_generated": "numeric",
    "stock_levels": "integer",
    "order_quantities": "integer",
    "shipping_times": "integer",
    "shipping_costs": "numeric",
    "production_volumes": "integer",
    "manufacturing_lead_time": "integer",
    "manufacturing_costs": "numeric",
    "lead_times_1": "integer",
    "lead_times_2": "integer",
    "defect_rates": "numeric",
    "costs": "numeric",
}

# Reject-rate gate for the whole batch (SPEC-001 §7.2: ">5% of rows rejected
# halts publish and alerts"). Agreed at 5.0 -- see docs/decisions/
# ADR-005-validation-failure-semantics.md. Operator-tunable per run via
# cli.py --max-reject-rate-percent; must be positive (a non-positive value
# would silently disable the gate).
DEFAULT_MAX_REJECT_RATE_PERCENT = 5.0

# Integer literal, optional sign. Anything else -- '12.5', '1,500', 'twelve',
# 'NaN' -- would fail the ::integer cast in staging, so it is a reject here.
_INTEGER_PATTERN = re.compile(r"[+-]?[0-9]+\Z")


class ValidatedRecord(BaseModel):
    """Represents a clean, validated individual business record ready for loading."""

    source_identity: str = Field(
        ..., description="Stable unique source ID: file_hash + row_number."
    )
    file_hash: str = Field(..., description="SHA-256 hash of the source file.")
    row_number: int = Field(..., description="1-based index of the row in the source file.")
    data: dict[str, Any] = Field(..., description="Cleaned row payload.")


class RowReject(BaseModel):
    """Represents an invalid row captured for the etl.row_rejects quarantine table."""

    file_hash: str
    row_number: int
    raw_data: dict[str, Any]
    error_message: str
    rejected_at: datetime = Field(default_factory=datetime.utcnow)


class BatchValidationResult(BaseModel):
    """Output structure of the validation phase."""

    success: bool = Field(..., description="False if a fatal file-level error occurred.")
    batch_id: str
    pipeline_name: str
    source_file: str
    source_hash: str
    started_at: datetime
    finished_at: datetime | None = None
    status: str = Field("PENDING", description="PENDING, SUCCESS, FAILED, or DUPLICATE")
    rows_read: int = 0
    rows_loaded: int = 0
    rows_rejected: int = 0
    error_message: str | None = None
    valid_records: list[ValidatedRecord] = Field(default_factory=list)
    rejected_records: list[RowReject] = Field(default_factory=list)


class BatchValidator:
    """Handles row-level validation, idempotency checks against the manifest, and transaction safety."""

    def __init__(
        self,
        pipeline_name: str,
        db_connection=None,
        max_reject_rate_percent: float = DEFAULT_MAX_REJECT_RATE_PERCENT,
        defect_rate_bounds: tuple[Any, Any] | None = None,
    ):
        """
        Args:
            max_reject_rate_percent: batch-abort threshold (SPEC-001 §7.2).
                A batch whose reject rate is strictly greater than this
                percentage fails; exactly-at-threshold passes. Must be
                positive -- a zero or negative value would silently disable
                the gate, so it is refused here as well as in cli.py.
            defect_rate_bounds: optional (low, high) for the defect_rates
                column. Dormant (None) until SPEC-001 Q7 decides the range;
                per the glossary a range is withheld, never guessed. When
                bounds are configured, out-of-range values are row rejects.
        """
        if max_reject_rate_percent <= 0:
            raise ValueError(
                "max_reject_rate_percent must be positive; refusing to run "
                "with a disabled reject-rate gate."
            )
        if defect_rate_bounds is not None:
            low, high = defect_rate_bounds
            if low > high:
                raise ValueError(
                    f"defect_rate_bounds lower bound ({low}) exceeds upper bound ({high})."
                )
        self.pipeline_name = pipeline_name
        self.db = db_connection
        self.max_reject_rate_percent = float(max_reject_rate_percent)
        self.defect_rate_bounds = defect_rate_bounds

    @staticmethod
    def _is_blank(value: Any) -> bool:
        return value is None or not str(value).strip()

    def _check_numeric_columns(self, row: dict[str, Any]) -> None:
        """
        Raises ValueError if any non-blank value in NUMERIC_COLUMNS cannot be
        parsed as the type stg_products will cast it to. Blank stays null.
        """
        for column, kind in NUMERIC_COLUMNS.items():
            raw = row.get(column)
            if self._is_blank(raw):
                continue
            text = str(raw).strip()
            if kind == "integer":
                parses = _INTEGER_PATTERN.match(text) is not None
            else:
                try:
                    parses = Decimal(text).is_finite()
                except InvalidOperation:
                    parses = False
            if not parses:
                raise ValueError(f"Unparseable {kind} value in column '{column}': {text!r}.")

    def _check_defect_rate_bounds(self, row: dict[str, Any]) -> None:
        """
        Row-level range check for defect_rates. Dormant while
        self.defect_rate_bounds is None (SPEC-001 Q7 undecided). Values that
        fail the numeric parse check have already been rejected by
        _check_numeric_columns, so anything reaching here that parses is
        compared against the configured bounds.
        """
        if self.defect_rate_bounds is None:
            return
        raw = row.get("defect_rates")
        if self._is_blank(raw):
            return
        try:
            value = Decimal(str(raw).strip())
        except InvalidOperation:
            return  # already rejected as unparseable
        if not value.is_finite():
            return
        low, high = (Decimal(str(b)) for b in self.defect_rate_bounds)
        if value < low or value > high:
            raise ValueError(
                f"defect_rates value {value} outside accepted bounds [{low}, {high}] (SPEC-001 Q7)."
            )

    def _check_manifest_duplicate(self, source_hash: str) -> bool:
        if not self.db:
            return False
        try:
            with self.db.cursor() as cursor:
                cursor.execute(
                    "SELECT status FROM etl.batch_manifest WHERE source_hash = %s AND status = 'SUCCESS';",
                    (source_hash,),
                )
                return cursor.fetchone() is not None
        except Exception as e:
            logger.error(f"Failed to query batch manifest for duplication check: {e}")
            raise

    def _log_manifest_start(self, batch_id: str, source_file: str, source_hash: str) -> datetime:
        started_at = datetime.utcnow()
        if not self.db:
            return started_at

        try:
            with self.db.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO etl.batch_manifest 
                    (batch_id, pipeline_name, source_file, source_hash, started_at, status, rows_read, rows_loaded, rows_rejected)
                    VALUES (%s, %s, %s, %s, %s, 'PENDING', 0, 0, 0)
                    ON CONFLICT (batch_id) DO UPDATE SET started_at = EXCLUDED.started_at;
                    """,
                    (
                        batch_id,
                        self.pipeline_name,
                        source_file,
                        source_hash,
                        started_at,
                    ),
                )
            self.db.commit()
        except Exception as e:
            logger.error(f"Failed to write start state to batch manifest: {e}")
            if self.db:
                self.db.rollback()
            raise
        return started_at

    def _update_manifest_completion(
        self,
        batch_id: str,
        status: str,
        rows_read: int,
        rows_loaded: int,
        rows_rejected: int,
        error_message: str | None = None,
    ):
        """Helper used only for fatal errors during validation stage."""
        if not self.db:
            return
        try:
            with self.db.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE etl.batch_manifest 
                    SET finished_at = %s, status = %s, rows_read = %s, rows_loaded = %s, rows_rejected = %s, error_message = %s
                    WHERE batch_id = %s;
                    """,
                    (
                        datetime.utcnow(),
                        status,
                        rows_read,
                        rows_loaded,
                        rows_rejected,
                        error_message,
                        batch_id,
                    ),
                )
            self.db.commit()
        except Exception as e:
            logger.error(f"Failed to update batch manifest completion: {e}")
            if self.db:
                self.db.rollback()

    def validate_batch(
        self, extraction_result: BatchExtractionResult, batch_id: str
    ) -> BatchValidationResult:
        started_at = datetime.utcnow()
        source_file = extraction_result.file_path
        source_hash = extraction_result.file_hash

        validation_result = BatchValidationResult(
            success=False,
            batch_id=batch_id,
            pipeline_name=self.pipeline_name,
            source_file=source_file,
            source_hash=source_hash,
            started_at=started_at,
        )

        # 1. Handle File-Level Extraction Failures
        if not extraction_result.success:
            error_msg = f"Fatal extraction failure: {extraction_result.error_message}"
            logger.error(error_msg)
            validation_result.error_message = error_msg
            validation_result.status = "FAILED"
            validation_result.finished_at = datetime.utcnow()
            self._log_manifest_start(batch_id, source_file, source_hash)
            self._update_manifest_completion(batch_id, "FAILED", 0, 0, 0, error_msg)
            return validation_result

        # 2. Check Idempotency Manifest
        try:
            if self._check_manifest_duplicate(source_hash):
                logger.info(f"Duplicate file detected via manifest. Hash: {source_hash}")
                validation_result.success = True
                validation_result.status = "DUPLICATE"
                validation_result.finished_at = datetime.utcnow()
                return validation_result
        except Exception as e:
            validation_result.success = False
            validation_result.status = "FAILED"
            validation_result.error_message = f"Database error during idempotency check: {e!s}"
            validation_result.finished_at = datetime.utcnow()
            return validation_result

        # Log start of processing in manifest (status = PENDING)
        started_at = self._log_manifest_start(batch_id, source_file, source_hash)
        validation_result.started_at = started_at

        rows_read = len(extraction_result.rows)
        validation_result.rows_read = rows_read

        # 3. Fatal File-Level Check: Zero valid rows
        if rows_read == 0:
            error_msg = "Fatal file-level error: Zero rows found in source file."
            logger.error(error_msg)
            validation_result.status = "FAILED"
            validation_result.error_message = error_msg
            validation_result.finished_at = datetime.utcnow()
            self._update_manifest_completion(batch_id, "FAILED", 0, 0, 0, error_msg)
            return validation_result

        valid_records: list[ValidatedRecord] = []
        rejected_records: list[RowReject] = []
        # sku uniqueness within the file (contract row check
        # sku_unique_in_file): the first occurrence wins, every later row
        # carrying the same trimmed sku is rejected. Latest-load-wins in
        # stg_products would eventually resolve duplicates anyway, but the
        # contract requires the collision to be visible per row, not absorbed.
        first_occurrence_row: dict[str, int] = {}

        # 4. Row-Level Validation Loop
        for idx, row in enumerate(extraction_result.rows, start=1):
            try:
                sku = row.get("sku")
                if not sku or not str(sku).strip():
                    raise ValueError("Missing or empty mandatory field: 'sku'.")

                normalised_sku = str(sku).strip()
                if normalised_sku in first_occurrence_row:
                    raise ValueError(
                        f"Duplicate sku '{normalised_sku}' in file: first occurrence "
                        f"at row {first_occurrence_row[normalised_sku]}; keeping the "
                        f"first occurrence."
                    )

                self._check_numeric_columns(row)
                self._check_defect_rate_bounds(row)

                first_occurrence_row.setdefault(normalised_sku, idx)

                source_identity = f"{source_hash}-{idx}"
                valid_records.append(
                    ValidatedRecord(
                        source_identity=source_identity,
                        file_hash=source_hash,
                        row_number=idx,
                        data=row,
                    )
                )
            except Exception as row_error:
                logger.warning(f"Row {idx} rejected: {row_error}")
                rejected_records.append(
                    RowReject(
                        file_hash=source_hash,
                        row_number=idx,
                        raw_data=row,
                        error_message=str(row_error),
                    )
                )

        rows_rejected = len(rejected_records)
        reject_rate_percent = (rows_rejected / rows_read) * 100.0

        validation_result.rows_loaded = len(valid_records)
        validation_result.rows_rejected = rows_rejected
        validation_result.valid_records = valid_records
        validation_result.rejected_records = rejected_records
        validation_result.finished_at = datetime.utcnow()

        # 5. Batch-level reject-rate gate (SPEC-001 §7.2, ADR-005).
        #
        # A breached batch is ABORTED, not partially loaded: valid rows are
        # dropped from the result so the loader cannot COPY them, the rejected
        # rows are kept so cli.py can still quarantine them, and the manifest
        # records FAILED. dbt reads bronze without consulting the manifest, so
        # committing even the "valid" portion would publish a batch this gate
        # just condemned. The file stays in data/incoming/ for fix-and-re-feed;
        # the manifest duplicate check honours only SUCCESS, so replay needs
        # no manual cleanup.
        if reject_rate_percent > self.max_reject_rate_percent:
            error_msg = (
                f"Reject-rate threshold breached: {rows_rejected} of {rows_read} rows "
                f"rejected ({reject_rate_percent:.2f}% > {self.max_reject_rate_percent:.2f}%). "
                f"Batch aborted; no rows will be loaded to bronze. "
                f"Rejected rows are quarantined in etl.row_rejects for investigation."
            )
            logger.error(error_msg)
            validation_result.success = False
            validation_result.status = "FAILED"
            validation_result.error_message = error_msg
            validation_result.rows_loaded = 0
            validation_result.valid_records = []
            self._update_manifest_completion(
                batch_id, "FAILED", rows_read, 0, rows_rejected, error_msg
            )
            return validation_result

        validation_result.success = True
        validation_result.status = "VALIDATED"  # Passed validation, ready for load

        # NOTE: Manifest status update to SUCCESS/FAILED is now handed over to load.py
        return validation_result
