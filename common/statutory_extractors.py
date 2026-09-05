"""PDF-based extractors for filed GST returns (GSTR-1/GSTR-3B) and deposited
TDS challans, plus the GSTR-1-vs-GSTR-3B reconciliation.

Extracted out of Combined_PF_Statutory.py for the same reason period_utils.py
was: these are pure functions over already-extracted PDF text, with nothing
Streamlit-specific about them, but Combined_PF_Statutory.py is a Streamlit
script (it calls st.set_page_config() at import time), so nothing outside it
could import extract_gstr1()/extract_gstr3b()/extract_tds()/compute_recon()
directly without triggering that. tally_tool/reports/ needs exactly these
functions for its GST/TDS "as per books" vs "as per filed return" reconciliation
(Phase 2) -- pulled out here rather than duplicated.

Combined_PF_Statutory.py re-exports these under their original names so its
own behavior is unchanged -- this is a pure extraction, not a rewrite.
"""

from __future__ import annotations

import os
import re

import fitz

from .period_utils import _MONTH_NUM, normalize_period


def read_pdf_text(file_bytes: bytes) -> str:
    pdf = fitz.open(stream=file_bytes, filetype="pdf")
    return "\n".join(page.get_text(sort=True) for page in pdf)


def g(pattern, text, flags=re.I):
    m = re.search(pattern, text, flags)
    return m.group(1).strip() if m else ""


def g_last(pattern, text, flags=re.I):
    """Like g(), but returns the LAST match — for "Grand Total"-style fields
    where a multi-section document can repeat the label for per-section
    subtotals before the true final total."""
    matches = list(re.finditer(pattern, text, flags))
    return matches[-1].group(1).strip() if matches else ""


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


def detect_statutory_type(text):
    if re.search(r"ITNS[\s\w.:]*281", text, re.I): return "TDS"
    if re.search(r"FORM\s*GSTR-2B|Form GSTR-2B", text): return "GSTR2B"
    if re.search(r"FORM\s*GSTR-3B|Form GSTR-3B", text): return "GSTR3B"
    if re.search(r"FORM\s*GSTR-1|Details of outward supplies", text): return "GSTR1"
    if re.search(r"FORM\s*5-A|PROFESSIONAL TAX RETURNS", text, re.I): return "PT"
    if re.search(r"Employer.s Code No|esic\.in|Employees.?\s*State Insurance", text, re.I): return "ESI"
    return None


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
