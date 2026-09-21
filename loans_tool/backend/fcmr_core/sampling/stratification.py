"""Exception severity stratification for deterministic sampling.

Groups records by exception severity (CRITICAL, HIGH, MEDIUM, LOW).
Used for proportional stratified sampling in audit workpapers.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

# Exception severity mapping
_SEVERITY_MAP = {
    # CRITICAL: UCID, PAN/Aadhaar duplicates (identity fraud risk)
    "UCID_KYC_INCONSISTENT": "CRITICAL",
    "PAN_DUPLICATE": "CRITICAL",
    "AADHAAR_DUPLICATE": "CRITICAL",
    # HIGH: Voter ID, Address, Bank Account duplicates (fraud indicators)
    "VOTER_ID_DUPLICATE": "HIGH",
    "ADDRESS_DUPLICATE": "HIGH",
    "BANK_ACCOUNT_DUPLICATE": "HIGH",
    "NAME_DOB_DUPLICATE": "HIGH",
    # MEDIUM: Email domain, age range, account length (data quality)
    "EMAIL_COMPANY_GENERIC_DOMAIN": "MEDIUM",
    "DOB_AGE_OUT_OF_RANGE": "MEDIUM",
    "BANK_ACCOUNT_INVALID_LENGTH": "MEDIUM",
    # LOW: PIN/address mismatches (lower audit impact)
    "PINCODE_MISMATCH": "LOW",
    "DISTRICT_PIN_MISMATCH": "LOW",
    "STATE_PIN_MISMATCH": "LOW",
    "ADDRESS_INCOMPLETE": "LOW",
}

_SEVERITY_ORDER = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]
_SEVERITY_WEIGHT = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}


def get_exception_severity(code: str) -> str:
    """Get severity level for an exception code."""
    return _SEVERITY_MAP.get(code, "LOW")


def stratify_by_exception_severity(wide_csv_path: Path) -> dict[str, list[int]]:
    """Group row indices by exception severity.

    Args:
        wide_csv_path: Path to wide exception CSV.

    Returns:
        {stratum: [row_indices]} where stratum is "CRITICAL", "HIGH", "MEDIUM", or "LOW".
        Also includes "OK" for rows with no exceptions.
    """
    if not wide_csv_path.exists():
        return {"OK": [], "CRITICAL": [], "HIGH": [], "MEDIUM": [], "LOW": []}

    try:
        df = pl.read_csv(
            wide_csv_path, columns=["exception_codes", "overall_status"], infer_schema_length=0
        )
    except Exception:
        return {"OK": [], "CRITICAL": [], "HIGH": [], "MEDIUM": [], "LOW": []}

    strata = {"OK": [], "CRITICAL": [], "HIGH": [], "MEDIUM": [], "LOW": []}

    for i, (codes_str, status) in enumerate(zip(df["exception_codes"], df["overall_status"])):
        if status == "OK" or not codes_str or str(codes_str).strip() == "":
            strata["OK"].append(i)
            continue

        # Parse pipe-delimited codes and find max severity
        codes = [c.strip() for c in str(codes_str).split("|") if c.strip()]
        max_severity = "LOW"
        for code in codes:
            severity = get_exception_severity(code)
            # Compare severity levels
            if _SEVERITY_ORDER.index(severity) < _SEVERITY_ORDER.index(max_severity):
                max_severity = severity

        strata[max_severity].append(i)

    return strata


def get_stratified_summary(wide_csv_path: Path) -> dict[str, dict]:
    """Get summary of strata: count and percentage per stratum.

    Returns:
        {stratum: {"count": int, "percentage": float}}
    """
    strata = stratify_by_exception_severity(wide_csv_path)
    total = sum(len(v) for v in strata.values())

    if total == 0:
        return {s: {"count": 0, "percentage": 0.0} for s in _SEVERITY_ORDER + ["OK"]}

    return {
        stratum: {
            "count": len(indices),
            "percentage": (len(indices) / total) * 100,
        }
        for stratum, indices in strata.items()
    }
