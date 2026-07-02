"""Hub page: Form 26AS Extractor — flattens Part A (TDS) into a searchable table."""
import io
import os
import sys
import tempfile
from pathlib import Path

import streamlit as st

from _pages.theme import page_header, footer

_TOOL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "form26as_tool")
if _TOOL_DIR not in sys.path:
    sys.path.insert(0, _TOOL_DIR)

from form26as.merge import parse_files  # noqa: E402
from form26as.writer import write_csv, write_xlsx  # noqa: E402

page_header(
    "🧮", "Form 26AS Extractor",
    "Flatten one or more Form 26AS downloads (text/Excel/HTML/PDF) into a single, "
    "searchable table — every transaction row carries its deductor's Name and TAN.",
    badges=["Streamlit", "Multi-year combine", "PDF password support"],
)

st.markdown(
    "Upload **one 26AS per assessment year** (TRACES only lets you download one at a "
    "time) — they'll be combined into a single workbook, tagged by year, with rollup "
    "summary tabs (by year / deductor / section / month)."
)

files = st.file_uploader(
    "Form 26AS file(s) — .txt, .xlsx, .xls, .html, or .pdf",
    type=["txt", "xlsx", "xls", "html", "htm", "pdf"],
    accept_multiple_files=True,
)

password = ""
if files and any(f.name.lower().endswith(".pdf") for f in files):
    password = st.text_input(
        "PDF password (applied to every PDF in this batch)",
        type="password",
        help="TRACES PDFs use your date of birth as DDMMYYYY, e.g. 15041985.",
    )

also_csv = st.checkbox("Also produce a CSV", value=False)

if files and st.button("Convert", type="primary"):
    with tempfile.TemporaryDirectory() as tmpdir:
        paths = []
        for f in files:
            p = Path(tmpdir) / f.name
            p.write_bytes(f.getvalue())
            paths.append(p)

        passwords = {p: password for p in paths if p.suffix.lower() == ".pdf"} if password else {}
        transactions, results = parse_files(paths, passwords=passwords)

        for r in results:
            name = r["path"].name
            if r["error"]:
                hint = " (if it's password-protected, enter the PDF password above)" if name.lower().endswith(".pdf") else ""
                st.error(f"**{name}** — {r['error']}{hint}")
            elif r["count"] == 0:
                st.warning(f"**{name}** — no Part A transactions found (detected year: {r['year']}).")
            else:
                st.success(f"**{name}** — {r['count']} transactions (year: {r['year']})")

        if transactions:
            out_name = (
                f"{paths[0].stem}_formatted.xlsx" if len(paths) == 1 else "26AS_Combined_formatted.xlsx"
            )
            xlsx_buf = io.BytesIO()
            xlsx_path = Path(tmpdir) / out_name
            write_xlsx(transactions, xlsx_path)
            xlsx_buf.write(xlsx_path.read_bytes())
            xlsx_buf.seek(0)

            st.divider()
            st.caption(f"**{len(transactions)}** transactions across **{len(paths)}** file(s).")
            dl1, dl2 = st.columns(2)
            dl1.download_button(
                "⬇ Download formatted workbook (.xlsx)",
                xlsx_buf, file_name=out_name,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )
            if also_csv:
                csv_path = Path(tmpdir) / (Path(out_name).stem + ".csv")
                write_csv(transactions, csv_path)
                dl2.download_button(
                    "⬇ Download CSV", csv_path.read_bytes(),
                    file_name=csv_path.name, mime="text/csv",
                    use_container_width=True,
                )

with st.expander("What this does / supported formats"):
    st.markdown(
        """
**Output columns** — one row per transaction:
`Assessment Year, Source File, Deductor Sr. No., Name of Deductor, TAN of Deductor,
Total Amount Paid/Credited, Total Tax Deducted, Total TDS Deposited, Txn Sr. No.,
Section, Transaction Date, Status of Booking, Date of Booking, Remarks,
Amount Paid/Credited, Tax Deducted, TDS Deposited`

**Summary tabs** in the Excel output — Summary by Year (multi-file only), by Deductor,
by Section, by Month — each with a grand-total row, plus AutoFilter and a frozen header
on the main sheet.

**Supported input** (auto-detected from content, not just the extension):
- Caret (`^`) delimited `.txt` — the native TRACES download (tab/comma also work)
- Genuine `.xlsx` / `.xls` workbooks
- **PDF**, including password-protected TRACES downloads (date of birth as `DDMMYYYY`)
- HTML exports, including TRACES "Excel" files that are really HTML with an
  `.xls`/`.xlsx` extension

Only **Part A** (TDS) is extracted; Part A1/A2/B/C rows are detected and skipped so
they don't pollute the totals.

**Command line / desktop app** — the same engine also ships as a standalone tool with
a double-click Windows launcher:
```bash
cd form26as_tool
pip install -r requirements.txt
python -m form26as path/to/26AS.txt
# or double-click Run26ASFormatter.bat for the file-picker GUI
```
"""
    )

footer()
