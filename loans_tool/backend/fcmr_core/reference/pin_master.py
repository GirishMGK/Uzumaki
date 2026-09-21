"""India Post PIN-code master.

The authoritative data is bundled as ``pin_master.parquet`` alongside this
module.  The master is keyed on ``pincode`` (6-digit string) and carries
``district``, ``state_name``, and ``circle_name`` columns.

If the parquet file is absent (first run or development), ``build_master()``
can regenerate it from the bundled raw CSV (``pin_master_raw.csv``).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import duckdb
import polars as pl

_HERE = Path(__file__).parent
_PARQUET = _HERE / "pin_master.parquet"
_RAW_CSV = _HERE / "pin_master_raw.csv"


def ensure_master() -> Path:
    """Return the path to the Parquet master, building it from CSV if needed."""
    if not _PARQUET.exists():
        if not _RAW_CSV.exists():
            raise FileNotFoundError(
                f"PIN master CSV not found at {_RAW_CSV}. "
                "Place the India Post Pincode Directory CSV there and run ensure_master()."
            )
        build_master()
    return _PARQUET


def build_master() -> None:
    """Build pin_master.parquet from the raw India Post CSV."""
    with duckdb.connect() as con:
        con.execute(f"""
            COPY (
                SELECT
                    CAST(Pincode AS VARCHAR) AS pincode,
                    LOWER(TRIM("District"))  AS district,
                    LOWER(TRIM("StateName")) AS state_name,
                    LOWER(TRIM("CircleName")) AS circle_name,
                    LOWER(TRIM("OfficeName")) AS office_name
                FROM read_csv(
                    '{_RAW_CSV.as_posix()}',
                    auto_detect=true,
                    ignore_errors=true
                )
                WHERE Pincode IS NOT NULL
                  AND LENGTH(CAST(Pincode AS VARCHAR)) = 6
            ) TO '{_PARQUET.as_posix()}'
            (FORMAT PARQUET, COMPRESSION ZSTD)
        """)


@lru_cache(maxsize=1)
def _load_master() -> pl.DataFrame:
    return pl.read_parquet(str(ensure_master()))


def lookup_pincode(pin: str) -> dict | None:
    """Return master row for a 6-digit PIN, or None if not found."""
    df = _load_master()
    rows = df.filter(pl.col("pincode") == pin)
    if rows.is_empty():
        return None
    # Return first row (a PIN may map to multiple post offices; first is sufficient for validation)
    return rows.row(0, named=True)


def is_valid_pin(pin: str) -> bool:
    return lookup_pincode(pin) is not None


def get_state_for_pin(pin: str) -> str | None:
    row = lookup_pincode(pin)
    return row["state_name"] if row else None


def get_district_for_pin(pin: str) -> str | None:
    row = lookup_pincode(pin)
    return row["district"] if row else None
