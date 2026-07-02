"""Parse one or more Form 26AS files and combine them into one transaction list.

Meant for the common "5-10 years of 26AS" case: TRACES only ever gives you one
assessment year per download, so consolidating years means running the parser
once per file and stacking the results, tagging each row with the year it came
from. A failure on one file (e.g. a wrong PDF password) does not abort the
rest of the batch.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Tuple, TypedDict

from .loader import load_grid
from .parser import Transaction, detect_assessment_year, parse_part_a, year_from_filename


class FileResult(TypedDict):
    path: Path
    count: int
    year: Optional[str]
    error: Optional[str]


def parse_one(path: Path, password: Optional[str] = None) -> Tuple[List[Transaction], FileResult]:
    """Parse a single file. Raises on load failure (e.g. wrong PDF password)."""
    grid = load_grid(path, password=password)
    year = detect_assessment_year(grid) or year_from_filename(path)
    txns = parse_part_a(grid)
    for t in txns:
        t.assessment_year = year
        t.source_file = path.name
    return txns, FileResult(path=path, count=len(txns), year=year, error=None)


def parse_files(
    paths: List[Path],
    passwords: Optional[Dict[Path, str]] = None,
    debug: bool = False,
) -> Tuple[List[Transaction], List[FileResult]]:
    """Parse every path, continuing past per-file failures.

    Returns (all_transactions_combined, per_file_results). A file that fails to
    load appears in the results with count=0 and a non-None error instead of
    raising, so one bad file does not stop the rest of a multi-year batch.
    """
    passwords = passwords or {}
    all_txns: List[Transaction] = []
    results: List[FileResult] = []

    for path in paths:
        try:
            grid = load_grid(path, password=passwords.get(path))
        except Exception as exc:  # noqa: BLE001 - report per file, keep going
            results.append(FileResult(path=path, count=0, year=None, error=str(exc)))
            continue

        if debug:
            import sys

            print(f"--- {path} ({len(grid)} rows) ---", file=sys.stderr)
            for i, row in enumerate(grid[:20]):
                print(f"  [{i}] {row}", file=sys.stderr)

        year = detect_assessment_year(grid) or year_from_filename(path)
        txns = parse_part_a(grid)
        for t in txns:
            t.assessment_year = year
            t.source_file = path.name
        all_txns.extend(txns)
        results.append(FileResult(path=path, count=len(txns), year=year, error=None))

    return all_txns, results
