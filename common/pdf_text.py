"""Shared PDF-to-text extraction (PyMuPDF/fitz), used by every regex-based
document extractor in common/ (lease_extractor.py, agreement_extractor.py)
so the same read_pdf_text() isn't duplicated in each one.
"""

from __future__ import annotations

import fitz


def read_pdf_text(file_bytes: bytes) -> str:
    pdf = fitz.open(stream=file_bytes, filetype="pdf")
    return "\n".join(page.get_text(sort=True) for page in pdf)
