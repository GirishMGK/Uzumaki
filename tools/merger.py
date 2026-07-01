"""PDF merge helpers (pypdf backend)."""

from __future__ import annotations

import io
from typing import List

from pypdf import PdfReader, PdfWriter


def get_page_count(pdf_bytes: bytes) -> int:
    return len(PdfReader(io.BytesIO(pdf_bytes)).pages)


def merge_pdfs(files: List[bytes]) -> bytes:
    """Concatenate a list of PDF byte blobs into one."""
    writer = PdfWriter()
    for blob in files:
        reader = PdfReader(io.BytesIO(blob))
        for page in reader.pages:
            writer.add_page(page)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()
