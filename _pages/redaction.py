"""Hub page: Secure Document Redaction Tool (tkinter desktop launcher)."""
import streamlit as st

st.title("Secure Document Redaction Tool")
st.caption("Auto-detect and redact PAN, TAN, GSTIN, CIN, Aadhaar, Phone, Email — PDF, DOCX, XLSX, images")

st.info(
    "This tool ships as a **desktop GUI app** (tkinter), which runs outside the "
    "Streamlit hub and needs a display on the machine that runs it.",
    icon="ℹ️",
)
st.markdown(
    """
**Launch it separately:**
```bash
pip install -r requirements.txt
python redaction_tool/main.py
```

**What it does**
- **Auto-detect patterns** — PAN, TAN, GSTIN, CIN, Aadhaar, Phone, Email (India-specific regex)
- **Custom keywords** — single entries or bulk import (comma/newline separated)
- **Profiles** — save/load reusable rule sets (patterns + keywords + replacement text)
- **Supported formats** — PDF (true redaction via PyMuPDF), DOCX, XLSX, JPG/PNG/TIFF/BMP (OCR via Tesseract)
- **Audit log** — per-file redaction counts by category, exportable to Excel/CSV
- Output is written to a `_redacted/` subfolder beside each source file (or a chosen output folder)

**Package layout**
```
redaction_tool/
  main.py                      # tkinter GUI entry point
  core/
    patterns.py                # PATTERN_GROUPS + get_active_patterns()
    profile_manager.py         # ProfileManager (save/load/list/delete)
    engine.py                  # RedactionEngine — dispatches by file extension
  redactors/
    word_redactor.py           # .docx (python-docx)
    excel_redactor.py          # .xlsx (openpyxl)
    pdf_redactor.py            # .pdf (PyMuPDF native redaction annotations)
    image_redactor.py          # .jpg/.png/.tiff/.bmp (pytesseract OCR + Pillow)
```

> Image redaction requires the **Tesseract OCR** binary installed separately
> (not just the `pytesseract` Python package) — see
> https://github.com/UB-Mannheim/tesseract/wiki for Windows installers.
"""
)
