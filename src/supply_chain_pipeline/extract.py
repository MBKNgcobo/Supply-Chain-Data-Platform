import csv
import hashlib
import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("extract")

# Headers whose source spelling does not normalise to the bronze column name.
#
# The source file carries BOTH 'Lead times' and 'Lead time' as separate columns
# (SPEC-001 section 11 Q3). They are different strings, so a generic snake_case
# pass yields 'lead_times' and 'lead_time' -- but bronze.supply_chain_raw declares
# lead_times_1 and lead_times_2, per docs/contracts/sources/. These aliases are
# that contract, in code.
#
# This map is deliberately tiny and explicit. It is the only place where a source
# header is renamed for a reason other than snake_case, so it is the one place to
# look when the upstream file changes. If Q3 is ever resolved and the two columns
# collapse, delete these two entries.
SOURCE_HEADER_ALIASES: dict[str, str] = {
    "lead_times": "lead_times_1",  # source header 'Lead times'
    "lead_time": "lead_times_2",  # source header 'Lead time'
}

# Headers the source contract marks required: true. SPEC-001 section 3.1.
DEFAULT_REQUIRED_HEADERS: list[str] = ["product_type", "sku"]


def normalise_header(raw: str) -> str:
    """
    Turns a source CSV header into a bronze column name.

    'Product Type'          -> 'product_type'
    '  Revenue generated '  -> 'revenue_generated'
    'Defect rates (%)'      -> 'defect_rates'

    Rules: trim, lowercase, collapse runs of anything that is not a letter or
    digit into a single underscore, and strip leading/trailing underscores.
    """
    if raw is None:
        return ""

    text = raw.strip().lower()
    cleaned: list[str] = []
    previous_was_separator = False

    for char in text:
        if char.isalnum():
            cleaned.append(char)
            previous_was_separator = False
        elif not previous_was_separator:
            # collapse runs of punctuation/whitespace into one underscore
            cleaned.append("_")
            previous_was_separator = True

    return "".join(cleaned).strip("_")


class DuplicateHeaderError(ValueError):
    """Raised when two source headers normalise to the same bronze column name.

    Carries every colliding name so the operator sees the full set in one
    message rather than fixing them one re-feed at a time.
    """

    def __init__(self, duplicates: list[str]):
        self.duplicates = duplicates
        super().__init__(
            "Duplicate source header(s) after normalisation: "
            + ", ".join(duplicates)
            + ". The file's shape no longer matches the source contract; "
            "no safe positional mapping exists, so the file is rejected. "
            "See SPEC-001 §7.1 / §10.5."
        )


def build_column_map(header_row: list[str]) -> list[str]:
    """
    Maps each position in the source header row to its bronze column name.

    Returns one bronze column name per source position, so the mapping is
    positional and every source column survives, including duplicates.

    WHY THIS EXISTS: csv.DictReader is unsafe here. When a file contains two
    identical header names, DictReader keeps only ONE key and the last value
    wins, so the earlier column is dropped without any error. Reading the rows
    positionally against an explicit column map means that cannot happen: a
    collision is detected explicitly.

    A collision is FATAL (SPEC-001 §7.1, §10.5), not resolved silently. Once
    two source columns normalise to one bronze name, there is no mapping that
    is provably right: whichever copy is kept, the other copy's values land
    under a header that does not describe them. Rejecting the whole file is
    the only response that cannot quietly mislabel data. The near-miss pair
    'Lead times' / 'Lead time' is NOT affected: they normalise to different
    names and are resolved by SOURCE_HEADER_ALIASES before this point.
    """
    columns: list[str] = []
    seen: dict[str, int] = {}
    duplicates: list[str] = []

    for position, raw in enumerate(header_row):
        normalised = normalise_header(raw)
        target = SOURCE_HEADER_ALIASES.get(normalised, normalised)

        if not target:
            # A blank or punctuation-only header. Keep it addressable rather
            # than discarding the column, since discarding it would shift every
            # later column by one and silently misalign the row.
            target = f"unnamed_column_{position + 1}"
            logger.warning(
                "Source header at position %d is blank or non-alphanumeric (%r); "
                "mapped to %r. Verify the file is not truncated.",
                position + 1,
                raw,
                target,
            )

        if target in seen:
            # Genuine duplicate after normalisation and aliasing. Collect and
            # raise after the loop so one message reports every collision.
            duplicates.append(target)
        else:
            seen[target] = position

        columns.append(target)

    if duplicates:
        raise DuplicateHeaderError(sorted(set(duplicates)))

    return columns


class BatchExtractionResult(BaseModel):
    """Pydantic model representing the output of the CSV extraction process."""

    success: bool = Field(..., description="Indicates whether extraction and validation succeeded.")
    file_path: str = Field(..., description="Path to the ingested CSV file.")
    file_hash: str = Field(..., description="SHA-256 hash of the file contents for deduplication.")
    is_duplicate: bool = Field(
        False, description="True if the file hash already exists in the tracking store."
    )
    total_rows: int = Field(0, description="Number of valid data rows extracted.")
    headers: list[str] = Field(default_factory=list, description="Column headers found in the CSV.")
    rows: list[dict[str, Any]] = Field(
        default_factory=list, description="Extracted raw rows as dictionaries."
    )
    error_message: str | None = Field(
        None, description="Detailed error message if validation failed."
    )


class CSVExtractor:
    """Handles validation, encoding checks, hashing, and extraction of batch CSV files."""

    def __init__(
        self,
        required_headers: list[str] | None = None,
        existing_hashes: set | None = None,
    ):
        """
        Args:
            required_headers: Column names that must be present in the CSV. Given
                in EITHER source spelling ('Product Type') or bronze spelling
                ('product_type'); both are normalised before comparison, so the
                caller does not need to know which convention is in play.
                Defaults to DEFAULT_REQUIRED_HEADERS.
            existing_hashes: A set of known file hashes used to detect duplicates.
        """
        if required_headers is None:
            required_headers = DEFAULT_REQUIRED_HEADERS
        self.required_headers = [normalise_header(h) for h in required_headers]
        self.existing_hashes = existing_hashes if existing_hashes is not None else set()

    def _compute_file_hash(self, file_path: Path) -> str:
        """Computes the SHA-256 hash of the file contents."""
        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()

    def _detect_encoding_and_read(self, file_path: Path) -> str:
        """Attempts to read the file using standard encodings to avoid codec errors."""
        encodings = ["utf-8-sig", "utf-8", "latin-1", "cp1252"]
        for enc in encodings:
            try:
                with open(file_path, encoding=enc) as f:
                    return f.read()
            except UnicodeDecodeError:
                continue
        raise UnicodeDecodeError(
            "utf-8",
            b"",
            0,
            0,
            f"Failed to decode {file_path.name} using supported encodings ({', '.join(encodings)}).",
        )

    def extract(self, file_path: str | Path) -> BatchExtractionResult:
        """
        Validates and extracts data from a batch CSV file.

        Performs:
        1. File existence check
        2. Format check (.csv extension)
        3. Encoding validation & text reading
        4. File hash generation (SHA-256) for duplication check
        5. Required header validation
        """
        path = Path(file_path)

        # 1. Validate File Existence
        if not path.exists():
            return BatchExtractionResult(
                success=False,
                file_path=str(path),
                file_hash="",
                error_message=f"File not found: {path}",
            )

        # 2. Validate Format
        if path.suffix.lower() != ".csv":
            return BatchExtractionResult(
                success=False,
                file_path=str(path),
                file_hash="",
                error_message=f"Invalid file format '{path.suffix}'. Expected '.csv'.",
            )

        try:
            # 3. Compute Hash & Check Deduplication
            file_hash = self._compute_file_hash(path)
            is_dup = file_hash in self.existing_hashes

            # 4. Validate Encoding & Read Content
            file_content = self._detect_encoding_and_read(path)

            # 5. Parse CSV & Validate Headers
            lines = file_content.splitlines()
            if not lines:
                return BatchExtractionResult(
                    success=False,
                    file_path=str(path),
                    file_hash=file_hash,
                    is_duplicate=is_dup,
                    error_message="The CSV file is empty.",
                )

            reader = csv.reader(lines)

            # 5. Parse CSV & Validate Headers
            #    Read positionally, NOT with csv.DictReader. The source file
            #    contains two near-identical headers ('Lead times' and 'Lead
            #    time'), and DictReader silently collapses any repeated key,
            #    dropping a column with no error. A positional read against an
            #    explicit column map keeps every column. SPEC-001 Q3.
            try:
                header_row = next(reader)
            except StopIteration:
                return BatchExtractionResult(
                    success=False,
                    file_path=str(path),
                    file_hash=file_hash,
                    is_duplicate=is_dup,
                    error_message="No column headers found in the CSV.",
                )

            try:
                columns = build_column_map(header_row)
            except DuplicateHeaderError as dup_error:
                # A header collision means the file no longer matches the
                # source contract. Fatal, per SPEC-001 §7.1 / §10.5 -- the
                # whole file is rejected, not reshaped.
                logger.error(str(dup_error))
                return BatchExtractionResult(
                    success=False,
                    file_path=str(path),
                    file_hash=file_hash,
                    is_duplicate=is_dup,
                    headers=header_row,
                    error_message=str(dup_error),
                )

            # A header row with no usable column names is a corrupt file, not an
            # empty one. Catching it here avoids a confusing COPY failure later.
            if not columns or not any(c and not c.startswith("unnamed_column_") for c in columns):
                return BatchExtractionResult(
                    success=False,
                    file_path=str(path),
                    file_hash=file_hash,
                    is_duplicate=is_dup,
                    headers=header_row,
                    error_message="Header row contains no usable column names.",
                )

            # Check for missing required headers, against the normalised names
            missing_headers = [req for req in self.required_headers if req not in columns]

            if missing_headers:
                return BatchExtractionResult(
                    success=False,
                    file_path=str(path),
                    file_hash=file_hash,
                    is_duplicate=is_dup,
                    headers=header_row,
                    error_message=f"Missing required column headers: {missing_headers}",
                )

            # Extract rows, keyed by bronze column name.
            rows: list[dict[str, Any]] = []
            ragged_rows = 0

            for line_number, values in enumerate(reader, start=2):
                # csv.reader yields [] for a truly blank line. Skipping those is
                # correct; they are not data.
                if not values:
                    continue

                row: dict[str, Any] = {}

                if len(values) != len(columns):
                    # A ragged row must not be silently truncated or padded:
                    # either way the columns after the mismatch would be wrong.
                    # The count is reported on the result so the operator can
                    # decide, and the row is still loaded with what it has.
                    ragged_rows += 1
                    logger.warning(
                        "Line %d has %d fields but the header declares %d. "
                        "Loading positionally; check the file for embedded newlines "
                        "or unescaped delimiters.",
                        line_number,
                        len(values),
                        len(columns),
                    )

                for index, column in enumerate(columns):
                    row[column] = values[index] if index < len(values) else ""

                rows.append(row)

            if ragged_rows:
                logger.warning(
                    "%d of %d row(s) had a field count that did not match the header.",
                    ragged_rows,
                    len(rows),
                )

            return BatchExtractionResult(
                success=True,
                file_path=str(path),
                file_hash=file_hash,
                is_duplicate=is_dup,
                total_rows=len(rows),
                headers=columns,
                rows=rows,
            )

        except Exception as e:
            return BatchExtractionResult(
                success=False,
                file_path=str(path),
                file_hash="",
                error_message=f"An unexpected error occurred during extraction: {e!s}",
            )
