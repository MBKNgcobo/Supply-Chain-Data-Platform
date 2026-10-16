import csv
import io
import json
import logging
from datetime import datetime

from pydantic import BaseModel, Field

from supply_chain_pipeline.manifest import BatchManifestManager
from supply_chain_pipeline.validate import (
    BatchValidationResult,
    RowReject,
    ValidatedRecord,
)

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("load")

# The bronze landing table and its column set, from
# sql/bootstrap/003_create_bronze_supply_chain_raw.sql.
#
# Every source column is TEXT in bronze; typing happens in dbt staging. This
# list is the contract between the loader and the DDL, and it is checked before
# the COPY runs, so an added column in the DDL that the loader does not know
# about is a startup failure rather than a silent schema drift.
BRONZE_TABLE = "bronze.supply_chain_raw"

BRONZE_SOURCE_COLUMNS: list[str] = [
    "product_type",
    "sku",
    "price",
    "availability",
    "number_of_products_sold",
    "revenue_generated",
    "customer_demographics",
    "stock_levels",
    "lead_times_1",
    "order_quantities",
    "shipping_times",
    "shipping_carriers",
    "shipping_costs",
    "supplier_name",
    "location",
    "lead_times_2",
    "production_volumes",
    "manufacturing_lead_time",
    "manufacturing_costs",
    "inspection_results",
    "defect_rates",
    "transportation_modes",
    "routes",
    "costs",
]

# Provenance columns written alongside every row, so any row can be traced back
# to the exact file and line that produced it.
PROVENANCE_COLUMNS: list[str] = [
    "source_batch_id",
    "source_identity",
    "file_hash",
    "row_number",
]


class LoadResult(BaseModel):
    """Output structure of the loading phase."""

    success: bool = Field(
        ..., description="True if the database transaction committed successfully."
    )
    batch_id: str
    rows_inserted_or_updated: int = 0
    rows_quarantined: int = 0
    error_message: str | None = None
    finished_at: datetime | None = None


class BatchLoader:
    """Handles secure transaction-wrapped bulk loading into PostgreSQL and final manifest status synchronization."""

    def __init__(self, db_connection):
        self.db = db_connection
        self.manifest_manager = BatchManifestManager(db_connection)

    def _stream_copy_to_staging(
        self, cursor, table_name: str, records: list[ValidatedRecord]
    ) -> None:
        """
        Streams valid records into `table_name` via COPY.

        The COPY header is built from the bronze column list intersected with the
        keys actually present in the data. Columns absent from this particular
        file are simply not in the COPY list, so they default to NULL rather than
        the loader inventing an empty string for them.

        A source column that is NOT a bronze column is reported and skipped: an
        unexpected new column in the upstream file should not abort an otherwise
        good load, but it must be visible, because it means the contract changed.
        """
        if not records:
            return

        sample_data = records[0].data
        data_columns = list(sample_data.keys())

        unknown = [c for c in data_columns if c not in BRONZE_SOURCE_COLUMNS]
        if unknown:
            logger.warning(
                "Source columns not present in %s and therefore not loaded: %s. "
                "The upstream file may have changed shape.",
                BRONZE_TABLE,
                ", ".join(sorted(unknown)),
            )

        # Preserve bronze's declared column order so the COPY is stable across runs.
        fieldnames = [c for c in BRONZE_SOURCE_COLUMNS if c in sample_data]
        headers = PROVENANCE_COLUMNS + fieldnames

        buffer = io.StringIO()
        writer = csv.writer(buffer, quoting=csv.QUOTE_MINIMAL)
        writer.writerow(headers)

        for rec in records:
            # source_batch_id is the file hash (see 003_create_bronze_supply_chain_raw.sql),
            # which is exactly rec.file_hash. Using it directly rather than splitting
            # source_identity, because source_identity is only parseable while the
            # hash happens to contain no '-' -- an assumption that would break silently.
            row_values = [
                rec.file_hash,  # source_batch_id
                rec.source_identity,  # source_identity
                rec.file_hash,  # file_hash
                rec.row_number,  # row_number
            ]
            for field in fieldnames:
                row_values.append(rec.data.get(field, ""))
            writer.writerow(row_values)

        buffer.seek(0)
        copy_sql = f"""
            COPY {table_name} ({", ".join(headers)})
            FROM STDIN WITH CSV HEADER;
        """
        cursor.copy_expert(copy_sql, buffer)

    def _bulk_insert_rejects(self, cursor, rejects: list[RowReject]) -> None:
        """
        Writes rejected rows to etl.row_rejects.

        raw_data must be serialised with json.dumps, not str(): str() produces
        Python dict repr (single quotes, None, True), which is not valid JSON and
        would make the ::jsonb cast fail -- turning a quarantine into a failed
        transaction, which is the opposite of what quarantine is for.
        """
        if not rejects:
            return

        for reject in rejects:
            cursor.execute(
                """
                INSERT INTO etl.row_rejects (file_hash, row_number, raw_data, error_message, rejected_at)
                VALUES (%s, %s, %s::jsonb, %s, %s);
                """,
                (
                    reject.file_hash,
                    reject.row_number,
                    json.dumps(reject.raw_data, default=str),
                    reject.error_message,
                    reject.rejected_at,
                ),
            )

    def persist_rejects(self, validation_result: BatchValidationResult) -> int:
        """
        Commits rejected rows to etl.row_rejects WITHOUT loading any valid
        rows (ADR-005).

        Used when a batch is aborted after validation -- currently the
        reject-rate threshold breach. Two things must hold at once: the
        rejected rows must be recorded (SPEC-001 §7.2: a reject must never be
        dropped without a record), and bronze must receive nothing from this
        batch (dbt reads bronze without consulting the manifest, so any
        committed row would be published). The manifest is already FAILED by
        the time this runs, and the manifest duplicate check honours only
        SUCCESS, so the operator can fix the file and re-feed it.

        Returns the number of rejects quarantined. Raises on quarantine
        failure: the caller must surface it, because an abort whose rejects
        are also lost is exactly the silence this path exists to prevent.
        """
        batch_id = validation_result.batch_id
        rejects = validation_result.rejected_records
        if not self.db:
            logger.error(
                "No database connection; %d rejected row(s) for batch %s cannot be quarantined.",
                len(rejects),
                batch_id,
            )
            return 0
        if not rejects:
            return 0
        try:
            with self.db.cursor() as cursor:
                self._bulk_insert_rejects(cursor, rejects)
            self.db.commit()
            logger.info(
                "Quarantined %d rejected row(s) for aborted batch %s (no rows loaded to bronze).",
                len(rejects),
                batch_id,
            )
            return len(rejects)
        except Exception as e:
            logger.error(f"Failed to quarantine rejects for aborted batch {batch_id}: {e}")
            if self.db:
                self.db.rollback()
            raise

    def load_batch(self, validation_result: BatchValidationResult) -> LoadResult:
        """
        Executes the load phase inside a strict database transaction block:
        1. Streams valid records into bronze.supply_chain_raw via COPY.
        2. Inserts rejected records into etl.row_rejects.
        3. Commits transaction and updates manifest status to SUCCESS.
        4. Rolls back and updates manifest status to FAILED if any error occurs.
        """
        batch_id = validation_result.batch_id
        load_result = LoadResult(success=False, batch_id=batch_id)

        # Handle pre-existing duplicate status or non-success validation states
        if not validation_result.success:
            load_result.success = False
            load_result.error_message = validation_result.error_message
            load_result.finished_at = datetime.utcnow()
            return load_result

        if validation_result.status == "DUPLICATE":
            load_result.success = True
            load_result.error_message = "Skipped load. File is a duplicate."
            load_result.finished_at = datetime.utcnow()
            return load_result

        if not self.db:
            load_result.error_message = "Database connection is not initialized."
            load_result.finished_at = datetime.utcnow()
            return load_result

        try:
            with self.db.cursor() as cursor:
                # 1. Stream valid records straight into bronze via COPY.
                #
                #    Bronze is append-only, so there is no temp table and no
                #    upsert. "Latest load wins" is resolved in stg_products, which
                #    is the only place that knows the grain (SPEC-001 Q5). Keeping
                #    the merge out of the loader means a re-run cannot silently
                #    overwrite the previous state -- it appends, and the previous
                #    state stays recoverable.
                #
                #    REPLAY SAFETY: bronze has a UNIQUE index on source_identity,
                #    and COPY cannot use ON CONFLICT, so re-loading a file whose
                #    rows are already present raises a unique violation. That is
                #    deliberate: it surfaces a replay instead of quietly
                #    doubling the row count. The manifest check in cli.py is what
                #    normally prevents this from being reached. See
                #    docs/decisions/ADR-001.
                self._stream_copy_to_staging(cursor, BRONZE_TABLE, validation_result.valid_records)

                # 2. Insert row-level rejects into quarantine
                self._bulk_insert_rejects(cursor, validation_result.rejected_records)

                # Reconciliation gate (SPEC-001 §7.3 / §10.7, ADR-005): every
                # row read must be accounted for as either loaded or rejected.
                # A mismatch means a row vanished between validation and load,
                # which must block SUCCESS rather than ship a batch whose
                # manifest counters are a guess. Checked before commit so a
                # breach leaves nothing in bronze.
                if (
                    validation_result.rows_read
                    != validation_result.rows_loaded + validation_result.rows_rejected
                ):
                    raise RuntimeError(
                        f"Row-count reconciliation failed for batch {batch_id}: "
                        f"rows_read={validation_result.rows_read} != "
                        f"rows_loaded={validation_result.rows_loaded} + "
                        f"rows_rejected={validation_result.rows_rejected}."
                    )

            # 3. Commit main transaction atomically
            self.db.commit()

            # 4. Update manifest to SUCCESS *after* successful commit
            self.manifest_manager.complete_batch(
                batch_id=batch_id,
                status="SUCCESS",
                rows_read=validation_result.rows_read,
                rows_loaded=validation_result.rows_loaded,
                rows_rejected=validation_result.rows_rejected,
            )

            load_result.success = True
            load_result.rows_inserted_or_updated = len(validation_result.valid_records)
            load_result.rows_quarantined = len(validation_result.rejected_records)
            load_result.finished_at = datetime.utcnow()
            logger.info(
                f"Successfully loaded batch {batch_id}: "
                f"{load_result.rows_inserted_or_updated} rows appended to {BRONZE_TABLE}."
            )

        except Exception as e:
            # Rollback transaction on failure
            logger.error(f"Transaction failed for batch {batch_id}. Rolling back. Error: {e}")
            if self.db:
                self.db.rollback()

            error_msg = str(e)
            load_result.success = False
            load_result.error_message = error_msg
            load_result.finished_at = datetime.utcnow()

            # Record failure in the manifest so the run is properly marked as FAILED and recoverable
            try:
                self.manifest_manager.complete_batch(
                    batch_id=batch_id,
                    status="FAILED",
                    rows_read=validation_result.rows_read,
                    rows_loaded=0,
                    rows_rejected=validation_result.rows_rejected,
                    error_message=error_msg,
                )
            except Exception as manifest_err:
                logger.error(f"Failed to record FAILED status in manifest: {manifest_err}")

        return load_result
