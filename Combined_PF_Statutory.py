# PF & Statutory Compliance Register — Combined App
# Run: streamlit run Combined_PF_Statutory.py
# Tabs: PF Register | Statutory Compliance (ESI / PT / TDS / GSTR-1 / GSTR-3B)
#
# This is a single-file combined app. All logic is self-contained.
# See PF.py and statutory_extractor.py for standalone versions.

import streamlit as st
import pdfplumber
import pandas as pd
import re
import datetime
import zipfile
import os
import sys
from pathlib import Path
from io import BytesIO

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common.period_utils import normalize_period as _normalize_period
from common.statutory_extractors import (
    read_pdf_text as _read_pdf_text,
    g,
    g_last,
    period_from_filename,
    detect_statutory_type,
    extract_tds,
    extract_gstr1,
    extract_gstr3b,
    compute_recon,
)

st.set_page_config(page_title="Statutory Extractor", layout="wide")
st.title("Statutory Extractor")
st.caption("PF Challan · ECR Return · TRRN  |  ESI · PT · TDS · GSTR-1 · GSTR-3B")

# _read_pdf_text/g/g_last/period_from_filename/detect_statutory_type/
# extract_tds/extract_gstr1/extract_gstr3b/compute_recon now live in
# common/statutory_extractors.py (imported above) so tally_tool/reports/ can
# reuse the filed-return parsing and GSTR-1-vs-GSTR-3B reconciliation logic
# for its own "as per books" vs "as per filed return" comparison, without
# importing this Streamlit script. _normalize_period/_MONTH_NUM similarly
# live in common/period_utils.py.
normalize_period = _normalize_period

# ── native folder picker ────────────────────────────────────────────────────
# st.file_uploader has no "select a whole folder" mode -- browsers don't
# expose that on a plain file input. This app runs as a local desktop app
# (see launcher.py), so instead of working around the browser we use a real
# native OS folder-picker dialog and walk the chosen folder ourselves --
# picking up every PDF, at any nesting depth, whether it's a loose file or
# packed inside a ZIP found along the way.
#
# On Windows (the only platform this ships packaged for -- see
# .github/workflows/build-exe.yml's windows-latest runner) this shells out
# to PowerShell's built-in System.Windows.Forms.FolderBrowserDialog instead
# of using tkinter. tkinter was tried first and reproduced live in the
# packaged .exe: "Can't find a usable init.tcl" -- PyInstaller's automatic
# tkinter hook didn't bundle a working Tcl/Tk runtime for this build
# environment. PowerShell + .NET's WinForms ship with every Windows
# install, so there's nothing to bundle and nothing that can go missing
# from the frozen build. tkinter is kept only as a best-effort fallback for
# running from source on Linux/macOS during development.

def _pick_folder() -> str | None:
    """Opens a native folder-picker dialog, returns the chosen path or None
    if the user cancelled / no dialog could be shown."""
    if sys.platform.startswith("win"):
        ps_script = (
            "Add-Type -AssemblyName System.Windows.Forms;"
            "$f = New-Object System.Windows.Forms.FolderBrowserDialog;"
            "$f.Description = 'Select a folder';"
            "if ($f.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) "
            "{ Write-Output $f.SelectedPath }"
        )
        try:
            import subprocess
            result = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
                capture_output=True, text=True, timeout=180,
            )
            return result.stdout.strip() or None
        except Exception as e:
            st.error(f"Couldn't open a folder picker here ({e}). "
                     "Use the file uploader instead, or zip the folder and upload the ZIP.")
            return None
    else:
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            folder = filedialog.askdirectory(title="Select a folder")
            root.destroy()
            return folder or None
        except Exception as e:
            st.error(f"Couldn't open a folder picker here ({e}). "
                     "Use the file uploader instead, or zip the folder and upload the ZIP.")
            return None


def _pick_folder_files(extensions=(".pdf",)):
    """Opens a native folder-picker dialog and returns [(filename, bytes), ...]
    for every matching file found recursively under the chosen folder,
    including matching files inside any ZIPs encountered there. Returns []
    if the user cancels, or if there's no display to show a dialog on
    (e.g. running headless/from source on a server)."""
    folder = _pick_folder()
    if not folder:
        return []

    results = []
    for dirpath, _dirs, filenames in os.walk(folder):
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            lower = fn.lower()
            if lower.endswith(".zip"):
                try:
                    with zipfile.ZipFile(full) as z:
                        for name in z.namelist():
                            if name.lower().endswith(extensions):
                                results.append((os.path.basename(name), z.read(name)))
                except Exception:
                    pass
            elif lower.endswith(extensions):
                with open(full, "rb") as f:
                    results.append((fn, f.read()))
    return results

# ── PF detection / extraction (same as PF.py) ─────────────────────────────────

def _detect_pf_type(text: str) -> str:
    if re.search(r"Payment\s+Confirmation\s+Receipt", text, re.I):
        return "TRRN"
    if re.search(r"COMBINED\s+CHALLAN\s+OF\s+A/C|CHALLAN\s+FOR\s+WAGE\s+MONTH"
                 r"|Dues\s+for\s+the\s+wage\s+month|system\s+generated\s+challan", text, re.I):
        return "CHALLAN"
    if re.search(r"ELECTRONIC\s+CHALLAN\s+CUM\s+RETURN|Return\s+Month"
                 r"|Salary\s+Disbursement\s+Date|ECR\s+Type\b", text, re.I):
        return "RETURN"
    return "CHALLAN"

def _detect_pf_type_plumber(file_path) -> str:
    _C = re.compile(r"COMBINED\s+CHALLAN\s+OF\s+A/C|CHALLAN\s+FOR\s+WAGE\s+MONTH"
                    r"|Dues\s+for\s+the\s+wage\s+month|system\s+generated\s+challan", re.I)
    _R = re.compile(r"ELECTRONIC\s+CHALLAN\s+CUM\s+RETURN|Return\s+Month"
                    r"|Salary\s+Disbursement\s+Date|ECR\s+Type\b", re.I)
    try:
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages[:3]:
                t = page.extract_text() or ""
                if re.search(r"Payment\s+Confirmation\s+Receipt", t, re.I): return "TRRN"
                if _C.search(t): return "CHALLAN"
                if _R.search(t): return "RETURN"
    except Exception:
        pass
    return ""

def extract_pf_trrn(file_name, text):
    # Ported from PF.py's fuller implementation (this file previously had
    # an abbreviated version that dropped the per-account Account-1/2/10/
    # 21/22 breakdown and the 7Q/14B damages-for-delay & interest-for-delay
    # columns entirely) -- real feedback: every "Payment Confirmation
    # Receipt" PDF must land in the TRRN sheet, and when it carries 7Q
    # (interest) / 14B (damages) amounts alongside the base Account
    # amount, those need their own separate columns, not folded into
    # "Total Amount (Rs)".
    lines = [l.strip() for l in text.splitlines()]
    kv = {}
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line or line == ':':
            i += 1
            continue
        if line.lower() == "payment confirmation" and i + 1 < len(lines):
            merged = line + " " + lines[i + 1]
            m = re.match(r'^(.+?)\s*:\s*(.+)$', merged)
            if m:
                kv[m.group(1).strip().lower()] = m.group(2).strip()
                i += 2
                continue
        m = re.match(r'^(.+?)\s*:\s*(.+)$', line)
        if m:
            kv[m.group(1).strip().lower()] = m.group(2).strip()
            i += 1
            continue
        if line and not re.match(r'^:+$', line):
            key = line.lower()
            for j in range(i + 1, min(i + 4, len(lines))):
                cand = lines[j].strip()
                if cand and cand != ':':
                    kv[key] = cand
                    break
        i += 1

    def acct_cols(n):
        # Three numbers on the "Account-N Amount (Rs)" row = Amount, 7Q,
        # 14B, in that column order (matches the Accounts / Amount (Rs) /
        # 7Q / 14B table header on the actual PDF).
        m = re.search(rf"Account-{n}\s+Amount\s*\(Rs\)\s*[:\s]+([\d,]+)\s+([\d,]+)\s+([\d,]+)", text, re.I)
        if m:
            return m.group(1).replace(",", ""), m.group(2).replace(",", ""), m.group(3).replace(",", "")
        m = re.search(rf"([\d,]+)\s+Account-{n}\s+Amount\s*\(Rs\)", text, re.I)
        if m:
            return m.group(1).replace(",", ""), "", ""
        m = re.search(rf"Account-{n}\s+Amount\s*\(Rs\)\s*[:\s]+([\d,]+)", text, re.I)
        if m:
            return m.group(1).replace(",", ""), "", ""
        for key in [f"account-{n} amount (rs)", f"account-{n} amount(rs)"]:
            v = kv.get(key, "")
            if v:
                nums = re.findall(r"[\d,]+", v)
                if nums:
                    return nums[0].replace(",", ""), "", ""
        return "", "", ""

    _acct_nums = [1, 2, 10, 21, 22]
    _cols = {n: acct_cols(n) for n in _acct_nums}

    def _sum_col(idx):
        total, has_val = 0, False
        for n in _acct_nums:
            v = _cols[n][idx]
            if v:
                try:
                    total += int(v)
                    has_val = True
                except ValueError:
                    pass
        return str(total) if has_val else ""

    def fv(*labels):
        for lbl in labels:
            v = kv.get(lbl.lower(), "")
            if v:
                return v
        return ""

    _DATE_RE = r"(\d{2}[-/\.]\w{3}[-/\.]\d{4}|\d{2}[-/\.]\d{2}[-/\.]\d{4})"

    def date_val(*labels):
        for lbl in labels:
            v = kv.get(lbl.lower(), "")
            if v:
                m = re.search(_DATE_RE, v)
                if m:
                    return m.group(1)
                if re.match(r'^\d{2}[-/\.]\w', v):
                    return v.split()[0]
        for lbl in labels:
            m = re.search(re.escape(lbl) + r"[\s\S]{0,30}?" + _DATE_RE, text, re.I)
            if m:
                return m.group(1)
        return ""

    wage_raw = fv("wage month")
    wage_raw = re.split(r'\s+\d{2}:', wage_raw)[0].strip() if wage_raw else ""
    total_amt = fv("total amount (rs)", "total amount(rs)")
    if not total_amt or not re.search(r'\d', total_amt):
        total_amt = g(r"Total Amount\s*\(Rs\)\s*[:\s]+([\d,]+)", text)
    bank = fv("payment confirmation bank", "bank name", "remitting bank", "bank")
    if not bank:
        for pat in [
            r"Payment\s+Confirmation\s*\n?\s*Bank\s*[:\s]+([A-Za-z][^\n]+)",
            r"Bank\s+Name\s*[:\s]+([A-Za-z][^\n]+)",
            r"Bank\s*[:\s]+([A-Z][A-Z &]+(?:BANK|LTD)[^\n]*)",
        ]:
            m = re.search(pat, text, re.I)
            if m:
                bank = m.group(1).strip()
                break

    return {
        "File Name": file_name,
        "Client Name": fv("establishment name"),
        "Establishment ID": fv("establishment id"),
        "TRRN No": fv("trrn no", "trrn number", "trrn"),
        "Challan Status": fv("challan status"),
        "Challan Type": fv("challan type"),
        "Wage Month": _normalize_period(wage_raw),
        "Total Members": fv("total members"),
        "Total Amount (Rs)": total_amt,
        "Account-1 (EPF)": _cols[1][0],
        "Account-2 (Admin EPF)": _cols[2][0],
        "Account-10 (EPS)": _cols[10][0],
        "Account-21 (EDLI)": _cols[21][0],
        "Account-22 (Admin)": _cols[22][0],
        "7Q Total": _sum_col(1),
        "14B Total": _sum_col(2),
        "Payment Date": date_val("payment date", "date of payment", "challan date", "value date"),
        "Payment Confirmation Date": date_val("payment confirmation date", "confirmation date"),
        "Bank": bank,
        "CRN": fv("crn"),
    }

def extract_pf_data(pdf_path) -> dict:
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[0]
        text = page.extract_text() or ""
        tables = page.extract_tables()
    is_new = bool(re.search(r"CHALLAN FOR WAGE MONTH", text, re.I))
    comp_m = re.search(r"^\s*Name\s*:\s*(.+)", text, re.I | re.M) if is_new else \
             re.search(r"Establishment Code & Name\s+\S+\s+(.*?)(?=\nAddress|\n\n|$)", text, re.DOTALL)
    company = comp_m.group(1).strip().splitlines()[0].strip() if comp_m else ""
    wm_m = re.search(r"CHALLAN FOR WAGE MONTH\s*[:\-]\s*(\w+\s+\d{4})", text, re.I) if is_new else \
           re.search(r"Dues for the wage month of\s+(\w+\s*\d{4})", text, re.I)
    month = wm_m.group(1).replace(" ", "") if wm_m else ""
    gt_matches = list(re.finditer(r"Grand Total\s*[:\-]?[^\d]*([\d,]+)", text, re.I))
    gt_m = gt_matches[-1] if gt_matches else None
    detail_table_df = None
    for t in (tables or []):
        if not t: continue
        header = [str(c or "").strip() for c in (t[0] or [])]
        if "PARTICULARS" in " ".join(header).upper() and "A/C" in " ".join(header).upper():
            cleaned = [[c if c is not None else "" for c in row] for row in t[1:] if row]
            if cleaned:
                detail_table_df = pd.DataFrame(cleaned, columns=header)
                break
    return {
        "Company": company, "Month": month,
        "Grand Total": gt_m.group(1) if gt_m else "",
        "Detail Table": detail_table_df, "Charges Table": detail_table_df,
    }

def extract_ecr_return(file_name, text, file_path=None):
    kv = {}
    if file_path:
        try:
            with pdfplumber.open(file_path) as pdf:
                for page in pdf.pages:
                    for seg in re.split(r'\s{2,}', page.extract_text() or ""):
                        m = re.match(r'^(.+?)\s*:\s*(.+)$', seg.strip())
                        if m:
                            kv.setdefault(m.group(1).strip().lower(), m.group(2).strip())
        except Exception:
            pass
    def f(keys, pat, num=False):
        for k in keys:
            v = kv.get(k, "")
            if v and v.lower() not in ("none", "na"): return re.sub(r"[,\s]", "", v) if num else v
        m = re.search(pat, text, re.I)
        return re.sub(r"[,\s]", "", m.group(1)) if m and num else (m.group(1).strip() if m else "")
    return {
        "File Name": file_name,
        "Name of Establishment": f(["name of establishment"], r"Name of Establishment\s*:?\s*(.+?)(?=\n|$)"),
        "Establishment Id": f(["establishment id"], r"Establishment Id\s*:?\s*([A-Z0-9/\-]+)"),
        "Wage Month": f(["wage month"], r"Wage Month\s*:?\s*([A-Za-z]+-\d{4})"),
        "Return Month": f(["return month"], r"Return Month\s*:?\s*([A-Za-z]+-\d{4})"),
        "ECR Type": f(["ecr type"], r"ECR Type\s*:?\s*(\w+)"),
        "TRRN No": f(["trrn number", "trrn no", "trrn"], r"TRRN(?:\s+Number|\s+No\.?|\b)\s*[:\s]+(\d+)"),
        "Total Members": f(["total members", "total subscribers"],
                           r"Total\s+(?:Members|Subscribers)\s*:?\s*([\d,]+)", num=True),
        "Total EPF Contribution": f(["total epf contribution"],
                                    r"Total EPF Contribution\s*:?\s*([\d,]+)", num=True),
        "Total EPS Contribution": f(["total eps contribution"],
                                    r"Total EPS Contribution\s*:?\s*([\d,]+)", num=True),
    }

# ── statutory detection / extraction (same as statutory_extractor.py) ─────────
# detect_statutory_type/extract_tds/extract_gstr1/extract_gstr3b/compute_recon
# are imported from common/statutory_extractors.py (see the top of this file)
# -- extract_esi/extract_pt stay here since nothing outside this file needs them.

def extract_esi(fn, text):
    return {"File Name": fn, "Employer Name": g(r"Employer.s Name\s*:\s*([^\n]+)", text),
            "Employer Code No": g(r"Employer.s Code No\s*:\s*([^\n]+)", text),
            "Challan Period": normalize_period(g(r"Challan Period\s*:\s*([^\n]+)", text)),
            "Challan Number": g(r"Challan Number\s*[:\s]+([^\n]+)", text),
            "Amount Paid": g(r"Amount Paid\s*:\s*([\d.,]+)", text),
            "Transaction Number": g(r"Transaction Number\s*:\s*([^\n]+)", text)}

def extract_pt(fn, text):
    period_raw = g(r"ending on\s*:\s*([A-Za-z]+\s*[-–]\s*\d{4})", text)
    return {"File Name": fn,
            "Client Name": g(r"Trade Name\s*:\s*(.+)", text) or g(r"Name of the Employer\s*:\s*(.+)", text),
            "Period": normalize_period(period_raw),
            "Grand Total": g_last(r"Grand Total\s+([\d,]+)", text) or g_last(r"Grand Total\s*:\s*([\d,]+)", text)}

def process_statutory_pdf(file_name, file_bytes, state_prefix="stat_"):
    import traceback
    try:
        text = _read_pdf_text(file_bytes)
        if len(text.strip()) < 50:
            st.session_state[state_prefix + "failed"].append(f"{file_name} — image-only PDF")
            return
        doc_type = detect_statutory_type(text)
        if doc_type == "GSTR2B":
            st.session_state[state_prefix + "failed"].append(f"{file_name} — GSTR-2B (skipped)")
        elif doc_type in ("ESI", "PT", "TDS", "GSTR1", "GSTR3B"):
            dispatch = {"ESI": ("esi", extract_esi), "PT": ("pt", extract_pt),
                        "TDS": ("tds", extract_tds), "GSTR1": ("gstr1", extract_gstr1),
                        "GSTR3B": ("gstr3b", extract_gstr3b)}
            key, fn = dispatch[doc_type]
            st.session_state[state_prefix + key].append(fn(file_name, text))
        else:
            preview = text.strip()[:200].replace("\n", " ")
            st.session_state[state_prefix + "failed"].append(f"{file_name} — Unrecognised. Preview: «{preview}»")
    except Exception as e:
        tb = traceback.format_exc().splitlines()
        st.session_state[state_prefix + "failed"].append(f"{file_name} — {type(e).__name__}: {e}")

# ── session state init ────────────────────────────────────────────────────────

_STAT_KEYS = ["stat_esi", "stat_pt", "stat_tds", "stat_gstr1", "stat_gstr3b"]
for _k in _STAT_KEYS:
    if _k not in st.session_state: st.session_state[_k] = []
if "stat_failed" not in st.session_state: st.session_state.stat_failed = []
if "stat_recon" not in st.session_state: st.session_state.stat_recon = []

# ── main tabs ─────────────────────────────────────────────────────────────────

tab_pf, tab_stat = st.tabs(["PF Register", "Statutory Compliance"])

with tab_pf:
    st.subheader("PF Consolidated Register")
    if "pf_folder_files" not in st.session_state:
        st.session_state.pf_folder_files = []

    pf_uploaded = st.file_uploader(
        "Upload PF Challan / TRRN PDFs, or a ZIP of them",
        type=["pdf", "zip"], accept_multiple_files=True, key="pf_uploader",
    )
    col_f1, col_f2 = st.columns([1, 3])
    with col_f1:
        if st.button("📁 Select a folder", key="pf_folder_btn"):
            picked = _pick_folder_files(extensions=(".pdf",))
            if picked:
                st.session_state.pf_folder_files = picked
                st.success(f"Found {len(picked)} PDF(s) in the selected folder.")
            elif picked == [] and st.session_state.pf_folder_files:
                pass  # user cancelled the dialog -- keep the previous pick
    with col_f2:
        if st.session_state.pf_folder_files:
            st.caption(
                f"📁 {len(st.session_state.pf_folder_files)} PDF(s) picked from a folder "
                f"(will be included below)."
            )
            if st.button("Clear folder selection", key="pf_folder_clear"):
                st.session_state.pf_folder_files = []
                st.rerun()

    if st.button("Generate PF Register", type="primary", key="pf_gen"):
        pf_files = list(st.session_state.pf_folder_files)
        for f in (pf_uploaded or []):
            if f.name.lower().endswith(".zip"):
                with zipfile.ZipFile(f) as z:
                    for name in z.namelist():
                        if name.lower().endswith(".pdf"):
                            pf_files.append((os.path.basename(name), z.read(name)))
            else:
                pf_files.append((f.name, f.read()))

        if not pf_files:
            st.error("Please upload PF PDFs or select a folder.")
        else:

            challan_rows, detail_tables, trrn_rows, return_rows, pf_failed = [], [], [], [], []
            progress = st.progress(0)
            for idx, (file_name, file_bytes) in enumerate(pf_files):
                temp = Path("data") / file_name
                temp.parent.mkdir(exist_ok=True)
                temp.write_bytes(file_bytes)
                file_path = temp
                try:
                    text = _read_pdf_text(file_bytes)
                    doc_type = _detect_pf_type(text)
                    if doc_type == "CHALLAN" or len(text.strip()) < 100:
                        pt = _detect_pf_type_plumber(file_path)
                        if pt: doc_type = pt
                    if doc_type == "TRRN":
                        trrn_rows.append(extract_pf_trrn(file_name, text))
                    elif doc_type == "RETURN":
                        return_rows.append(extract_ecr_return(file_name, text, file_path))
                    else:
                        data = extract_pf_data(file_path)
                        dtbl = data.pop("Detail Table")
                        data.pop("Charges Table", None)
                        data["File Name"] = file_name
                        challan_rows.append(data)
                        if dtbl is not None and not dtbl.empty:
                            dtbl.insert(0, "Company", data.get("Company", ""))
                            dtbl.insert(1, "Month", data.get("Month", ""))
                            detail_tables.append(dtbl)
                except Exception as e:
                    pf_failed.append(f"{file_name}: {e}")
                progress.progress((idx + 1) / len(pf_files))

            parts = []
            if challan_rows: parts.append(f"**{len(challan_rows)}** Challan(s)")
            if return_rows:  parts.append(f"**{len(return_rows)}** Return(s)")
            if trrn_rows:    parts.append(f"**{len(trrn_rows)}** TRRN(s)")
            if pf_failed:    parts.append(f"**{len(pf_failed)}** failed")
            st.success("Done — " + " · ".join(parts)) if parts else st.error("No data extracted")

            if pf_failed:
                with st.expander(f"⚠️ {len(pf_failed)} failed", expanded=True):
                    for msg in pf_failed: st.code(msg)

            if challan_rows or trrn_rows or return_rows:
                lbls, tab_idx = [], 0
                if challan_rows: lbls.append(f"Challan ({len(challan_rows)})")
                if detail_tables: lbls.append("Summary")
                if return_rows: lbls.append(f"Return ({len(return_rows)})")
                if trrn_rows: lbls.append(f"TRRN ({len(trrn_rows)})")
                ptabs = st.tabs(lbls)
                if challan_rows:
                    with ptabs[tab_idx]: st.dataframe(pd.DataFrame(challan_rows), use_container_width=True)
                    tab_idx += 1
                if detail_tables:
                    with ptabs[tab_idx]: st.dataframe(pd.concat(detail_tables, ignore_index=True), use_container_width=True)
                    tab_idx += 1
                if return_rows:
                    with ptabs[tab_idx]: st.dataframe(pd.DataFrame(return_rows), use_container_width=True)
                    tab_idx += 1
                if trrn_rows:
                    with ptabs[tab_idx]: st.dataframe(pd.DataFrame(trrn_rows), use_container_width=True)

                buf = BytesIO()
                with pd.ExcelWriter(buf, engine="openpyxl") as w:
                    if challan_rows: pd.DataFrame(challan_rows).to_excel(w, sheet_name="Challan Header", index=False)
                    if detail_tables: pd.concat(detail_tables, ignore_index=True).to_excel(w, sheet_name="Challan Summary", index=False)
                    if return_rows: pd.DataFrame(return_rows).to_excel(w, sheet_name="Return", index=False)
                    if trrn_rows: pd.DataFrame(trrn_rows).to_excel(w, sheet_name="TRRN", index=False)
                buf.seek(0)
                st.download_button("Download Excel", buf, file_name="PF_Consolidated_Register.xlsx",
                                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key="pf_dl")

with tab_stat:
    st.subheader("Statutory Compliance Extractor")
    st.caption("ESI · PT · TDS (ITNS 281) · GSTR-1 · GSTR-3B")

    if "stat_folder_files" not in st.session_state:
        st.session_state.stat_folder_files = []

    col1, col2 = st.columns(2)
    with col1:
        stat_pdfs = st.file_uploader("Upload PDFs", type="pdf", accept_multiple_files=True, key="stat_pdf")
    with col2:
        stat_zips = st.file_uploader("Upload ZIPs", type="zip", accept_multiple_files=True, key="stat_zip")

    col_f1, col_f2 = st.columns([1, 3])
    with col_f1:
        if st.button("📁 Select a folder", key="stat_folder_btn"):
            picked = _pick_folder_files(extensions=(".pdf",))
            if picked:
                st.session_state.stat_folder_files = picked
                st.success(f"Found {len(picked)} PDF(s) in the selected folder.")
    with col_f2:
        if st.session_state.stat_folder_files:
            st.caption(
                f"📁 {len(st.session_state.stat_folder_files)} PDF(s) picked from a folder "
                f"(will be included in Extract All)."
            )
            if st.button("Clear folder selection", key="stat_folder_clear"):
                st.session_state.stat_folder_files = []
                st.rerun()

    col_b1, col_b2, col_b3 = st.columns(3)
    with col_b1:
        stat_btn = st.button("Extract All", type="primary", use_container_width=True, key="stat_btn")
    with col_b2:
        if st.button("Clear", use_container_width=True, key="stat_clear"):
            for _k in _STAT_KEYS: st.session_state[_k] = []
            st.session_state.stat_failed = []
            st.session_state.stat_recon = []
            st.session_state.stat_folder_files = []
            st.success("Cleared.")
    with col_b3:
        if st.button("Reset", use_container_width=True, key="stat_reset"):
            for _k in _STAT_KEYS: st.session_state[_k] = []
            st.session_state.stat_failed = []
            st.session_state.stat_recon = []
            st.session_state.stat_folder_files = []
            st.rerun()

    if stat_btn:
        for _k in _STAT_KEYS: st.session_state[_k] = []
        st.session_state.stat_failed = []
        st.session_state.stat_recon = []

        all_pdfs = list(st.session_state.stat_folder_files)
        all_pdfs += [(f.name, f.read()) for f in (stat_pdfs or [])]
        for zf in (stat_zips or []):
            with zipfile.ZipFile(zf) as z:
                for name in z.namelist():
                    if name.lower().endswith(".pdf"):
                        all_pdfs.append((os.path.basename(name), z.read(name)))

        if not all_pdfs:
            st.warning("No PDF files found.")
        else:
            bar = st.progress(0)
            for i, (name, data) in enumerate(all_pdfs):
                process_statutory_pdf(name, data, state_prefix="stat_")
                bar.progress((i + 1) / len(all_pdfs))
            if st.session_state.stat_gstr1 or st.session_state.stat_gstr3b:
                stat_recon, stat_dup_keys = compute_recon(
                    st.session_state.stat_gstr1, st.session_state.stat_gstr3b
                )
                st.session_state.stat_recon = stat_recon
                if stat_dup_keys:
                    st.warning(
                        "Multiple GSTR-3B files found for the same GSTIN + Tax Period — "
                        "only the most recently processed file is used for each: "
                        + ", ".join(f"{gstin} / {period}" for gstin, period in stat_dup_keys),
                        icon="⚠️",
                    )
            counts = {k: len(st.session_state[k]) for k in _STAT_KEYS}
            st.success(f"Done — **{counts['stat_esi']}** ESI · **{counts['stat_pt']}** PT · **{counts['stat_tds']}** TDS · **{counts['stat_gstr1']}** GSTR-1 · **{counts['stat_gstr3b']}** GSTR-3B")

    if st.session_state.stat_failed:
        with st.expander(f"⚠️ {len(st.session_state.stat_failed)} issue(s)"):
            for msg in st.session_state.stat_failed: st.code(msg, language="")

    if any(st.session_state[k] for k in _STAT_KEYS):
        stat_recon_rows = st.session_state.get("stat_recon", [])
        tlabels = [f"ESI ({len(st.session_state['stat_esi'])})", f"PT ({len(st.session_state['stat_pt'])})",
                   f"TDS ({len(st.session_state['stat_tds'])})", f"GSTR-1 ({len(st.session_state['stat_gstr1'])})",
                   f"GSTR-3B ({len(st.session_state['stat_gstr3b'])})", f"Recon ({len(stat_recon_rows)})"]
        stabs = st.tabs(tlabels)
        for tab, key in zip(stabs[:-1], ["stat_esi", "stat_pt", "stat_tds", "stat_gstr1", "stat_gstr3b"]):
            with tab:
                rows = st.session_state[key]
                st.dataframe(pd.DataFrame(rows), use_container_width=True) if rows else st.info("No records.")
        with stabs[-1]:
            if stat_recon_rows:
                df_r = pd.DataFrame(stat_recon_rows)
                def _hl(row):
                    c = {"Matched": "#d4edda", "Mismatch": "#f8d7da", "Only in GSTR-1": "#fff3cd", "Only in GSTR-3B": "#cce5ff"}.get(row["Status"], "")
                    return [f"background-color:{c}" if c else ""] * len(row)
                st.dataframe(df_r.style.apply(_hl, axis=1), use_container_width=True)
            else:
                st.info("Upload both GSTR-1 and GSTR-3B to see reconciliation.")

        buf = BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as w:
            for sname, key in [("ESI", "stat_esi"), ("PT", "stat_pt"), ("TDS", "stat_tds"),
                                ("GSTR1", "stat_gstr1"), ("GSTR3B", "stat_gstr3b")]:
                rows = st.session_state[key]
                (pd.DataFrame(rows) if rows else pd.DataFrame(columns=["No Data"])).to_excel(w, sheet_name=sname, index=False)
            (pd.DataFrame(stat_recon_rows) if stat_recon_rows else pd.DataFrame(columns=["No Data"])).to_excel(
                w, sheet_name="GSTR1_vs_3B_Recon", index=False)
        buf.seek(0)
        st.download_button("⬇️ Download Excel (All Sheets)", buf,
                           file_name="Statutory_Compliance_Data.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           use_container_width=True, type="primary", key="stat_dl")
