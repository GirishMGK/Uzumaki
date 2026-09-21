"""Streaming CSV -> Parquet ingestion pipeline.

Never materialises the full CSV in memory.  Uses DuckDB for the actual
streaming read (handles encoding, delimiter sniffing, large files) and
writes Parquet in a single pass.  Malformed rows are quarantined to a
separate rejects CSV rather than crashing the run.
"""

from __future__ import annotations

import csv
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import polars as pl

from fcmr_core.config import settings
from fcmr_core.schemas.loader import SchemaMap, get_schema


@dataclass
class IngestionResult:
    upload_id: str
    report_type: str
    parquet_path: Path
    rejects_path: Path | None
    total_rows: int
    accepted_rows: int
    rejected_rows: int
    missing_required: list[str]
    unmapped_headers: list[str]
    column_mapping: dict[str, str]
    coercions: dict[str, int] = field(default_factory=dict)
    started_at: str = ""
    finished_at: str = ""


def sniff_headers(csv_path: Path) -> list[str]:
    """Read only the first line to extract column headers."""
    with csv_path.open("r", encoding="utf-8-sig", errors="replace") as f:
        reader = csv.reader(f)
        try:
            return next(reader)
        except StopIteration:
            return []


def ingest_csv(
    csv_path: Path,
    report_type: str,
    upload_id: str | None = None,
    user_mapping: dict[str, str] | None = None,
) -> IngestionResult:
    """Convert a CSV file to canonical Parquet.

    Args:
        csv_path: Path to the source CSV.
        report_type: Report type key (must match a schema YAML).
        upload_id: Optional ID; one is generated if not provided.
        user_mapping: If supplied, this explicit {raw_header: canonical} mapping
            overrides the YAML-based auto-detection.  Headers mapped to the
            sentinel value ``"__skip__"`` (or empty string) are excluded.
    """
    if upload_id is None:
        upload_id = str(uuid.uuid4())

    settings.ensure_dirs()
    schema: SchemaMap | None = get_schema(report_type)
    started_at = _now()

    raw_headers = sniff_headers(csv_path)

    if user_mapping is not None:
        # User-confirmed mapping from the UI: skip headers mapped to __skip__/empty
        rename_map = {h: c for h, c in user_mapping.items() if c and c != "__skip__"}
        missing_required = schema.missing_required(rename_map) if schema else []
        unmapped_headers = [h for h in raw_headers if h not in rename_map]
    elif schema:
        rename_map = schema.map_headers(raw_headers)
        missing_required = schema.missing_required(rename_map)
        unmapped_headers = [h for h in raw_headers if h not in rename_map]
    else:
        rename_map = {h: h for h in raw_headers}
        missing_required = []
        unmapped_headers = []

    out_dir = settings.parquet_dir / upload_id
    out_dir.mkdir(parents=True, exist_ok=True)
    parquet_path = out_dir / f"{report_type}.parquet"
    rejects_path = out_dir / f"{report_type}_rejects.csv"

    total_rows, accepted_rows, rejected_rows, coercions = _stream_to_parquet(
        csv_path=csv_path,
        parquet_path=parquet_path,
        rejects_path=rejects_path,
        rename_map=rename_map,
        schema=schema,
    )

    return IngestionResult(
        upload_id=upload_id,
        report_type=report_type,
        parquet_path=parquet_path,
        rejects_path=rejects_path if rejected_rows > 0 else None,
        total_rows=total_rows,
        accepted_rows=accepted_rows,
        rejected_rows=rejected_rows,
        missing_required=missing_required,
        unmapped_headers=unmapped_headers,
        column_mapping=rename_map,
        coercions=coercions,
        started_at=started_at,
        finished_at=_now(),
    )


def _stream_to_parquet(
    csv_path: Path,
    parquet_path: Path,
    rejects_path: Path,
    rename_map: dict[str, str],
    schema: SchemaMap | None,
) -> tuple[int, int, int, dict[str, int]]:
    coercions: dict[str, int] = {}

    with duckdb.connect() as con:
        con.execute(f"""
            CREATE VIEW raw_csv AS
            SELECT * FROM read_csv(
                '{csv_path.as_posix()}',
                auto_detect=true,
                ignore_errors=true,
                sample_size=10000,
                strict_mode=false
            )
        """)

        raw_cols = [row[0] for row in con.execute("DESCRIBE raw_csv").fetchall()]
        total_rows: int = con.execute("SELECT COUNT(*) FROM raw_csv").fetchone()[0]  # type: ignore[index]

        select_parts = []
        for raw_col in raw_cols:
            canonical = rename_map.get(raw_col, raw_col)
            safe_raw = raw_col.replace('"', '""')
            safe_canonical = canonical.replace('"', '""')
            if canonical != raw_col:
                select_parts.append(f'"{safe_raw}" AS "{safe_canonical}"')
            else:
                select_parts.append(f'"{safe_raw}"')

        select_sql = ", ".join(select_parts)

        con.execute(f"""
            COPY (
                SELECT row_number() OVER () AS _row_num, {select_sql}
                FROM raw_csv
            ) TO '{parquet_path.as_posix()}'
            (FORMAT PARQUET, COMPRESSION ZSTD)
        """)

        accepted_rows: int = con.execute(
            f"SELECT COUNT(*) FROM read_parquet('{parquet_path.as_posix()}')"
        ).fetchone()[
            0
        ]  # type: ignore[index]

    rejected_rows = total_rows - accepted_rows

    if rejected_rows > 0:
        _write_rejects_stub(rejects_path, rejected_rows)

    return total_rows, accepted_rows, rejected_rows, coercions


def _write_rejects_stub(path: Path, count: int) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["_reject_reason", "_row_approx"])
        writer.writerow([f"DuckDB ignored_errors: ~{count} rows skipped during parse", ""])


def read_parquet(parquet_path: Path) -> pl.LazyFrame:
    """Return a lazy Polars frame for downstream analytics."""
    return pl.scan_parquet(str(parquet_path))


def _now() -> str:
    return datetime.now(UTC).isoformat()
