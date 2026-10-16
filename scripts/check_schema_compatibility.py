"""Schema-compatibility gate: loader columns vs bronze DDL (CI quality gate).

Fails (exit 1) when:
  1. a column the loader writes (load.BRONZE_SOURCE_COLUMNS) has no matching
     column in sql/bootstrap/003_create_bronze_supply_chain_raw.sql, or
  2. the DDL declares a source (non-provenance) column the loader never writes.

Provenance columns (source_batch_id, source_identity, file_hash, row_number)
are written by the loader alongside every row but are not source columns, so
they are expected in the DDL and excluded from the "DDL has extra" check.

Usage: python scripts/check_schema_compatibility.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DDL = REPO_ROOT / "sql" / "bootstrap" / "003_create_bronze_supply_chain_raw.sql"

# DDL bookkeeping columns that are not source data.
PROVENANCE_COLUMNS = {"source_batch_id", "source_identity", "file_hash", "row_number"}
DDL_META_COLUMNS = {"bronze_row_id", "loaded_at"} | PROVENANCE_COLUMNS


def loader_columns() -> list[str]:
    sys.path.insert(0, str(REPO_ROOT / "src"))
    from supply_chain_pipeline.load import BRONZE_SOURCE_COLUMNS

    return list(BRONZE_SOURCE_COLUMNS)


def ddl_source_columns() -> list[str]:
    text = DDL.read_text(encoding="utf-8")
    # Column definitions inside the CREATE TABLE (...) block look like
    # "    sku   text," -- capture the leading identifier of each line that
    # ends with a comma and whose type token is a known DDL type.
    cols: list[str] = []
    in_table = False
    for line in text.splitlines():
        if "CREATE TABLE" in line:
            in_table = True
            continue
        if in_table and line.strip().startswith(");"):
            break
        if not in_table:
            continue
        match = re.match(r"\s*([a-z][a-z0-9_]*)\s+(text|bigint|integer|timestamptz)\b", line, re.I)
        if match:
            cols.append(match.group(1).lower())
    return [c for c in cols if c not in DDL_META_COLUMNS]


def main() -> int:
    if not DDL.exists():
        print(f"FAIL: DDL file missing: {DDL}")
        return 1
    loader = loader_columns()
    ddl = ddl_source_columns()
    loader_only = [c for c in loader if c not in ddl]
    ddl_only = [c for c in ddl if c not in loader]
    if loader_only:
        print("FAIL: loader writes columns missing from bronze DDL:")
        for col in loader_only:
            print(f"  - {col}")
    if ddl_only:
        print("FAIL: bronze DDL declares source columns the loader never writes:")
        for col in ddl_only:
            print(f"  - {col}")
    if loader_only or ddl_only:
        print(f"(loader: {len(loader)} cols, DDL: {len(ddl)} source cols)")
        return 1
    print(f"OK: loader and bronze DDL agree on {len(loader)} source columns.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
