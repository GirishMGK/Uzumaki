"""PDF edit helpers: remove / insert / reorder pages (pypdf backend), plus
find & replace text (PyMuPDF backend)."""

from __future__ import annotations

import io
import re
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


def replace_text(
    pdf_bytes: bytes,
    find: str,
    replacement: str,
    case_sensitive: bool = True,
    whole_word: bool = False,
) -> tuple[bytes, int]:
    """
    Find every occurrence of `find` and replace it with `replacement`,
    returning (new_pdf_bytes, occurrences_replaced).

    Uses PyMuPDF's native redaction annotations -- the same technique
    redaction_tool/redactors/pdf_redactor.py already uses (page.search_for()
    to locate every matching string's bounding box, then
    add_redact_annot(rect, text=...) + apply_redactions() to genuinely strip
    the old text and draw the new text in its place, not just overlay it).

    This is a real substitution, not a cosmetic overlay, but it is not a
    true "edit the text run in place" operation -- no PDF library does that
    cleanly, since PDF stores glyph-positioned runs, not reflowable text.
    Font/size/style of the replacement is approximated (matched to the
    original span's height), so it can look slightly different from the
    surrounding text, especially for bold/italic runs or unusual fonts.
    """
    if not find:
        raise ValueError("Enter text to find.")

    import fitz  # PyMuPDF

    # search_for() locates occurrences of `find` case-INsensitively no
    # matter what case is passed in (confirmed directly against this
    # PyMuPDF version: searching "Hello" returns quads for "Hello", "hello",
    # AND "HELLO" alike) -- there is no flag to change that. So every quad
    # it returns is verified against the literal text PyMuPDF itself
    # extracts from that exact bounding box (get_textbox()), which *does*
    # preserve real case, before it's accepted -- that's what actually
    # enforces case_sensitive here, not search_for's own matching.
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    total = 0
    try:
        for page in doc:
            quads = page.search_for(find, quads=True)
            if not quads:
                continue

            accepted = []
            for q in quads:
                rect = q.rect
                actual = page.get_textbox(rect).strip()
                if case_sensitive:
                    if actual != find:
                        continue
                else:
                    if actual.lower() != find.lower():
                        continue
                if whole_word:
                    # Approximate word-boundary check: look at a slightly
                    # padded rect and require nothing alphanumeric butts up
                    # directly against the match on either side.
                    pad = max(2.0, rect.height * 0.3)
                    padded = fitz.Rect(rect.x0 - pad, rect.y0, rect.x1 + pad, rect.y1)
                    context = page.get_textbox(padded)
                    inner = re.escape(actual)
                    if not re.search(r"(?<![A-Za-z0-9])" + inner + r"(?![A-Za-z0-9])", context):
                        continue
                accepted.append(rect)

            if not accepted:
                continue

            for rect in accepted:
                fontsize = max(6, min(24, rect.height * 0.75))
                page.add_redact_annot(
                    rect, text=replacement, fill=(1, 1, 1),
                    text_color=(0, 0, 0), fontsize=fontsize,
                )
            total += len(accepted)
            page.apply_redactions()

        out = io.BytesIO()
        doc.save(out)
        return out.getvalue(), total
    finally:
        doc.close()
