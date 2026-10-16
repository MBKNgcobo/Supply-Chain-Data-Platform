import argparse
import logging
import sys
import uuid
from pathlib import Path

# Import pipeline components
from supply_chain_pipeline.extract import DEFAULT_REQUIRED_HEADERS, CSVExtractor
from supply_chain_pipeline.load import BatchLoader
from supply_chain_pipeline.manifest import BatchManifestManager
from supply_chain_pipeline.validate import (
    DEFAULT_MAX_REJECT_RATE_PERCENT,
    BatchValidator,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("cli")


def get_db_connection():
    """
    Helper to establish a PostgreSQL database connection.
    Replace with your actual connection pool or environment configuration (e.g., psycopg2).
    """
    try:
        import importlib
        import os

        psycopg2 = importlib.import_module("psycopg2")
        # Example using environment variables
        conn = psycopg2.connect(
            dbname=os.getenv("DB_NAME", "pipeline_db"),
            user=os.getenv("DB_USER", "postgres"),
            password=os.getenv("DB_PASSWORD", "postgres"),
            host=os.getenv("DB_HOST", "localhost"),
            port=os.getenv("DB_PORT", "5432"),
        )
        return conn
    except ImportError:
        logger.warning("psycopg2 not installed. Running in mock/offline database mode.")
        return None
    except Exception as e:
        logger.error(f"Failed to connect to PostgreSQL database: {e}")
        raise


def run_pipeline(
    file_path: str,
    pipeline_name: str = "supply_chain_ingestion_pipeline",
    required_headers: list | None = None,
    max_reject_rate_percent: float = DEFAULT_MAX_REJECT_RATE_PERCENT,
) -> int:
    """
    Orchestrates the complete end-to-end ETL pipeline execution:
    1. Sets up manifest & checks duplicate status.
    2. Extracts and validates file contents.
    3. Validates row constraints and handles rejects.
    4. Loads valid records atomically into PostgreSQL.
    5. Returns 0 on success, non-zero on failure.

    Exit codes (SPEC-001 §10.8, ADR-005):
        0  success, or a duplicate file skipped as a no-op
        1  extraction failure (file-level fatal checks)
        2  validation failure (zero rows, reject-rate breach, idempotency error)
        3  load failure (transaction rolled back; manifest FAILED)
        99 unhandled exception

    Note on required_headers: the default used to be
    ["sku", "description", "price", "quantity"], which described a different
    source entirely -- neither 'description' nor 'quantity' exists in
    supply_chain_data.csv, so extraction failed on every real file. The default
    is now the contract's required columns. See ADR-001.
    """
    if required_headers is None:
        required_headers = DEFAULT_REQUIRED_HEADERS

    batch_id = f"batch-{uuid.uuid4()}"
    path = Path(file_path)
    logger.info(f"Starting pipeline '{pipeline_name}' for file: {path.name} [Batch ID: {batch_id}]")

    db_conn = None
    try:
        db_conn = get_db_connection()
        manifest_mgr = BatchManifestManager(db_conn)

        # Ensure schema & manifest table exist
        manifest_mgr.setup_manifest_table()

        # Step 1: Extraction & Hashing
        # We instantiate extractor without a pre-filtered set since manifest check handles it globally
        extractor = CSVExtractor(required_headers=required_headers)
        extraction_result = extractor.extract(path)

        if not extraction_result.success:
            logger.error(f"Extraction failed: {extraction_result.error_message}")
            return 1

        # Check manifest duplicate before proceeding further
        if manifest_mgr.is_already_processed(extraction_result.file_hash):
            logger.info(
                f"Duplicate file detected via manifest. Hash {extraction_result.file_hash} already processed successfully. Skipping."
            )
            return 0

        # Step 2: Validation & Manifest Registration
        validator = BatchValidator(
            pipeline_name=pipeline_name,
            db_connection=db_conn,
            max_reject_rate_percent=max_reject_rate_percent,
        )
        validation_result = validator.validate_batch(extraction_result, batch_id)

        # A duplicate is a successful no-op, not a failure: nothing to load,
        # nothing to alert on. Reported distinctly from the FAILED path so the
        # exit code stays unambiguous (ADR-005).
        if validation_result.status == "DUPLICATE":
            logger.info(
                f"Batch {batch_id} skipped: hash {extraction_result.file_hash} "
                f"was already processed successfully."
            )
            return 0

        if not validation_result.success and validation_result.status == "FAILED":
            logger.error(f"Validation fatal error: {validation_result.error_message}")
            # An aborted batch still records its rejects (ADR-005): quarantine
            # them without loading any valid rows, so bronze receives nothing
            # and dbt has nothing to publish from this file.
            if validation_result.rejected_records:
                try:
                    quarantined = BatchLoader(db_connection=db_conn).persist_rejects(
                        validation_result
                    )
                    logger.info(
                        f"Quarantined {quarantined} rejected row(s) for aborted batch {batch_id}."
                    )
                except Exception as quarantine_error:
                    logger.error(
                        f"Could not quarantine rejects for aborted batch {batch_id}: {quarantine_error}"
                    )
            return 2

        # Step 3: Loading into PostgreSQL (Atomic Transaction + COPY)
        loader = BatchLoader(db_connection=db_conn)
        load_result = loader.load_batch(validation_result)

        if not load_result.success:
            logger.error(f"Load phase failed: {load_result.error_message}")
            return 3

        logger.info(
            f"Pipeline completed successfully! "
            f"Rows loaded: {load_result.rows_inserted_or_updated}, "
            f"Rows quarantined: {load_result.rows_quarantined}"
        )
        return 0

    except Exception as e:
        logger.exception(f"Unhandled exception during pipeline execution: {e}")
        if db_conn:
            try:
                db_conn.rollback()
            except Exception:
                pass
        return 99
    finally:
        if db_conn:
            db_conn.close()


def _positive_float(value: str) -> float:
    """argparse type: a positive float, or a hard error.

    A zero or negative reject-rate would silently disable the publish gate
    (SPEC-001 §7.2, ADR-005), so it is refused at the command line rather
    than accepted and quietly meaningless.
    """
    try:
        number = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not a number.")
    if number <= 0:
        raise argparse.ArgumentTypeError(
            f"{value!r} must be positive; a non-positive reject-rate "
            f"would disable the reject-rate gate."
        )
    return number


def main():
    parser = argparse.ArgumentParser(
        description="Command-Line Interface for the CSV Ingestion ETL Pipeline."
    )
    parser.add_argument("--file", required=True, help="Path to the incoming CSV file to ingest.")
    parser.add_argument(
        "--pipeline-name",
        default="supply_chain_ingestion_pipeline",
        help="Name of the pipeline for manifest tracking.",
    )
    parser.add_argument(
        "--headers",
        nargs="+",
        default=None,
        help=(
            "Required column headers for CSV validation. Accepts either the source "
            "spelling ('Product Type') or the bronze spelling ('product_type'). "
            "Defaults to the columns marked required in the source contract: "
            + ", ".join(DEFAULT_REQUIRED_HEADERS)
        ),
    )
    parser.add_argument(
        "--max-reject-rate-percent",
        type=_positive_float,
        default=DEFAULT_MAX_REJECT_RATE_PERCENT,
        help=(
            "Batch-abort threshold: a batch whose rejected-row rate exceeds this "
            f"percentage fails without loading any rows (default: {DEFAULT_MAX_REJECT_RATE_PERCENT}). "
            "Must be positive; see docs/decisions/ADR-005-validation-failure-semantics.md."
        ),
    )

    args = parser.parse_args()

    # Execute pipeline and exit with appropriate status code (Requirement B)
    exit_code = run_pipeline(
        file_path=args.file,
        pipeline_name=args.pipeline_name,
        required_headers=args.headers,
        max_reject_rate_percent=args.max_reject_rate_percent,
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
