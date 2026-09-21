"""Analytics aggregation: status counts, exception code frequencies.

Reads wide CSV outputs and aggregates exception data for dashboard display.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl


def aggregate_status_counts(wide_csv_path: Path) -> dict[str, int]:
    """Count rows per overall_status: OK, WARN, ERROR.

    Args:
        wide_csv_path: Path to the wide exception CSV (one row per input record).

    Returns:
        {status: count} e.g. {"OK": 1200, "WARN": 340, "ERROR": 18}
    """
    if not wide_csv_path.exists():
        return {"OK": 0, "WARN": 0, "ERROR": 0}

    try:
        df = pl.read_csv(wide_csv_path, columns=["overall_status"], infer_schema_length=0)
        counts = df["overall_status"].value_counts(sort=True).to_dicts()
        result = {row["overall_status"]: row["counts"] for row in counts}
        # Ensure all statuses are present
        return {
            "OK": result.get("OK", 0),
            "WARN": result.get("WARN", 0),
            "ERROR": result.get("ERROR", 0),
        }
    except Exception:
        return {"OK": 0, "WARN": 0, "ERROR": 0}


def aggregate_exception_codes(wide_csv_path: Path, top_n: int | None = 10) -> dict[str, int]:
    """Count top N exception codes from exception_codes column (pipe-delimited).

    Args:
        wide_csv_path: Path to the wide exception CSV.
        top_n: Number of top codes to return. None = all codes.

    Returns:
        {exception_code: count} sorted by frequency descending.
    """
    if not wide_csv_path.exists():
        return {}

    try:
        df = pl.read_csv(wide_csv_path, columns=["exception_codes"], infer_schema_length=0)
        # Parse pipe-delimited codes
        all_codes = []
        for codes_str in df["exception_codes"]:
            if codes_str and str(codes_str).strip():
                codes = [c.strip() for c in str(codes_str).split("|") if c.strip()]
                all_codes.extend(codes)

        # Count and sort
        code_counts = {}
        for code in all_codes:
            code_counts[code] = code_counts.get(code, 0) + 1

        # Return top N (or all if top_n is None)
        sorted_codes = sorted(code_counts.items(), key=lambda x: x[1], reverse=True)
        limit = top_n if top_n is not None else len(sorted_codes)
        return {code: count for code, count in sorted_codes[:limit]}
    except Exception:
        return {}


def get_summary(wide_csv_path: Path) -> dict:
    """Get a complete summary: status counts, top exceptions, total rows.

    Args:
        wide_csv_path: Path to the wide exception CSV.

    Returns:
        {
            "total_rows": int,
            "status_counts": {status: count},
            "exception_codes": {code: count},
        }
    """
    status_counts = aggregate_status_counts(wide_csv_path)
    exception_codes = aggregate_exception_codes(wide_csv_path, top_n=10)
    total_rows = sum(status_counts.values())

    return {
        "total_rows": total_rows,
        "status_counts": status_counts,
        "exception_codes": exception_codes,
    }
