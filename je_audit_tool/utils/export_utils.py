"""
Export Utilities — generates the multi-sheet Excel audit report and working paper.
Uses xlsxwriter via pd.ExcelWriter for full formatting control.
"""
from __future__ import annotations
import io
import re
from datetime import datetime
import pandas as pd
import numpy as np


# ── Color palette ─────────────────────────────────────────────────────────────
_NAVY    = "#1e3a5f"
_CRITICAL= "#FF3B3B"
_HIGH    = "#FF8C00"
_MEDIUM  = "#F5A623"
_LOW     = "#4CAF50"
_LIGHT   = "#f0f4f8"


def to_excel_report(
    df_orig: pd.DataFrame,
    exceptions_df: pd.DataFrame,
    benford_df: pd.DataFrame | None,
    chi2_df: pd.DataFrame | None,
    audit_params: dict,
    col_map: dict,
) -> bytes:
    """
    Build full Excel audit report.
    Sheets: Exception Transactions | Summary by Risk | Dashboard Data | Benford Results | Audit Working Paper
    Returns bytes for st.download_button.
    """
    output = io.BytesIO()

    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        wb = writer.book

        # ── shared formats ────────────────────────────────────────────────────
        fmt = _build_formats(wb)

        _sheet_exceptions(writer, wb, fmt, df_orig, exceptions_df, audit_params)
        _sheet_summary(writer, wb, fmt, exceptions_df, audit_params)
        _sheet_dashboard(writer, wb, fmt, df_orig, exceptions_df, col_map)
        _sheet_benford(writer, wb, fmt, benford_df, chi2_df)
        _sheet_working_paper(writer, wb, fmt, df_orig, exceptions_df, audit_params)

    output.seek(0)
    return output.getvalue()


# ── Format builder ────────────────────────────────────────────────────────────

def _build_formats(wb) -> dict:
    def f(**kw):
        return wb.add_format(kw)

    return {
        "title":     f(bold=True, font_size=14, font_color=_NAVY),
        "subtitle":  f(font_size=10, font_color="#555555"),
        "hdr":       f(bold=True, bg_color=_NAVY, font_color="white", border=1, font_size=10, align="center", valign="vcenter"),
        "normal":    f(border=1, font_size=9, text_wrap=True, valign="top"),
        "label":     f(bold=True, font_size=10, bg_color=_LIGHT, border=1),
        "value":     f(font_size=10, border=1, text_wrap=True),
        "critical":  f(bg_color=_CRITICAL, font_color="white", border=1, font_size=9, bold=True),
        "high":      f(bg_color=_HIGH, font_color="white", border=1, font_size=9),
        "medium":    f(bg_color=_MEDIUM, border=1, font_size=9),
        "low":       f(bg_color=_LOW, font_color="white", border=1, font_size=9),
        "section":   f(bold=True, font_size=12, font_color="white", bg_color=_NAVY, border=1),
        "conclusion":f(bold=True, font_size=11, font_color=_NAVY, bg_color="#e8f0fe", border=2, text_wrap=True),
        "money":     f(num_format='₹#,##0.00', border=1, font_size=9),
    }


def _risk_fmt(fmt: dict, risk_level: str):
    mapping = {"Critical": fmt["critical"], "High": fmt["high"],
                "Medium": fmt["medium"], "Low": fmt["low"]}
    return mapping.get(str(risk_level), fmt["normal"])


# ── Sheet 1: Exception Transactions ──────────────────────────────────────────

def _sheet_exceptions(writer, wb, fmt, df_orig, exceptions_df, audit_params):
    ws = wb.add_worksheet("Exception Transactions")
    writer.sheets["Exception Transactions"] = ws

    ws.write(0, 0, "Journal Entry Testing — Exception Transactions", fmt["title"])
    ws.write(1, 0,
             f"Report: {datetime.now():%d-%b-%Y %H:%M}  |  "
             f"Period: {audit_params.get('start_date','N/A')} to {audit_params.get('end_date','N/A')}  |  "
             f"Total: {len(exceptions_df):,} exceptions",
             fmt["subtitle"])

    if exceptions_df.empty:
        ws.write(3, 0, "No exceptions identified.", fmt["subtitle"])
        return

    merged = exceptions_df.merge(
        df_orig.reset_index().rename(columns={"index": "original_index"}),
        on="original_index", how="left",
    )

    priority = ["Exception Category", "Exception Type", "Risk Level", "Risk Score", "Detail"]
    other = [c for c in merged.columns if c not in priority + ["original_index"]]
    cols = [c for c in priority + other if c in merged.columns]
    merged = merged[cols]

    for ci, col_name in enumerate(cols):
        ws.write(3, ci, col_name, fmt["hdr"])
        ws.set_column(ci, ci, max(16, len(str(col_name)) + 3))

    for ri, row in enumerate(merged.itertuples(index=False), start=4):
        rl = getattr(row, "Risk_Level", None) or getattr(row, "Risk Level", "Low")
        rf = _risk_fmt(fmt, rl)
        for ci, val in enumerate(row):
            _safe_write(ws, ri, ci, val, rf)

    ws.freeze_panes(4, 3)
    ws.autofilter(3, 0, 3 + len(merged), len(cols) - 1)


# ── Sheet 2: Summary by Risk ──────────────────────────────────────────────────

def _sheet_summary(writer, wb, fmt, exceptions_df, audit_params):
    ws = wb.add_worksheet("Summary by Risk")
    writer.sheets["Summary by Risk"] = ws

    ws.write(0, 0, "Exception Summary by Risk Level & Category", fmt["title"])
    ws.write(1, 0, f"Materiality: ₹{audit_params.get('overall_materiality',0):,.0f}  |  "
             f"Performance Materiality: ₹{audit_params.get('performance_materiality',0):,.0f}  |  "
             f"Trivial: ₹{audit_params.get('trivial_threshold',0):,.0f}", fmt["subtitle"])
    ws.set_column(0, 0, 48)
    ws.set_column(1, 5, 22)

    if exceptions_df.empty:
        ws.write(3, 0, "No exceptions found.", fmt["subtitle"])
        return

    summary = (
        exceptions_df
        .groupby(["Exception Category", "Exception Type", "Risk Level"])
        .agg(Count=("original_index", "count"),
             Unique_Txns=("original_index", "nunique"),
             Total_Score=("Risk Score", "sum"))
        .reset_index()
        .sort_values(["Total_Score", "Count"], ascending=[False, False])
    )

    headers = ["Category", "Exception Type", "Risk Level", "Count", "Unique Transactions", "Total Risk Score"]
    for ci, h in enumerate(headers):
        ws.write(3, ci, h, fmt["hdr"])

    for ri, row in enumerate(summary.itertuples(index=False), start=4):
        rf = _risk_fmt(fmt, row[2])
        for ci, val in enumerate(row):
            _safe_write(ws, ri, ci, val, rf)

    ws.freeze_panes(4, 0)
    ws.autofilter(3, 0, 3 + len(summary), len(headers) - 1)


# ── Sheet 3: Dashboard Data ───────────────────────────────────────────────────

def _sheet_dashboard(writer, wb, fmt, df_orig, exceptions_df, col_map):
    ws = wb.add_worksheet("Dashboard Data")
    writer.sheets["Dashboard Data"] = ws

    ws.write(0, 0, "Dashboard & Analytics Data", fmt["title"])
    ws.set_column(0, 5, 28)

    row = 3

    # Exceptions by category
    ws.write(row, 0, "Exceptions by Category", fmt["section"])
    ws.write(row, 1, "", fmt["section"])
    ws.write(row, 2, "", fmt["section"])
    row += 1
    for h, c in [("Category", 0), ("Count", 1), ("Unique Transactions", 2)]:
        ws.write(row, c, h, fmt["hdr"])
    row += 1

    if not exceptions_df.empty:
        cat_s = (exceptions_df.groupby("Exception Category")
                 .agg(Count=("original_index", "count"),
                      Unique_Txns=("original_index", "nunique"))
                 .reset_index().sort_values("Count", ascending=False))
        for _, r in cat_s.iterrows():
            ws.write(row, 0, r["Exception Category"], fmt["normal"])
            ws.write(row, 1, int(r["Count"]), fmt["normal"])
            ws.write(row, 2, int(r["Unique_Txns"]), fmt["normal"])
            row += 1

    row += 2

    # Risk level distribution
    ws.write(row, 0, "Risk Level Distribution", fmt["section"])
    ws.write(row, 1, "", fmt["section"])
    row += 1
    ws.write(row, 0, "Risk Level", fmt["hdr"])
    ws.write(row, 1, "Count", fmt["hdr"])
    row += 1

    if not exceptions_df.empty:
        rl_s = exceptions_df["Risk Level"].value_counts().reset_index()
        rl_s.columns = ["Risk Level", "Count"]
        for _, r in rl_s.iterrows():
            rf = _risk_fmt(fmt, r["Risk Level"])
            ws.write(row, 0, r["Risk Level"], rf)
            ws.write(row, 1, int(r["Count"]), rf)
            row += 1


# ── Sheet 4: Benford Results ──────────────────────────────────────────────────

def _sheet_benford(writer, wb, fmt, benford_df, chi2_df):
    ws = wb.add_worksheet("Benford Results")
    writer.sheets["Benford Results"] = ws

    ws.write(0, 0, "Benford's Law Analysis — First-Digit Distribution", fmt["title"])
    ws.write(1, 0, "Compares observed digit frequency against expected Benford's distribution.", fmt["subtitle"])
    ws.set_column(0, 7, 22)

    if benford_df is None or benford_df.empty:
        ws.write(3, 0, "Benford analysis not run or insufficient data (minimum 100 rows required).", fmt["subtitle"])
        return

    # Chi-square summary table
    if chi2_df is not None and not chi2_df.empty:
        ws.write(3, 0, "Chi-Square Test Summary", fmt["section"])
        ws.write(3, 1, "", fmt["section"])
        for ri, row in enumerate(chi2_df.itertuples(index=False), start=4):
            ws.write(ri, 0, str(row[0]), fmt["label"])
            ws.write(ri, 1, str(row[1]), fmt["value"])

    row_start = 4 + (len(chi2_df) if chi2_df is not None else 0) + 2

    ws.write(row_start, 0, "Digit-Level Analysis", fmt["section"])
    for c in range(len(benford_df.columns)):
        ws.write(row_start, c, "", fmt["section"])
    row_start += 1

    for ci, col_name in enumerate(benford_df.columns):
        ws.write(row_start, ci, col_name, fmt["hdr"])

    for ri, row in enumerate(benford_df.itertuples(index=False), start=row_start + 1):
        flagged = str(row[-1]).upper() == "YES"
        rf = fmt["high"] if flagged else fmt["normal"]
        for ci, val in enumerate(row):
            _safe_write(ws, ri, ci, val, rf)


# ── Sheet 5: Audit Working Paper ─────────────────────────────────────────────

def _sheet_working_paper(writer, wb, fmt, df_orig, exceptions_df, audit_params):
    ws = wb.add_worksheet("Audit Working Paper")
    writer.sheets["Audit Working Paper"] = ws

    ws.set_column(0, 0, 42)
    ws.set_column(1, 1, 65)

    def w(r, lbl, val):
        ws.write(r, 0, lbl, fmt["label"])
        ws.write(r, 1, str(val), fmt["value"])
        return r + 1

    row = 0
    ws.merge_range(row, 0, row, 1, "JOURNAL ENTRY TESTING — AUDIT WORKING PAPER", fmt["section"])
    ws.set_row(row, 22)
    row += 2

    ws.merge_range(row, 0, row, 1, "ENGAGEMENT DETAILS", fmt["section"])
    row += 1
    row = w(row, "Client Name",            audit_params.get("client_name", "____________________"))
    row = w(row, "Financial Year",          audit_params.get("financial_year", "FY 2025-26"))
    row = w(row, "Audit Period",            f"{audit_params.get('start_date','N/A')} to {audit_params.get('end_date','N/A')}")
    row = w(row, "Prepared By",             audit_params.get("prepared_by", "____________________"))
    row = w(row, "Reviewed By",             audit_params.get("reviewed_by", "____________________"))
    row = w(row, "Date of Report",          datetime.now().strftime("%d-%b-%Y"))
    row += 1

    ws.merge_range(row, 0, row, 1, "MATERIALITY PARAMETERS", fmt["section"])
    row += 1
    row = w(row, "Overall Materiality",       f"₹{audit_params.get('overall_materiality', 0):,.0f}")
    row = w(row, "Performance Materiality",   f"₹{audit_params.get('performance_materiality', 0):,.0f}")
    row = w(row, "Trivial / Clearly Trivial", f"₹{audit_params.get('trivial_threshold', 0):,.0f}")
    row = w(row, "Approval Limit",            f"₹{audit_params.get('approval_limit', 0):,.0f}")
    row += 1

    ws.merge_range(row, 0, row, 1, "JE TESTING SUMMARY", fmt["section"])
    row += 1
    row = w(row, "Total JE Lines Analysed",       f"{len(df_orig):,}")

    if not exceptions_df.empty:
        total_exc   = len(exceptions_df)
        unique_txns = exceptions_df["original_index"].nunique()
        exc_rate    = round(unique_txns / max(len(df_orig), 1) * 100, 1)
        high_ct     = len(exceptions_df[exceptions_df["Risk Level"].isin(["High", "Critical"])])
        crit_ct     = len(exceptions_df[exceptions_df["Risk Level"] == "Critical"])

        row = w(row, "Total Exceptions Identified",   f"{total_exc:,}")
        row = w(row, "Unique Transactions Flagged",   f"{unique_txns:,}")
        row = w(row, "Exception Rate",                f"{exc_rate}%")
        row = w(row, "High + Critical Exceptions",    f"{high_ct:,}")
        row = w(row, "Critical Exceptions",           f"{crit_ct:,}")
        row = w(row, "Exception Categories Triggered",f"{exceptions_df['Exception Category'].nunique()}")
    row += 1

    ws.merge_range(row, 0, row, 1, "EXCEPTION CATEGORY BREAKDOWN", fmt["section"])
    row += 1
    if not exceptions_df.empty:
        for cat, g in exceptions_df.groupby("Exception Category"):
            row = w(row, cat,
                    f"{len(g):,} exceptions  |  {g['original_index'].nunique():,} unique transactions")
    row += 2

    # Audit conclusion
    ws.merge_range(row, 0, row, 1, "AUDIT CONCLUSION", fmt["section"])
    row += 1

    if not exceptions_df.empty:
        exc_rate = round(exceptions_df["original_index"].nunique() / max(len(df_orig), 1) * 100, 1)
        high_ct  = len(exceptions_df[exceptions_df["Risk Level"].isin(["High", "Critical"])])

        if high_ct > 50 or exc_rate > 10:
            conclusion = (
                f"SIGNIFICANT RISK IDENTIFIED — {high_ct} high/critical exceptions noted across "
                f"{exc_rate}% of transactions. Material misstatement risk is elevated. "
                "Extended substantive procedures recommended. Obtain management representation "
                "and explanations for all Critical-flagged entries before finalising."
            )
        elif high_ct > 10 or exc_rate > 3:
            conclusion = (
                f"MODERATE RISK — {high_ct} high/critical exceptions with {exc_rate}% exception rate. "
                "Follow-up testing required on flagged items. Obtain management explanations. "
                "Consider expanding sample for affected account categories."
            )
        else:
            conclusion = (
                f"LOW RISK — Limited exceptions ({exc_rate}% rate). "
                "JE testing procedures appear broadly satisfactory. "
                "Standard follow-up procedures apply to any remaining open items."
            )
    else:
        conclusion = "No exceptions identified through automated JE testing procedures."

    ws.merge_range(row, 0, row + 3, 1, conclusion, fmt["conclusion"])
    ws.set_row(row, 90)
    row += 5

    ws.write(row, 0, "Prepared By", fmt["label"])
    ws.write(row, 1, f"{audit_params.get('prepared_by', '____________________')} — {datetime.now():%d-%b-%Y}", fmt["value"])
    row += 1
    ws.write(row, 0, "Reviewed By", fmt["label"])
    ws.write(row, 1, f"{audit_params.get('reviewed_by', '____________________')} — ____________________", fmt["value"])


# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe_write(ws, row, col, val, fmt):
    """Write a cell value, coercing types to avoid xlsxwriter errors."""
    try:
        import numpy as np
        if val is None or (isinstance(val, float) and np.isnan(val)):
            ws.write(row, col, "", fmt)
        elif isinstance(val, (np.integer,)):
            ws.write(row, col, int(val), fmt)
        elif isinstance(val, (np.floating,)):
            ws.write(row, col, float(val), fmt)
        elif hasattr(val, "isoformat"):
            ws.write(row, col, str(val), fmt)
        else:
            ws.write(row, col, val, fmt)
    except Exception:
        ws.write(row, col, str(val), fmt)
