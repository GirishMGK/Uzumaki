"""PDF -> Word conversion (text-extraction based)."""

from __future__ import annotations

import io

from docx import Document
from pypdf import PdfReader


def pdf_to_word(pdf_bytes: bytes) -> bytes:
    """
    Extract text from each PDF page into a .docx, one page per section.
    Text-based conversion — scanned/image PDFs yield little/no text.
    """
    reader = PdfReader(io.BytesIO(pdf_bytes))
    doc = Document()
    for i, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        for line in text.splitlines():
            doc.add_paragraph(line)
        if i < len(reader.pages) - 1:
            doc.add_page_break()
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()
