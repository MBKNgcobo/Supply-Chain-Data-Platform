"""Contract-parity gate: source-contract YAML vs implemented code (CI quality gate).

Fails (exit 1) when docs/contracts/sources/supply_chain_data-csv.contract.yaml
describes behaviour the code does not implement (or vice versa):

  1. Contract `row_checks_implemented` must all exist in validate.py as
     recognised row-check names. The one KNOWN exception is
     `defect_rates_in_range`, which the contract itself marks DORMANT
     ("requires bounds; SPEC-001 Q7 undecided") and validate.py implements
     behind `defect_rate_bounds=None`. That pair is the documented, allowed
     dormancy -- anything else listed-but-missing fails.
  2. Every contract column `name` must match a bronze column the loader writes
     (load.BRONZE_SOURCE_COLUMNS). A documented column the pipeline drops (or
     a pipeline column the contract never describes) is a docs/code mismatch.
  3. `max_reject_rate_percent` in the contract must equal
     validate.DEFAULT_MAX_REJECT_RATE_PERCENT.

Usage: python scripts/check_contract_parity.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACT = REPO_ROOT / "docs" / "contracts" / "sources" / "supply_chain_data-csv.contract.yaml"

# Row checks the contract may list as implemented while dormant-by-design.
ALLOWED_DORMANT = {"defect_rates_in_range"}
# Markers that must appear near the check's implementation in validate.py.
CHECK_EVIDENCE = {
    "sku_not_null": ["sku_not_null", "missing sku", "sku is required", "blank"],
    "sku_unique_in_file": ["seen_skus", "duplicate", "unique"],
    "numeric_columns_parse": ["NUMERIC_COLUMNS", "unparseable", "not a valid"],
    "defect_rates_in_range": ["defect_rate_bounds", "defect_rates"],
}


def _contract_text() -> str:
    if not CONTRACT.exists():
        print(f"FAIL: contract file missing: {CONTRACT}")
        raise SystemExit(1)
    return CONTRACT.read_text(encoding="utf-8")


def _implemented_checks(text: str) -> list[str]:
    match = re.search(r"row_checks_implemented:\s*\n((?:\s*-\s*.+\n)+)", text)
    if not match:
        print("FAIL: contract has no row_checks_implemented list.")
        raise SystemExit(1)
    return re.findall(r"-\s*([a-z_]+)", match.group(1))


def _contract_columns(text: str) -> list[str]:
    # Most columns use `- name:` directly as the bronze name. The two
    # colliding lead-time headers additionally declare `bronze_column:`
    # (lead_times_1 / lead_times_2, SPEC-001 Q3); where present the bronze
    # name replaces the normalised source name in the comparison.
    cdc_at = text.find("cdc:")
    before = text[:cdc_at] if cdc_at != -1 else text
    names = re.findall(r"-\s*name:\s*([a-z_][a-z0-9_]*)", before)
    bronze_map = dict(
        re.findall(
            r"-\s*name:\s*([a-z_][a-z0-9_]*)(?:(?!\n\s*-\s*name:).)*?bronze_column:\s*([a-z_][a-z0-9_]*)",
            before,
            re.S,
        )
    )
    return [bronze_map.get(name, name) for name in names]


def main() -> int:
    sys.path.insert(0, str(REPO_ROOT / "src"))
    import supply_chain_pipeline.load as load_mod
    import supply_chain_pipeline.validate as validate_mod

    failures: list[str] = []
    contract = _contract_text()
    validate_src = (
        (REPO_ROOT / "src" / "supply_chain_pipeline" / "validate.py")
        .read_text(encoding="utf-8")
        .lower()
    )

    # 1. Implemented checks must have code evidence.
    for check in _implemented_checks(contract):
        evidence = CHECK_EVIDENCE.get(check, [check])
        if not any(marker in validate_src for marker in evidence):
            if check in ALLOWED_DORMANT and "defect_rate_bounds" in validate_src:
                print(f"NOTE: '{check}' is dormant-by-design (bounds unset) -- allowed.")
                continue
            failures.append(
                f"contract lists '{check}' as implemented but validate.py shows no evidence"
            )

    # 2. Contract columns must equal loader bronze columns.
    contract_cols = set(_contract_columns(contract))
    loader_cols = set(load_mod.BRONZE_SOURCE_COLUMNS)
    for col in sorted(contract_cols - loader_cols):
        failures.append(f"contract documents column '{col}' the loader never writes")
    for col in sorted(loader_cols - contract_cols):
        failures.append(f"loader writes bronze column '{col}' the contract never documents")

    # 3. Reject-rate threshold must agree.
    match = re.search(r"max_reject_rate_percent:\s*([0-9.]+)", contract)
    if not match:
        failures.append("contract has no max_reject_rate_percent")
    elif float(match.group(1)) != validate_mod.DEFAULT_MAX_REJECT_RATE_PERCENT:
        failures.append(
            f"contract max_reject_rate_percent={match.group(1)} != "
            f"code {validate_mod.DEFAULT_MAX_REJECT_RATE_PERCENT}"
        )

    if failures:
        print("FAIL: contract/code parity broken:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print(
        f"OK: contract parity holds "
        f"({len(contract_cols)} columns, "
        f"{len(_implemented_checks(contract))} implemented checks, "
        f"reject-rate {validate_mod.DEFAULT_MAX_REJECT_RATE_PERCENT}%)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
