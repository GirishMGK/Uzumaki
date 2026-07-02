"""Load a Form 26AS file (real .xlsx or HTML) into a uniform grid of string cells.

TRACES lets you download Form 26AS as an "Excel" file, but that file is very
often actually an HTML document with an .xls/.xlsx extension. It can also be a
genuine .xlsx workbook, or a plain .html export. This loader detects which it is
and returns a single flat grid (list of rows, each row a list of stripped
strings). The parser scans that grid for the header rows it needs, so it does
not matter that different parts of the statement have different column counts.
"""

from __future__ import annotations

from pathlib import Path
from typing import List

Grid = List[List[str]]


def _looks_like_html(head: bytes) -> bool:
    lowered = head.lower()
    return b"<html" in lowered or b"<table" in lowered or b"<!doctype html" in lowered


def _load_xlsx(path: Path) -> Grid:
    from openpyxl import load_workbook

    wb = load_workbook(filename=str(path), read_only=True, data_only=True)
    grid: Grid = []
    for ws in wb.worksheets:
        for row in ws.iter_rows(values_only=True):
            grid.append(["" if c is None else str(c).strip() for c in row])
    wb.close()
    return grid


def _load_text(path: Path) -> Grid:
    """Split a caret (^) delimited TRACES text export into a grid.

    Falls back to tab or comma if no carets are present.
    """
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        lines = fh.read().splitlines()
    sample = "\n".join(lines[:200])
    if "^" in sample:
        delim = "^"
    elif "\t" in sample:
        delim = "\t"
    else:
        delim = ","
    return [[cell.strip() for cell in line.split(delim)] for line in lines if line.strip()]


def _load_html(path: Path) -> Grid:
    from bs4 import BeautifulSoup

    with open(path, "rb") as fh:
        raw = fh.read()
    soup = BeautifulSoup(raw, "lxml")
    grid: Grid = []
    for table in soup.find_all("table"):
        for tr in table.find_all("tr"):
            cells: List[str] = []
            for cell in tr.find_all(["td", "th"]):
                text = cell.get_text(separator=" ", strip=True)
                # Expand colspan so columns stay aligned across rows: the first
                # slot holds the text, the rest are blank placeholders.
                try:
                    span = int(cell.get("colspan", 1))
                except (TypeError, ValueError):
                    span = 1
                cells.append(text)
                cells.extend([""] * max(0, span - 1))
            if cells:
                grid.append(cells)
    return grid


def load_grid(path: str | Path) -> Grid:
    """Return the file's contents as a list of rows of stripped string cells."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"No such file: {path}")

    with open(path, "rb") as fh:
        head = fh.read(4096)

    suffix = path.suffix.lower()
    if suffix in {".txt", ".csv"}:
        return _load_text(path)

    # HTML masquerading as .xls/.xlsx is common from TRACES, so sniff content
    # first rather than trusting the extension.
    if _looks_like_html(head):
        return _load_html(path)

    # A caret-delimited text export sniffed by content (extension may vary).
    if b"^" in head and b"PK\x03\x04" not in head[:4]:
        return _load_text(path)

    if suffix in {".xlsx", ".xlsm", ".xls"}:
        try:
            return _load_xlsx(path)
        except Exception:
            # Genuine-looking extension but not a real workbook — fall back.
            return _load_html(path)
    if suffix in {".html", ".htm"}:
        return _load_html(path)

    # Unknown extension: try workbook, then HTML.
    try:
        return _load_xlsx(path)
    except Exception:
        return _load_html(path)
