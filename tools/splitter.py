"""PDF split / extract helpers (pypdf backend)."""

from __future__ import annotations

import io
import zipfile
from typing import Dict, List

from pypdf import PdfReader, PdfWriter


def _parse_range_string(spec: str, total: int) -> List[List[int]]:
    """
    Parse "1-3,5,8-10" into 0-indexed groups: [[0,1,2],[4],[7,8,9]].
    Raises ValueError on malformed / out-of-bounds input.
    """
    groups: List[List[int]] = []
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk:
            a, b = chunk.split("-", 1)
            start, end = int(a), int(b)
        else:
            start = end = int(chunk)
        if start < 1 or end > total or start > end:
            raise ValueError(f"Invalid range '{chunk}' for a {total}-page document.")
        groups.append(list(range(start - 1, end)))
    if not groups:
        raise ValueError("No valid ranges provided.")
    return groups


def _write_pages(reader: PdfReader, indices: List[int]) -> bytes:
    writer = PdfWriter()
    for i in indices:
        writer.add_page(reader.pages[i])
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def split_by_ranges(pdf_bytes: bytes, range_str: str) -> Dict[str, bytes]:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    groups = _parse_range_string(range_str, len(reader.pages))
    result: Dict[str, bytes] = {}
    for idx, grp in enumerate(groups, 1):
        result[f"range_{idx}_pages_{grp[0]+1}-{grp[-1]+1}.pdf"] = _write_pages(reader, grp)
    return result


def split_every_n_pages(pdf_bytes: bytes, n: int) -> Dict[str, bytes]:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    total = len(reader.pages)
    n = max(1, int(n))
    result: Dict[str, bytes] = {}
    for part, start in enumerate(range(0, total, n), 1):
        idx = list(range(start, min(start + n, total)))
        result[f"part_{part:03d}_pages_{idx[0]+1}-{idx[-1]+1}.pdf"] = _write_pages(reader, idx)
    return result


def extract_pages(pdf_bytes: bytes, page_str: str) -> bytes:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    groups = _parse_range_string(page_str, len(reader.pages))
    flat: List[int] = []
    for g in groups:
        flat.extend(g)
    return _write_pages(reader, flat)


def split_all_pages(pdf_bytes: bytes) -> Dict[str, bytes]:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    return {f"page_{i+1:03d}.pdf": _write_pages(reader, [i])
            for i in range(len(reader.pages))}


def pack_zip(parts: Dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, blob in parts.items():
            zf.writestr(name, blob)
    return buf.getvalue()
