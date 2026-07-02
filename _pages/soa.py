"""Hub page: SOA · RPS · Reconcile — Streamlit port of the Flask app (app.py).

Upload any mix of SOA and Repayment Schedule PDFs; each is auto-classified
and processed:
  * SOA -> per-loan TOC/TOD workbook (+ a portfolio exception report for 2+).
  * RPS -> a single combined workbook (Loan_Details + Repayment_Schedule).
  * any Agreement No present as BOTH an SOA and an RPS is reconciled automatically.
"""
import io
import os
import sys
import tempfile
import zipfile
from pathlib import Path

import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _pages.theme import page_header, footer

from extract_soa import extract_loan, write_workbook, write_portfolio
from extract_rps import extract_rps, write_rps_workbook
from reconcile import classify, reconcile_jobs, write_reconciliation_workbook

page_header(
    "📊", "SOA · RPS · Reconcile",
    "L&T Finance NBFC Statement-of-Account extractor with TOC/TOD validation, "
    "RPS parser, and automatic SOA-vs-RPS reconciliation by Agreement No.",
    badges=["Auto-classify SOA/RPS", "TOC/TOD checks", "Portfolio report"],
)

files = st.file_uploader(
    "SOA / RPS PDF(s) — drop any mix; each is auto-classified",
    type=["pdf"],
    accept_multiple_files=True,
)

if files and st.button("Process", type="primary"):
    with tempfile.TemporaryDirectory() as tmpdir:
        job_dir = Path(tmpdir)
        results, soas, rpss = [], [], []
        progress = st.progress(0, text="Starting…")

        for i, f in enumerate(files):
            pdf_path = job_dir / f"{i:03d}_{f.name}"
            pdf_path.write_bytes(f.getvalue())
            progress.progress((i + 1) / len(files), text=f"Processing {f.name}…")

            rec = {"file": f.name, "ok": False, "doctype": "unknown", "agreement": "",
                   "exceptions": 0, "stage": "", "instalments": 0, "error": "",
                   "xlsx": None, "data": None}
            try:
                kind = classify(str(pdf_path))
                rec["doctype"] = kind
                if kind == "soa":
                    d = extract_loan(str(pdf_path))
                    xlsx = job_dir / f"{i:03d}_{pdf_path.stem}.xlsx"
                    write_workbook(str(xlsx), d["master"], d["fin_rows"], d["recv"], d["disb"],
                                   d["txns"], d["checks"], d["dpd_rows"], part_pay=d["part_pay"],
                                   bounce=d["bounce"], charges=d["charges"], amort=d["amort"],
                                   bounce_grid=d["bounce_grid"], quality=d["quality"])
                    rec.update(ok=True, agreement=d["master"].get("Agreement No"),
                               exceptions=d["summary"]["Exceptions"], stage=d["summary"]["Current Stage"],
                               xlsx=xlsx, data=d)
                    soas.append(d)
                elif kind == "rps":
                    d = extract_rps(str(pdf_path))
                    rec.update(ok=True, agreement=d["master"].get("Agreement No"),
                               instalments=len(d["schedule"]), data=d)
                    rpss.append(d)
                else:
                    rec["error"] = "not a recognised SOA or RPS document"
            except Exception as e:
                rec["error"] = str(e)[:200]
            results.append(rec)

        good_soa = [r for r in results if r["doctype"] == "soa" and r["ok"]]
        portfolio_path = rps_path = recon_path = None
        if len(good_soa) >= 2:
            portfolio_path = job_dir / "Portfolio_Exception_Report.xlsx"
            write_portfolio(str(portfolio_path), [r["data"] for r in good_soa])
        if rpss:
            rps_path = job_dir / "RPS_Combined.xlsx"
            write_rps_workbook(str(rps_path), rpss)
        pairs, soa_only, rps_only = reconcile_jobs(soas, rpss)
        if pairs:
            recon_path = job_dir / "SOA_RPS_Reconciliation.xlsx"
            write_reconciliation_workbook(str(recon_path), pairs, soa_only, rps_only)

        progress.progress(1.0, text="Done")

        # ── results table ────────────────────────────────────────────────────
        st.divider()
        deviations = sum(1 for p in pairs if p["summary"]["result"] == "EXCEPTION")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("SOA processed", len(good_soa),
                  f"{sum(1 for r in good_soa if r['exceptions'] == 0)} clean")
        c2.metric("RPS processed", len(rpss))
        c3.metric("Reconciled", len(pairs), f"{deviations} with deviations" if deviations else None)
        c4.metric("Failed", sum(1 for r in results if not r["ok"]))

        table_rows = [
            {"File": r["file"], "Type": r["doctype"], "Agreement No": r["agreement"] or "",
             "Status": "OK" if r["ok"] else f"FAILED: {r['error']}",
             "Exceptions": r["exceptions"] if r["doctype"] == "soa" else "",
             "Stage": r["stage"], "Instalments": r["instalments"] if r["doctype"] == "rps" else ""}
            for r in results
        ]
        st.dataframe(table_rows, use_container_width=True, hide_index=True)

        # ── downloads ─────────────────────────────────────────────────────────
        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for r in results:
                if r["xlsx"]:
                    zf.write(r["xlsx"], arcname=os.path.basename(r["xlsx"]))
            for p in (portfolio_path, rps_path, recon_path):
                if p:
                    zf.write(p, arcname=p.name)
        zip_buf.seek(0)

        st.markdown("**Downloads**")
        dl_cols = st.columns(4)
        dl_cols[0].download_button("⬇ All (.zip)", zip_buf, file_name="SOA_RPS_Output.zip",
                                   mime="application/zip", use_container_width=True)
        if portfolio_path:
            dl_cols[1].download_button("⬇ Portfolio report", portfolio_path.read_bytes(),
                                       file_name=portfolio_path.name, use_container_width=True)
        if rps_path:
            dl_cols[2].download_button("⬇ RPS combined", rps_path.read_bytes(),
                                       file_name=rps_path.name, use_container_width=True)
        if recon_path:
            dl_cols[3].download_button("⬇ Reconciliation", recon_path.read_bytes(),
                                       file_name=recon_path.name, use_container_width=True)

        if any(r["xlsx"] for r in results):
            with st.expander("Individual loan workbooks"):
                for r in results:
                    if r["xlsx"]:
                        st.download_button(
                            f"⬇ {r['file']}", r["xlsx"].read_bytes(),
                            file_name=os.path.basename(r["xlsx"]), key=f"dl_{r['file']}",
                        )

with st.expander("What this does / command line"):
    st.markdown(
        """
**SOA mode** — per-loan working-paper workbooks + a Portfolio Exception Report,
with TOC/TOD validation (see `README.md` for the full check table).

**RPS mode** — one combined workbook (`Loan_Details`, `Repayment_Schedule`) from
all uploaded Repayment Schedules.

**Reconcile mode** — any Agreement No present as both an SOA and an RPS is
automatically matched and reconciled — `Reconciliation_Summary`, `Term_Checks`,
`Instalment_Comparison`, `Unmatched`.

A bad/corrupt file is reported as FAILED with the reason and never aborts the batch.

**Command line** (for large batches / folders):
```bash
python extract_soa.py SOA_xxxx.pdf [output.xlsx]      # single loan
python extract_soa.py --dir ./pdfs ./out              # one workbook per loan
python extract_soa.py --portfolio ./pdfs report.xlsx  # portfolio exception report
python extract_rps.py --dir ./rps_pdfs out.xlsx        # combine an RPS folder
```
"""
    )

footer()
