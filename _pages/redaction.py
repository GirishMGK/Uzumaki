"""Hub page: Secure Document Redaction Tool — Streamlit port of the tkinter app.

Auto-detects and redacts PAN, TAN, GSTIN, CIN, Aadhaar, Phone, Email (plus
custom keywords) across PDF, DOCX, XLSX, and images, reusing the same
pattern-matching / redaction engine as the original desktop GUI.
"""
import io
import os
import sys
import tempfile
import zipfile
from pathlib import Path

import streamlit as st

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TOOL_DIR = os.path.join(_REPO_ROOT, "redaction_tool")
sys.path.insert(0, _REPO_ROOT)
if _TOOL_DIR not in sys.path:
    sys.path.insert(0, _TOOL_DIR)

from _pages.theme import page_header, footer

from core.patterns import PATTERN_GROUPS, get_active_patterns
from core.engine import RedactionEngine, SUPPORTED_EXTENSIONS

page_header(
    "🔒", "Secure Document Redaction Tool",
    "Auto-detect and redact PAN, TAN, GSTIN, CIN, Aadhaar, Phone, Email — "
    "PDF, DOCX, XLSX, images (OCR).",
    badges=["PDF true redaction", "DOCX/XLSX", "Image OCR"],
)

files = st.file_uploader(
    "File(s) to redact — " + ", ".join(sorted(SUPPORTED_EXTENSIONS)),
    type=[ext.lstrip(".") for ext in SUPPORTED_EXTENSIONS],
    accept_multiple_files=True,
)

st.markdown("**Auto-detect patterns**")
pattern_cols = st.columns(len(PATTERN_GROUPS))
enabled = {}
for col, (key, grp) in zip(pattern_cols, PATTERN_GROUPS.items()):
    with col:
        enabled[key] = st.checkbox(grp["label"], value=True, key=f"pat_{key}")

c1, c2 = st.columns([2, 1])
with c1:
    keywords_raw = st.text_area(
        "Custom keywords / phrases (comma or newline separated)",
        placeholder="e.g. John Doe, Acme Pvt Ltd",
    )
with c2:
    replacement = st.text_input("Replacement text", value="[REDACTED]")
    case_sensitive = st.checkbox("Case sensitive matching", value=False)

custom_terms = [t.strip() for t in keywords_raw.replace(",", "\n").splitlines() if t.strip()]
patterns = get_active_patterns(enabled)

if files and st.button("Redact", type="primary", disabled=not (patterns or custom_terms)):
    if not (patterns or custom_terms):
        st.warning("Enable at least one auto-detect pattern or add a custom keyword.")
    else:
        with tempfile.TemporaryDirectory() as tmpdir:
            in_dir = Path(tmpdir) / "in"
            out_dir = Path(tmpdir) / "_redacted"
            in_dir.mkdir()
            paths = []
            for f in files:
                p = in_dir / f.name
                p.write_bytes(f.getvalue())
                paths.append(str(p))

            progress = st.progress(0, text="Starting…")

            def _cb(done, total, name):
                pct = done / total if total else 1.0
                progress.progress(min(pct, 1.0), text=f"Processing {name} ({done}/{total})…")

            engine = RedactionEngine()
            audit = engine.process_files(
                paths, patterns, custom_terms, replacement or "[REDACTED]",
                case_sensitive, output_dir=str(out_dir), progress_callback=_cb,
            )
            progress.progress(1.0, text="Done")

            total_redactions = sum(e.get("Count", 0) for e in audit)
            errors = [e for e in audit if e.get("Category") == "ERROR"]

            st.divider()
            k1, k2, k3 = st.columns(3)
            k1.metric("Files processed", len(files))
            k2.metric("Total redactions", total_redactions)
            k3.metric("Errors", len(errors))

            if audit:
                st.markdown("**Audit log**")
                st.dataframe(audit, use_container_width=True, hide_index=True)

            out_files = sorted(out_dir.glob("*")) if out_dir.exists() else []
            if out_files:
                zip_buf = io.BytesIO()
                with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
                    for p in out_files:
                        zf.write(p, arcname=p.name)
                zip_buf.seek(0)
                st.download_button(
                    "⬇ Download all redacted files (.zip)", zip_buf,
                    file_name="redacted_files.zip", mime="application/zip",
                )
                with st.expander("Individual redacted files"):
                    for p in out_files:
                        st.download_button(f"⬇ {p.name}", p.read_bytes(), file_name=p.name, key=f"dl_{p.name}")

            if audit:
                import pandas as pd
                audit_csv = pd.DataFrame(audit).to_csv(index=False).encode("utf-8")
                st.download_button("⬇ Audit log (CSV)", audit_csv, file_name="redaction_audit_log.csv", mime="text/csv")

with st.expander("What this does"):
    st.markdown(
        """
- **Auto-detect patterns** — PAN, TAN, GSTIN, CIN, Aadhaar, Phone, Email (India-specific regex)
- **Custom keywords** — comma or newline separated
- **Supported formats** — PDF (true redaction via PyMuPDF), DOCX, XLSX, JPG/PNG/TIFF/BMP (OCR via Tesseract)
- **Audit log** — per-file redaction counts by category, exportable to CSV

> Image redaction requires the **Tesseract OCR** binary installed separately
> (not just the `pytesseract` Python package) — see
> https://github.com/UB-Mannheim/tesseract/wiki for Windows installers.

**Package layout**
```
redaction_tool/
  main.py                      # standalone tkinter GUI entry point (still works outside the hub)
  core/
    patterns.py, profile_manager.py, engine.py
  redactors/
    word_redactor.py, excel_redactor.py, pdf_redactor.py, image_redactor.py
```
"""
    )

footer()
