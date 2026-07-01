# PF Consolidated Register
# Auto-detects Challan ECR vs TRRN Payment Confirmation PDFs
# Run: streamlit run PF.py

import streamlit as st
import pdfplumber
import fitz
import pandas as pd
import re
import datetime
from pathlib import Path
from io import BytesIO


def _read_fitz_text(file_bytes: bytes) -> str:
    pdf = fitz.open(stream=file_bytes, filetype="pdf")
    return "\n".join(page.get_text(sort=True) for page in pdf)


def _g(pattern, text, flags=re.I):
    m = re.search(pattern, text, flags)
    return m.group(1).strip() if m else ""


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


def _detect_pf_type(text: str) -> str:
    if re.search(r"Payment\s+Confirmation\s+Receipt", text, re.I):
        return "TRRN"
    if re.search(
        r"COMBINED\s+CHALLAN\s+OF\s+A/C"
        r"|CHALLAN\s+FOR\s+WAGE\s+MONTH"
        r"|Dues\s+for\s+the\s+wage\s+month"
        r"|system\s+generated\s+challan",
        text, re.I
    ):
        return "CHALLAN"
    if re.search(
        r"ELECTRONIC\s+CHALLAN\s+CUM\s+RETURN"
        r"|Return\s+Month"
        r"|Salary\s+Disbursement\s+Date"
        r"|ECR\s+Type\b",
        text, re.I
    ):
        return "RETURN"
    return "CHALLAN"


def _detect_pf_type_plumber(file_path) -> str:
    _CHALLAN = re.compile(
        r"COMBINED\s+CHALLAN\s+OF\s+A/C|CHALLAN\s+FOR\s+WAGE\s+MONTH"
        r"|Dues\s+for\s+the\s+wage\s+month|system\s+generated\s+challan", re.I)
    _RETURN = re.compile(
        r"ELECTRONIC\s+CHALLAN\s+CUM\s+RETURN|Return\s+Month"
        r"|Salary\s+Disbursement\s+Date|ECR\s+Type\b", re.I)
    try:
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages[:3]:
                pg_text = page.extract_text() or ""
                if re.search(r"Payment\s+Confirmation\s+Receipt", pg_text, re.I):
                    return "TRRN"
                if _CHALLAN.search(pg_text):
                    return "CHALLAN"
                if _RETURN.search(pg_text):
                    return "RETURN"
                for tbl in (page.extract_tables() or []):
                    cell_text = " ".join(str(c or "") for row in tbl for c in row)
                    if re.search(r"Payment\s+Confirmation\s+Receipt", cell_text, re.I):
                        return "TRRN"
                    if _CHALLAN.search(cell_text):
                        return "CHALLAN"
                    if _RETURN.search(cell_text):
                        return "RETURN"
    except Exception:
        pass
    return ""


def extract_pf_trrn(file_name: str, text: str) -> dict:
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
        total_amt = _g(r"Total Amount\s*\(Rs\)\s*[:\s]+([\d,]+)", text, re.I)
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
    if is_new:
        comp_m = re.search(r"^\s*Name\s*:\s*(.+)", text, re.I | re.M)
        company = comp_m.group(1).strip() if comp_m else ""
        code_m = re.search(r"^\s*Code\s*:\s*([\w/.\-]+)", text, re.I | re.M)
        estab_code = code_m.group(1).strip() if code_m else ""
        wm_m = re.search(r"CHALLAN FOR WAGE MONTH\s*[:\-]\s*(\w+\s+\d{4})", text, re.I)
        month = wm_m.group(1).replace(" ", "") if wm_m else ""
        trrn_header = ""
        tm = re.search(r"TRRN\s*[:\s]+(\d+)", text, re.I)
        trrn_header = tm.group(1) if tm else ""
        gm = re.search(r"Generated\s+On\s+(\d{2}-[A-Za-z]+-\d{4})", text, re.I)
        generated_on = gm.group(1) if gm else ""
        bm = re.search(r"Bank\s+Name\s*:\s*([^\n]+)", text, re.I)
        bank_name = bm.group(1).strip() if bm else ""
        cm = re.search(r"CRN\s*:\s*([A-Z0-9]+)", text, re.I)
        crn = cm.group(1) if cm else ""
        dm = re.search(r"\bDate\s*:\s*(\d{2}-[A-Za-z]+-\d{4})", text, re.I)
        payment_date = dm.group(1) if dm else ""
    else:
        comp_m = re.search(r"Establishment Code & Name\s+\S+\s+(.*?)(?=\nAddress|\n\n|$)", text, re.DOTALL)
        company = comp_m.group(1).strip().splitlines()[0].strip() if comp_m else ""
        ec_m = re.search(r"Establishment Code & Name\s+(\S+)", text, re.I)
        estab_code = ec_m.group(1).strip() if ec_m else ""
        wm_m = (
            re.search(r"Dues for the wage month of\s+(\w+\s*\d{4})", text, re.I) or
            re.search(r"wage month\s*[:\-]?\s*(\w+\s*\d{4})", text, re.I)
        )
        month = wm_m.group(1).replace(" ", "") if wm_m else ""
        trrn_header = generated_on = bank_name = crn = payment_date = ""

    subs3 = re.search(r"Total Subscribers[:\s]+(\d+)[:\s]+(\d+)[:\s]+(\d+)", text)
    subs1 = re.search(r"Total Subscribers\s*:\s*(\d+)", text)
    wages = re.search(r"Total Wages[:\s]+([\d,]+)[:\s]+([\d,]+)[:\s]+([\d,]+)", text)
    epf_subs = subs3.group(1) if subs3 else (subs1.group(1) if subs1 else "")
    eps_subs = subs3.group(2) if subs3 else ""
    edli_subs = subs3.group(3) if subs3 else ""
    epf_wages = wages.group(1) if wages else ""
    eps_wages = wages.group(2) if wages else ""
    edli_wages = wages.group(3) if wages else ""

    detail_table_df = None
    for t in tables:
        if not t or not isinstance(t, list):
            continue
        header = [str(c or "").strip() for c in (t[0] or [])]
        header_text = " ".join(header).upper()
        if "PARTICULARS" in header_text and "A/C" in header_text:
            cleaned = [[c if c is not None else "" for c in row] for row in t[1:] if row]
            if cleaned:
                df = pd.DataFrame(cleaned, columns=header)
                p_col = next((col for col in df.columns if "PARTICULARS" in str(col).upper()), None)
                if p_col:
                    df_text = " ".join(df[p_col].astype(str)).upper()
                    if any(kw in df_text for kw in ("ADMINISTRATION", "EMPLOYER", "EMPLOYEE", "7Q", "14B")):
                        detail_table_df = df
                        break

    challan_date = generated_on
    if not challan_date:
        cd_m = re.search(r"system generated challan on\s+(\d{2}-[A-Z]{3}-\d{4})", text, re.I)
        if cd_m:
            challan_date = cd_m.group(1)

    gt_m = re.search(r"Grand Total\s*[:\-]?[^\d]*([\d,]+)\s*$", text, re.I | re.MULTILINE)
    grand_total = gt_m.group(1) if gt_m else ""

    return {
        "Company": company, "Establishment Code": estab_code, "Month": month,
        "TRRN No": trrn_header, "Total Subscribers": epf_subs,
        "EPF Subscribers": epf_subs, "EPS Subscribers": eps_subs, "EDLI Subscribers": edli_subs,
        "EPF Wages": epf_wages, "EPS Wages": eps_wages, "EDLI Wages": edli_wages,
        "Bank Name": bank_name, "CRN": crn, "Payment Date": payment_date,
        "Challan Date": challan_date, "Grand Total": grand_total,
        "Detail Table": detail_table_df, "Charges Table": detail_table_df,
    }


# ── UI ────────────────────────────────────────────────────────────────────────

st.set_page_config(page_title="PF Register", layout="wide")
st.title("PF Consolidated Register")
st.caption("Auto-detects Challan ECR vs TRRN Payment Confirmation PDFs")

input_tab1, input_tab2 = st.tabs(["Upload Files", "Select Folder"])
source = "upload"
files_to_process = None

with input_tab1:
    uploaded_files = st.file_uploader(
        "Upload PF Challan / TRRN PDFs", type="pdf", accept_multiple_files=True
    )
    if uploaded_files:
        source = "upload"
        files_to_process = uploaded_files

with input_tab2:
    folder_path = st.text_input(
        "Enter folder path", placeholder="C:/Users/YourName/Documents/PF_PDFs"
    )
    if folder_path:
        folder = Path(folder_path)
        if folder.exists() and folder.is_dir():
            pdf_files = [f for f in folder.rglob("*") if f.is_file() and f.suffix.lower() == ".pdf"]
            if pdf_files:
                st.success(f"Found {len(pdf_files)} PDF file(s)")
                source = "folder"
                files_to_process = pdf_files
            else:
                st.warning("No PDF files found in this folder")
        else:
            st.error("Folder does not exist or is not a valid directory")

if st.button("Generate PF Register", type="primary"):
    if not files_to_process:
        st.error("Please upload files or select a valid folder with PDFs")
    else:
        challan_rows, detail_tables, trrn_rows, failed, detection_log = [], [], [], [], []
        progress = st.progress(0)
        total = len(files_to_process)

        for idx, file in enumerate(files_to_process):
            if source == "upload":
                file_name = file.name
                file_bytes = file.read()
                temp = Path("data") / file_name
                temp.parent.mkdir(exist_ok=True)
                temp.write_bytes(file_bytes)
                file_path = temp
            else:
                file_path = file
                file_name = file.name
                file_bytes = file_path.read_bytes()

            try:
                text = _read_fitz_text(file_bytes)
                doc_type = _detect_pf_type(text)
                if doc_type == "CHALLAN" or len(text.strip()) < 100:
                    plumber_type = _detect_pf_type_plumber(file_path)
                    if plumber_type:
                        doc_type = plumber_type
                detection_log.append((file_name, doc_type, text[:400].replace("\n", " ")))

                if doc_type == "TRRN":
                    trrn_rows.append(extract_pf_trrn(file_name, text))
                else:
                    data = extract_pf_data(file_path)
                    detail_table = data.pop("Detail Table")
                    data.pop("Charges Table", None)
                    data["File Name"] = file_name
                    challan_rows.append(data)
                    if detail_table is not None and not detail_table.empty:
                        detail_table.insert(0, "Company", data["Company"])
                        detail_table.insert(1, "Month", data["Month"])
                        detail_tables.append(detail_table)
            except Exception as e:
                failed.append(f"{file_name}: {e}")

            progress.progress((idx + 1) / total)

        parts = []
        if challan_rows:
            parts.append(f"**{len(challan_rows)}** Challan(s)")
        if trrn_rows:
            parts.append(f"**{len(trrn_rows)}** TRRN(s)")
        if failed:
            parts.append(f"**{len(failed)}** failed")

        if parts:
            st.success("Done — " + " · ".join(parts))
        else:
            st.error("No data could be extracted")

        if failed:
            with st.expander(f"⚠️ {len(failed)} failed file(s)", expanded=True):
                for msg in failed:
                    st.code(msg)

        if challan_rows or trrn_rows:
            preview_labels = []
            if challan_rows:
                preview_labels.append(f"Challan Header ({len(challan_rows)})")
            if detail_tables:
                preview_labels.append("Challan Summary")
            if trrn_rows:
                preview_labels.append(f"TRRN ({len(trrn_rows)})")

            preview_tabs = st.tabs(preview_labels)
            tab_idx = 0
            if challan_rows:
                with preview_tabs[tab_idx]:
                    st.dataframe(pd.DataFrame(challan_rows), use_container_width=True)
                tab_idx += 1
            if detail_tables:
                with preview_tabs[tab_idx]:
                    st.dataframe(pd.concat(detail_tables, ignore_index=True), use_container_width=True)
                tab_idx += 1
            if trrn_rows:
                with preview_tabs[tab_idx]:
                    st.dataframe(pd.DataFrame(trrn_rows), use_container_width=True)

            buffer = BytesIO()
            with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
                if challan_rows:
                    pd.DataFrame(challan_rows).to_excel(writer, sheet_name="Challan Header", index=False)
                if detail_tables:
                    pd.concat(detail_tables, ignore_index=True).to_excel(
                        writer, sheet_name="Challan Summary", index=False
                    )
                if trrn_rows:
                    pd.DataFrame(trrn_rows).to_excel(writer, sheet_name="TRRN", index=False)
            buffer.seek(0)
            st.download_button(
                "Download Excel", buffer,
                file_name="PF_Consolidated_Register.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

if st.button("Refresh"):
    st.rerun()
