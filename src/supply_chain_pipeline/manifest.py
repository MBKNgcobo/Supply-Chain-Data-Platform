import logging
from datetime import datetime

from pydantic import BaseModel, Field

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("manifest")


class ManifestRecord(BaseModel):
    """Pydantic model representing a row in the etl.batch_manifest table."""

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


class BatchManifestManager:
    """Manages tracking of processed files and batch runs in PostgreSQL."""

    def __init__(self, db_connection):
        """
        Args:
            db_connection: An active PostgreSQL connection object.
        """
        self.db = db_connection

    def setup_manifest_table(self) -> None:
        """Ensures the etl schema and batch_manifest table exist in PostgreSQL."""
        if not self.db:
            return

        try:
            with self.db.cursor() as cursor:
                cursor.execute("""
                    CREATE SCHEMA IF NOT EXISTS etl;
                    
                    CREATE TABLE IF NOT EXISTS etl.batch_manifest (
                        batch_id TEXT PRIMARY KEY,
                        pipeline_name TEXT NOT NULL,
                        source_file TEXT NOT NULL,
                        source_hash TEXT NOT NULL,
                        started_at TIMESTAMP WITH TIME ZONE NOT NULL,
                        finished_at TIMESTAMP WITH TIME ZONE,
                        status TEXT NOT NULL,
                        rows_read INT DEFAULT 0,
                        rows_loaded INT DEFAULT 0,
                        rows_rejected INT DEFAULT 0,
                        error_message TEXT
                    );
                    
                    CREATE INDEX IF NOT EXISTS idx_batch_manifest_hash 
                    ON etl.batch_manifest (source_hash) 
                    WHERE status = 'SUCCESS';
                """)
            self.db.commit()
            logger.info("Batch manifest schema and table verified/created successfully.")
        except Exception as e:
            logger.error(f"Failed to set up batch manifest table: {e}")
            if self.db:
                self.db.rollback()
            raise

    def is_already_processed(self, source_hash: str) -> bool:
        """
        Checks if a file with the given SHA-256 hash has already been successfully processed.
        Returns True to prevent duplicate loads (Requirement A).
        """
        if not self.db:
            return False

        try:
            with self.db.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT batch_id 
                    FROM etl.batch_manifest 
                    WHERE source_hash = %s AND status = 'SUCCESS';
                    """,
                    (source_hash,),
                )
                result = cursor.fetchone()
                return result is not None
        except Exception as e:
            logger.error(f"Error checking manifest for hash {source_hash}: {e}")
            raise

    def start_batch(
        self, batch_id: str, pipeline_name: str, source_file: str, source_hash: str
    ) -> datetime:
        """Records the initialization of a batch run with a PENDING status."""
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
                    ON CONFLICT (batch_id) DO UPDATE 
                    SET started_at = EXCLUDED.started_at,
                        status = 'PENDING';
                    """,
                    (batch_id, pipeline_name, source_file, source_hash, started_at),
                )
            self.db.commit()
            logger.info(f"Batch {batch_id} registered as PENDING.")
        except Exception as e:
            logger.error(f"Failed to record batch start in manifest: {e}")
            if self.db:
                self.db.rollback()
            raise

        return started_at

    def complete_batch(
        self,
        batch_id: str,
        status: str,
        rows_read: int,
        rows_loaded: int,
        rows_rejected: int,
        error_message: str | None = None,
    ) -> None:
        """Updates the batch manifest upon completion (SUCCESS, FAILED, or DUPLICATE)."""
        if not self.db:
            return

        finished_at = datetime.utcnow()
        try:
            with self.db.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE etl.batch_manifest 
                    SET finished_at = %s,
                        status = %s,
                        rows_read = %s,
                        rows_loaded = %s,
                        rows_rejected = %s,
                        error_message = %s
                    WHERE batch_id = %s;
                    """,
                    (
                        finished_at,
                        status,
                        rows_read,
                        rows_loaded,
                        rows_rejected,
                        error_message,
                        batch_id,
                    ),
                )
            self.db.commit()
            logger.info(f"Batch {batch_id} completed with status: {status}")
        except Exception as e:
            logger.error(f"Failed to update batch completion in manifest: {e}")
            if self.db:
                self.db.rollback()
            raise

    def get_last_successful_run(self, pipeline_name: str) -> ManifestRecord | None:
        """Retrieves metadata of the last successful run for incremental watermarking."""
        if not self.db:
            return None

        try:
            with self.db.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT batch_id, pipeline_name, source_file, source_hash, 
                           started_at, finished_at, status, rows_read, rows_loaded, rows_rejected, error_message
                    FROM etl.batch_manifest
                    WHERE pipeline_name = %s AND status = 'SUCCESS'
                    ORDER BY finished_at DESC
                    LIMIT 1;
                    """,
                    (pipeline_name,),
                )
                row = cursor.fetchone()
                if not row:
                    return None

                return ManifestRecord(
                    batch_id=row[0],
                    pipeline_name=row[1],
                    source_file=row[2],
                    source_hash=row[3],
                    started_at=row[4],
                    finished_at=row[5],
                    status=row[6],
                    rows_read=row[7],
                    rows_loaded=row[8],
                    rows_rejected=row[9],
                    error_message=row[10],
                )
        except Exception as e:
            logger.error(f"Failed to fetch last successful run for pipeline {pipeline_name}: {e}")
            return None
