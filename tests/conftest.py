"""
Shared test infrastructure: a fake psycopg2-style connection and row builders.

No test in this suite talks to a real database. FakeConnection records every
SQL statement, COPY payload, commit, and rollback, so a test can assert
exactly what WOULD have been written -- which is the point of the suite: the
acceptance criterion is about what must NOT reach bronze when a data-quality
gate fires (docs/testing/unit-test-plan.md, ADR-005).
"""

from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"

# The 24 source headers, spelled the way the real file spells them (SPEC-001
# section 3.2 / docs/contracts/sources/supply_chain_data-csv.contract.yaml).
SOURCE_HEADERS: list[str] = [
    "Product Type",
    "SKU",
    "Price",
    "Availability",
    "Number of products sold",
    "Revenue generated",
    "Customer demographics",
    "Stock levels",
    "Lead times",
    "Order quantities",
    "Shipping times",
    "Shipping carriers",
    "Shipping costs",
    "Supplier name",
    "Location",
    "Lead time",
    "Production volumes",
    "Manufacturing lead time",
    "Manufacturing costs",
    "Inspection results",
    "Defect rates",
    "Transportation modes",
    "Routes",
    "Costs",
]


def build_row(sku: str = "SKU-001", **overrides: str) -> dict[str, str]:
    """A fully-valid row keyed by BRONZE column names (post-normalisation).

    Every value parses as the type stg_products will cast it to. Override any
    single column with build_row(price="twelve") to target one check without
    disturbing the rest.
    """
    row: dict[str, str] = {
        "product_type": "Electronics",
        "sku": sku,
        "price": "29.99",
        "availability": "150",
        "number_of_products_sold": "1200",
        "revenue_generated": "35980.50",
        "customer_demographics": "Male",
        "stock_levels": "80",
        "lead_times_1": "14",
        "order_quantities": "200",
        "shipping_times": "5",
        "shipping_carriers": "FedEx",
        "shipping_costs": "12.75",
        "supplier_name": "Acme Supplies",
        "location": "Chicago",
        "lead_times_2": "10",
        "production_volumes": "500",
        "manufacturing_lead_time": "7",
        "manufacturing_costs": "1450.00",
        "inspection_results": "Pass",
        "defect_rates": "2.5",
        "transportation_modes": "Road",
        "routes": "Route-A",
        "costs": "3200.00",
    }
    row.update(overrides)
    return row


class FakeCursor:
    """Records executes and COPY payloads; defers fetchone to the connection."""

    def __init__(self, connection: "FakeConnection"):
        self.connection = connection

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def execute(self, sql: str, params: Any = None) -> None:
        if (
            self.connection.fail_execute_containing
            and self.connection.fail_execute_containing in sql
        ):
            raise RuntimeError(
                f"forced failure on SQL containing {self.connection.fail_execute_containing!r}"
            )
        self.connection.executed.append((sql, params))

    def fetchone(self) -> Any:
        return self.connection.next_fetchone()

    def copy_expert(self, sql: str, buffer: Any) -> None:
        if self.connection.fail_copy:
            raise RuntimeError("forced COPY failure")
        self.connection.copy_calls.append((sql, buffer.read()))


class FakeConnection:
    """A psycopg2 connection stand-in that records everything written."""

    def __init__(self, fetchone_results: list[Any] | None = None):
        self.executed: list[tuple[str, Any]] = []
        self.copy_calls: list[tuple[str, str]] = []
        self.commits = 0
        self.rollbacks = 0
        # When set, any execute() whose SQL contains this substring raises.
        self.fail_execute_containing: str | None = None
        # When set, any COPY raises.
        self.fail_copy = False
        self._fetchone_results: list[Any] = list(fetchone_results or [])

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        pass

    def next_fetchone(self) -> Any:
        if self._fetchone_results:
            return self._fetchone_results.pop(0)
        return None

    def find_executed(self, substring: str) -> list[tuple[str, Any]]:
        """Every recorded statement whose SQL contains `substring`."""
        return [(sql, params) for sql, params in self.executed if substring in sql]

    def find_params(self, substring: str) -> list[Any]:
        """Every recorded params payload whose string form contains `substring`."""
        return [params for _, params in self.executed if substring in str(params)]


@pytest.fixture
def fake_db() -> FakeConnection:
    return FakeConnection()


def make_extraction(
    rows: list[dict[str, Any]] | None = None,
    success: bool = True,
    file_hash: str = "f" * 64,
    file_path: str = "fixture.csv",
    error_message: str | None = None,
):
    """A BatchExtractionResult as CSVExtractor.extract would return it."""
    from supply_chain_pipeline.extract import BatchExtractionResult

    rows = rows if rows is not None else []
    return BatchExtractionResult(
        success=success,
        file_path=file_path,
        file_hash=file_hash,
        total_rows=len(rows),
        headers=list(rows[0].keys()) if rows else [],
        rows=rows,
        error_message=error_message,
    )


def make_row_reject(
    row_number: int,
    raw_data: dict[str, Any] | None = None,
    error_message: str = "test reject",
    file_hash: str = "f" * 64,
):
    from supply_chain_pipeline.validate import RowReject

    return RowReject(
        file_hash=file_hash,
        row_number=row_number,
        raw_data=raw_data if raw_data is not None else build_row(),
        error_message=error_message,
    )


def make_validation_result(
    valid_rows: list[dict[str, Any]] | None = None,
    rejected: list[Any] | None = None,
    file_hash: str = "f" * 64,
    batch_id: str = "batch-test",
    status: str = "VALIDATED",
):
    """A BatchValidationResult as BatchValidator.validate_batch returns it."""
    from supply_chain_pipeline.validate import BatchValidationResult, ValidatedRecord

    valid_rows = valid_rows if valid_rows is not None else [build_row()]
    rejected = rejected if rejected is not None else []
    valid_records = [
        ValidatedRecord(
            source_identity=f"{file_hash}-{idx}",
            file_hash=file_hash,
            row_number=idx,
            data=row,
        )
        for idx, row in enumerate(valid_rows, start=1)
    ]
    return BatchValidationResult(
        success=status in ("VALIDATED", "DUPLICATE"),
        batch_id=batch_id,
        pipeline_name="test_pipeline",
        source_file="fixture.csv",
        source_hash=file_hash,
        started_at=datetime.utcnow(),
        status=status,
        rows_read=len(valid_rows) + len(rejected),
        rows_loaded=len(valid_rows),
        rows_rejected=len(rejected),
        valid_records=valid_records,
        rejected_records=rejected,
    )
