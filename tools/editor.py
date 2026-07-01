"""PDF edit helpers: remove / insert / reorder pages (pypdf backend)."""

from __future__ import annotations

import io
from typing import List

from pypdf import PdfReader, PdfWriter


def remove_pages(pdf_bytes: bytes, remove_indices: List[int]) -> bytes:
    """Return a PDF with the given 0-indexed pages removed."""
    reader = PdfReader(io.BytesIO(pdf_bytes))
    drop = set(remove_indices)
    writer = PdfWriter()
    for i, page in enumerate(reader.pages):
        if i not in drop:
            writer.add_page(page)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def insert_pdf(base_bytes: bytes, insert_bytes: bytes, position: int) -> bytes:
    """
    Insert every page of `insert_bytes` into `base_bytes` at `position`
    (0-indexed; position == page count appends at the end).
    """
    base = PdfReader(io.BytesIO(base_bytes))
    ins = PdfReader(io.BytesIO(insert_bytes))
    writer = PdfWriter()
    pos = max(0, min(int(position), len(base.pages)))
    for i in range(pos):
        writer.add_page(base.pages[i])
    for page in ins.pages:
        writer.add_page(page)
    for i in range(pos, len(base.pages)):
        writer.add_page(base.pages[i])
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def reorder_pages(pdf_bytes: bytes, new_order: List[int]) -> bytes:
    """Rebuild the PDF using `new_order` (0-indexed permutation)."""
    reader = PdfReader(io.BytesIO(pdf_bytes))
    writer = PdfWriter()
    for i in new_order:
        writer.add_page(reader.pages[i])
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()
