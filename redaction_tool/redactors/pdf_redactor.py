"""
PDF Redactor — uses PyMuPDF (fitz) native redaction annotations.

Strategy: extract each page's text, run the regex patterns against it,
locate every matched string's bounding quads with page.search_for(), then
apply a true PDF redaction (strips the underlying text, not just an overlay)
via add_redact_annot() / apply_redactions().
"""

import re
from pathlib import Path


def redact_pdf(
    input_path: str,
    output_path: str,
    patterns: list,
    custom_terms: list,
    replacement: str = "[REDACTED]",
    case_sensitive: bool = False,
) -> tuple:
    """Returns (total_count, audit_entries)."""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        raise ImportError(
            "PyMuPDF is required for PDF redaction.\n"
            "Install with:  pip install PyMuPDF"
        )

    flags = 0 if case_sensitive else re.IGNORECASE

    compiled = []
    for regex, label in patterns:
        try:
            compiled.append((re.compile(regex, flags), label))
        except re.error:
            pass
    for kw in custom_terms:
        kw = kw.strip()
        if kw:
            try:
                compiled.append((re.compile(re.escape(kw), flags), "Custom Keyword"))
            except re.error:
                pass

    doc = fitz.open(input_path)
    total = 0
    audit = []
    file_name = Path(input_path).name

    for page_num, page in enumerate(doc, start=1):
        text = page.get_text("text")
        seen_on_page = set()  # avoid re-searching the same string twice on a page

        for pat, label in compiled:
            for m in pat.finditer(text):
                matched = m.group()
                if not matched or matched in seen_on_page:
                    continue
                seen_on_page.add(matched)

                quads = page.search_for(matched, quads=True)
                if not quads:
                    continue
                for q in quads:
                    page.add_redact_annot(
                        q, text=replacement, fill=(1, 1, 1),
                        text_color=(0.3, 0.3, 0.3), fontsize=8,
                    )
                total += len(quads)
                audit.append({
                    "File": file_name,
                    "Page": f"Page {page_num}",
                    "Category": label,
                    "Original Term": (matched[:45] + "…") if len(matched) > 45 else matched,
                    "Count": len(quads),
                })

        if seen_on_page:
            page.apply_redactions()

    doc.save(output_path)
    doc.close()
    return total, audit
