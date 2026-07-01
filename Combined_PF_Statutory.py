# PF & Statutory Compliance Register — Combined App
# Run: streamlit run Combined_PF_Statutory.py
# Tabs: PF Register | Statutory Compliance (ESI / PT / TDS / GSTR-1 / GSTR-3B)
#
# This is a single-file combined app. All logic is self-contained.
# See PF.py and statutory_extractor.py for standalone versions.

import streamlit as st
import pdfplumber
import fitz
import pandas as pd
import re
import datetime
import zipfile
import os
from pathlib import Path
from io import BytesIO

st.set_page_config(page_title="PF & Statutory Compliance Register", layout="wide")
st.title("PF & Statutory Compliance Register")
st.caption("PF Challan · ECR Return · TRRN  |  ESI · PT · TDS · GSTR-1 · GSTR-3B")

# ── shared utils ──────────────────────────────────────────────────────────────

def _read_pdf_text(file_bytes: bytes) -> str:
    pdf = fitz.open(stream=file_bytes, filetype="pdf")
    return "\n".join(page.get_text(sort=True) for page in pdf)

def _g(pattern, text, flags=re.I):
    m = re.search(pattern, text, flags)
    return m.group(1).strip() if m else ""

g = _g

def g_last(pattern, text, flags=re.I):
    """Like g(), but returns the LAST match — for "Grand Total"-style fields
    where a multi-section document can repeat the label for per-section
    subtotals before the true final total."""
    matches = list(re.finditer(pattern, text, flags))
    return matches[-1].group(1).strip() if matches else ""

def _normalize_period(raw):
    if not raw:
        return ""
    raw = str(raw).strip()
    cleaned = re.sub(r'\s*[-–]\s*', '-', raw)
    for candidate in [cleaned, cleaned.title(), raw, raw.title()]:
        for fmt in ["%b-%y", "%b-%Y", "%B-%Y", "%B-%y", "%m-%Y", "%B"]:
            try:
                dt = datetime.datetime.strptime(candidate, fmt)
                return candidate.title() if fmt == "%B" else dt.strftime("%b-%Y")
            except Exception:
                pass
    return raw

normalize_period = _normalize_period

_MONTH_NUM = {
    1: "January", 2: "February", 3: "March", 4: "April",
    5: "May", 6: "June", 7: "July", 8: "August",
    9: "September", 10: "October", 11: "November", 12: "December",
}

def period_from_filename(file_name):
    base = os.path.splitext(os.path.basename(file_name))[0]
    m = re.search(r'[_\-\s](\d{2})(\d{4})(?:[_\-\s]|$)', base)
    if not m:
        m = re.search(r'(\d{2})(\d{4})', base)
    if m:
        mm, yyyy = int(m.group(1)), int(m.group(2))
        if 1 <= mm <= 12 and 2000 <= yyyy <= 2099:
            fy = f"{yyyy}-{str(yyyy+1)[2:]}" if mm >= 4 else f"{yyyy-1}-{str(yyyy)[2:]}"
            return _MONTH_NUM[mm], fy
    return "", ""

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
    # Full implementation in PF.py — abbreviated key extraction here
    lines = [l.strip() for l in text.splitlines()]
    kv = {}
    for i, line in enumerate(lines):
        m = re.match(r'^(.+?)\s*:\s*(.+)$', line)
        if m:
            kv[m.group(1).strip().lower()] = m.group(2).strip()
    def fv(*labels):
        for lbl in labels:
            v = kv.get(lbl.lower(), "")
            if v: return v
        return ""
    return {
        "File Name": file_name,
        "Client Name": fv("establishment name"),
        "Establishment ID": fv("establishment id"),
        "TRRN No": fv("trrn no", "trrn number", "trrn"),
        "Challan Status": fv("challan status"),
        "Wage Month": _normalize_period(fv("wage month")),
        "Total Amount (Rs)": fv("total amount (rs)", "total amount(rs)"),
        "Payment Date": fv("payment date"),
        "Bank": fv("bank name", "bank"),
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

def detect_statutory_type(text):
    if re.search(r"ITNS[\s\w.:]*281", text, re.I): return "TDS"
    if re.search(r"FORM\s*GSTR-2B|Form GSTR-2B", text): return "GSTR2B"
    if re.search(r"FORM\s*GSTR-3B|Form GSTR-3B", text): return "GSTR3B"
    if re.search(r"FORM\s*GSTR-1|Details of outward supplies", text): return "GSTR1"
    if re.search(r"FORM\s*5-A|PROFESSIONAL TAX RETURNS", text, re.I): return "PT"
    if re.search(r"Employer.s Code No|esic\.in|Employees.?\s*State Insurance", text, re.I): return "ESI"
    return None

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

def extract_tds(fn, text):
    return {"File Name": fn,
            "Client Name": g(r"Name of Deductor\s*:\s*([^\n]+)", text) or g(r"Name of Tax Payer\s*:\s*([^\n]+)", text),
            "TAN": g(r"TAN\s*:\s*([A-Z]{4}\d{5}[A-Z])", text),
            "Assessment Year": g(r"Assessment Year\s*:\s*([\d-]+)", text),
            "Financial Year": g(r"Financial Year\s*:\s*([\d-]+)", text),
            "Amount (Rs)": g(r"Amount\s*\(in Rs\.\)\s*:\s*[^\d]*([\d,]+)", text),
            "Date of Deposit": g(r"Date of Deposit\s*:\s*([\w-]+)", text),
            "CIN": g(r"CIN\s*:\s*([A-Z0-9]+)", text)}

def extract_gstr1(fn, text):
    gstin = g(r"(?:1\s+)?GSTIN\s+(\w{15})", text)
    client = g(r"Legal name of the registered person\s+([^\n]+)", text)
    fin_yr = g(r"Financial year\s+([\d\-]+)", text)
    period = g(r"Tax period\s+([A-Za-z]+)", text)
    if period and period.title() not in _MONTH_NUM.values(): period = ""
    fn_p, fn_fy = period_from_filename(fn)
    tl_m = re.search(
        r"Total Liability.*?\b([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})",
        text, re.S | re.I)
    tl = tuple(v.replace(",", "") for v in tl_m.groups()) if tl_m else ("", "", "", "", "")
    return {"File Name": fn, "Client Name": client, "GSTIN": gstin,
            "Financial Year": fin_yr or fn_fy, "Tax Period": period or fn_p,
            "ARN": g(r"\bARN\b\s+([A-Z0-9]+)", text),
            "TL Taxable Value": tl[0], "TL IGST": tl[1], "TL CGST": tl[2], "TL SGST": tl[3]}

def extract_gstr3b(fn, text):
    gstin = g(r"GSTIN of the supplier\s+(\w{15})", text) or g(r"GSTIN\s+(\w{15})", text)
    client = g(r"Legal name of the registered person\s+([^\n]+)", text)
    fin_yr = g(r"^Year\s+([\d\-]+)", text, re.M) or g(r"\bYear\s+(20\d{2}[-–]\d{2,4})\b", text)
    period = g(r"^Period\s+([A-Za-z]+)", text, re.M) or g(r"\bPeriod\s+([A-Z][a-z]+)\b", text)
    if period and period.title() not in _MONTH_NUM.values(): period = ""
    fn_p, fn_fy = period_from_filename(fn)
    def row5(marker):
        pat = re.escape(marker) + r"[^0-9]*?([\d,]+\.\d{2})[^0-9]*?([\d,]+\.\d{2})[^0-9]*?([\d,]+\.\d{2})[^0-9]*?([\d,]+\.\d{2})[^0-9]*?([\d,]+\.\d{2})"
        m = re.search(pat, text, re.S | re.I)
        return tuple(v.replace(",", "") for v in m.groups()) if m else ("", "", "", "", "")
    t31a = row5("(a) Outward taxable supplies (other than zero rated")
    return {"File Name": fn, "Client Name": client, "GSTIN": gstin,
            "Financial Year": fin_yr or fn_fy, "Tax Period": period or fn_p,
            "ARN": g(r"\bARN\b\s+([A-Z0-9]+)", text),
            "3.1(a) Taxable Value": t31a[0], "3.1(a) IGST": t31a[1],
            "3.1(a) CGST": t31a[2], "3.1(a) SGST": t31a[3]}

def compute_recon(gstr1_rows, gstr3b_rows):
    def to_f(val):
        try: return float(str(val).replace(",", "")) if val not in ("", None) else 0.0
        except ValueError: return 0.0
    idx3b, dup_keys = {}, set()
    for r in gstr3b_rows:
        key = (str(r.get("GSTIN", "")).strip().upper(), normalize_period(r.get("Tax Period", "")))
        if key in idx3b:
            dup_keys.add(key)  # e.g. original + amended return for same GSTIN/period
        idx3b[key] = r  # last-processed file wins for a duplicate key
    results, matched = [], set()
    for r1 in gstr1_rows:
        gstin = str(r1.get("GSTIN", "")).strip().upper()
        period = normalize_period(r1.get("Tax Period", ""))
        key = (gstin, period)
        r3b = idx3b.get(key)
        g1_tv = to_f(r1.get("TL Taxable Value"))
        g1_tax = to_f(r1.get("TL IGST")) + to_f(r1.get("TL CGST")) + to_f(r1.get("TL SGST"))
        g3_tv = to_f(r3b.get("3.1(a) Taxable Value")) if r3b else 0.0
        g3_tax = (to_f(r3b.get("3.1(a) IGST")) + to_f(r3b.get("3.1(a) CGST")) + to_f(r3b.get("3.1(a) SGST"))) if r3b else 0.0
        status = "Only in GSTR-1" if not r3b else ("Matched" if abs(g1_tv - g3_tv) <= 1.0 and abs(g1_tax - g3_tax) <= 1.0 else "Mismatch")
        if r3b: matched.add(key)
        results.append({"GSTIN": gstin, "Tax Period": period,
                        "GSTR-1 Taxable": g1_tv, "GSTR-3B Taxable": g3_tv, "Diff Taxable": round(g1_tv - g3_tv, 2),
                        "GSTR-1 Tax": g1_tax, "GSTR-3B Tax": g3_tax, "Diff Tax": round(g1_tax - g3_tax, 2),
                        "Status": status})
    return results, sorted(dup_keys)

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
    pf_uploaded = st.file_uploader(
        "Upload PF Challan / TRRN PDFs", type="pdf", accept_multiple_files=True, key="pf_uploader"
    )
    if st.button("Generate PF Register", type="primary", key="pf_gen"):
        if not pf_uploaded:
            st.error("Please upload PF PDFs")
        else:
            challan_rows, detail_tables, trrn_rows, return_rows, pf_failed = [], [], [], [], []
            progress = st.progress(0)
            for idx, file in enumerate(pf_uploaded):
                file_name = file.name
                file_bytes = file.read()
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
                progress.progress((idx + 1) / len(pf_uploaded))

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

    col1, col2 = st.columns(2)
    with col1:
        stat_pdfs = st.file_uploader("Upload PDFs", type="pdf", accept_multiple_files=True, key="stat_pdf")
    with col2:
        stat_zips = st.file_uploader("Upload ZIPs", type="zip", accept_multiple_files=True, key="stat_zip")

    col_b1, col_b2, col_b3 = st.columns(3)
    with col_b1:
        stat_btn = st.button("Extract All", type="primary", use_container_width=True, key="stat_btn")
    with col_b2:
        if st.button("Clear", use_container_width=True, key="stat_clear"):
            for _k in _STAT_KEYS: st.session_state[_k] = []
            st.session_state.stat_failed = []
            st.session_state.stat_recon = []
            st.success("Cleared.")
    with col_b3:
        if st.button("Reset", use_container_width=True, key="stat_reset"):
            for _k in _STAT_KEYS: st.session_state[_k] = []
            st.session_state.stat_failed = []
            st.session_state.stat_recon = []
            st.rerun()

    if stat_btn:
        for _k in _STAT_KEYS: st.session_state[_k] = []
        st.session_state.stat_failed = []
        st.session_state.stat_recon = []

        all_pdfs = [(f.name, f.read()) for f in (stat_pdfs or [])]
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
