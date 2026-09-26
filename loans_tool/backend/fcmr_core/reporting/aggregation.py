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
        # polars' value_counts().to_dicts() names the count column "count" --
        # this used to read "counts" (plural), which KeyErrors on every call
        # and was silently swallowed by the except below, so this always
        # returned all-zero counts regardless of the real data (the wide CSV
        # itself, and aggregate_exception_codes below, were both fine).
        result = {row["overall_status"]: row["count"] for row in counts}
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
        # Fully vectorized in Polars (Rust): split the pipe-joined codes per
        # row, explode to one row per code, then value_counts(). The
        # previous version did this same split/strip/count in a pure-Python
        # loop over every row -- fine at a few thousand rows, but a real
        # cost on a large batch (this runs on every EAD Analytics run view
        # and download), same class of fix as build_exception_csvs' own
        # vectorization above. (A group_by(..., maintain_order=True) here
        # measured *slower* than the old Python loop at 2M rows --
        # maintain_order forces a non-parallel path; value_counts() doesn't
        # need it, since which of several equal-count codes sorts first is
        # a cosmetic chart-ordering detail, not something callers rely on.)
        df = pl.read_csv(wide_csv_path, columns=["exception_codes"], infer_schema_length=0)
        codes = (
            df.get_column("exception_codes")
            .fill_null("")
            .str.split("|")
            .explode(empty_as_null=False)
            .str.strip_chars()
        )
        codes = codes.filter(codes != "")
        counts = codes.value_counts(sort=True)
        limit = top_n if top_n is not None else counts.height
        return {row["exception_codes"]: row["count"] for row in counts.head(limit).to_dicts()}
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
