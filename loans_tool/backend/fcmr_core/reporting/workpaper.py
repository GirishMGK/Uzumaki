"""Excel workpaper builder for audit documentation.

Generates a 4-sheet workpaper: Lead Sheet, Detailed Exceptions, TOC and TOD, Methodology.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import polars as pl
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from fcmr_core.reporting.aggregation import aggregate_exception_codes


def _sanitize_filename(text: str) -> str:
    """Remove invalid filename characters."""
    invalid = '<>:"/\\|?*'
    for char in invalid:
        text = text.replace(char, "_")
    return text


def build_workpaper(
    engagement: dict,
    run: dict,
    wide_csv_path: Path,
    sample_records: list[dict],
    output_dir: Path,
) -> Path:
    """Build a 4-sheet Excel workpaper.

    Args:
        engagement: Engagement dict from store.
        run: Run dict from store.
        wide_csv_path: Path to wide exception CSV.
        sample_records: List of sampled records from select_sample().
        output_dir: Directory to save the workpaper.

    Returns:
        Path to saved workpaper .xlsx.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # Generate filename
    engagement_name = _sanitize_filename(engagement.get("name", "Engagement"))
    period_from = (engagement.get("period_from") or "").split("T")[0]
    period_to = (engagement.get("period_to") or "").split("T")[0]
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    filename = f"{engagement_name}_{period_from}_{period_to}_{timestamp}.xlsx"
    workpaper_path = output_dir / filename

    # Create workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Lead Sheet"

    # Styles
    header_fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
    header_font = Font(bold=True, color="FFFFFF", size=11)
    title_font = Font(bold=True, size=14)
    subheader_font = Font(bold=True, size=11)
    border = Border(
        left=Side(style="thin"),
        right=Side(style="thin"),
        top=Side(style="thin"),
        bottom=Side(style="thin"),
    )

    # ── Sheet 1: Lead Sheet ──
    _build_lead_sheet(
        ws,
        engagement,
        run,
        wide_csv_path,
        sample_records,
        header_fill,
        header_font,
        title_font,
        subheader_font,
        border,
    )

    # ── Sheet 2: Detailed Exceptions ──
    ws2 = wb.create_sheet("Detailed Exceptions")
    _build_detailed_exceptions_sheet(ws2, wide_csv_path, header_fill, header_font, border)

    # ── Sheet 3: TOC and TOD ──
    ws3 = wb.create_sheet("TOC and TOD")
    _build_toc_tod_sheet(ws3, sample_records, header_fill, header_font, border)

    # ── Sheet 4: Methodology ──
    ws4 = wb.create_sheet("Methodology")
    _build_methodology_sheet(
        ws4, engagement, run, wide_csv_path, sample_records, title_font, subheader_font
    )

    # Save
    wb.save(workpaper_path)
    return workpaper_path


def _build_lead_sheet(
    ws,
    engagement,
    run,
    wide_csv_path,
    sample_records,
    header_fill,
    header_font,
    title_font,
    subheader_font,
    border,
):
    """Build Lead Sheet."""
    row = 1

    # Header
    ws[f"A{row}"] = "Loan Analytics - Audit Workpaper"
    ws[f"A{row}"].font = title_font
    row += 1

    ws[f"A{row}"] = f"Engagement: {engagement.get('name', 'N/A')}"
    ws[f"A{row}"].font = subheader_font
    row += 1

    ws[f"A{row}"] = (
        f"Client: {engagement.get('client_name', 'N/A')} | Period: {engagement.get('period_from', '')} to {engagement.get('period_to', '')}"
    )
    row += 1

    ws[f"A{row}"] = f"Audit Date: {datetime.now(UTC).strftime('%Y-%m-%d')}"
    row += 2

    # Exception Summary
    ws[f"A{row}"] = "Exception Summary"
    ws[f"A{row}"].font = subheader_font
    row += 1

    try:
        df = pl.read_csv(wide_csv_path, columns=["overall_status"], infer_schema_length=0)
        status_counts = df["overall_status"].value_counts().to_dicts()
        status_dict = {d["overall_status"]: d["count"] for d in status_counts}

        total = sum(status_dict.values())
        ws[f"A{row}"] = "OK:"
        ws[f"B{row}"] = status_dict.get("OK", 0)
        ws[f"C{row}"] = f"{(status_dict.get('OK', 0) / total * 100):.1f}%" if total > 0 else "0%"
        row += 1

        ws[f"A{row}"] = "Warnings:"
        ws[f"B{row}"] = status_dict.get("WARN", 0)
        ws[f"C{row}"] = f"{(status_dict.get('WARN', 0) / total * 100):.1f}%" if total > 0 else "0%"
        row += 1

        ws[f"A{row}"] = "Errors:"
        ws[f"B{row}"] = status_dict.get("ERROR", 0)
        ws[f"C{row}"] = f"{(status_dict.get('ERROR', 0) / total * 100):.1f}%" if total > 0 else "0%"
        row += 2
    except Exception:
        row += 4

    # Verification Plan
    ws[f"A{row}"] = "Verification Plan (Top Exception Codes)"
    ws[f"A{row}"].font = subheader_font
    row += 1

    # Headers
    for col, header in enumerate(
        ["Exception Code", "Frequency", "Source Document", "Compliance Point"], 1
    ):
        cell = ws.cell(row=row, column=col, value=header)
        cell.fill = header_fill
        cell.font = header_font

    row += 1

    # Top exception codes
    exception_codes = aggregate_exception_codes(wide_csv_path, top_n=10)
    source_docs = {
        "PAN_DUPLICATE": "PAN Registration / KYC",
        "AADHAAR_DUPLICATE": "Aadhaar Card / KYC",
        "VOTER_ID_DUPLICATE": "Voter Card / KYC",
        "ADDRESS_DUPLICATE": "Sanction Letter / Address Proof",
        "BANK_ACCOUNT_DUPLICATE": "Bank Statement / Account Proof",
        "EMAIL_COMPANY_GENERIC_DOMAIN": "KYC Form / Email Verification",
        "DOB_AGE_OUT_OF_RANGE": "ID Proof / Birth Certificate",
        "BANK_ACCOUNT_INVALID_LENGTH": "Bank Statement / Account Details",
    }

    for code, count in exception_codes.items():
        ws[f"A{row}"] = code
        ws[f"B{row}"] = count
        ws[f"C{row}"] = source_docs.get(code, "SOA / KYC")
        ws[f"D{row}"] = "Risk Assessment" if "DUP" in code else "Data Quality"
        row += 1

    # Adjust column widths
    ws.column_dimensions["A"].width = 30
    ws.column_dimensions["B"].width = 12
    ws.column_dimensions["C"].width = 30
    ws.column_dimensions["D"].width = 20


def _build_detailed_exceptions_sheet(ws, wide_csv_path, header_fill, header_font, border):
    """Build Detailed Exceptions sheet."""
    try:
        df = pl.read_csv(wide_csv_path, infer_schema_length=0)
        cols_to_keep = [
            c
            for c in [
                "customer_id",
                "overall_status",
                "exception_count",
                "exception_codes",
                "exception_descriptions",
            ]
            if c in df.columns
        ]
        df = df.select(cols_to_keep)

        # Headers
        for col_idx, col_name in enumerate(cols_to_keep, 1):
            cell = ws.cell(row=1, column=col_idx, value=col_name)
            cell.fill = header_fill
            cell.font = header_font

        # Data rows
        for row_idx, row in enumerate(df.rows(), 2):
            for col_idx, value in enumerate(row, 1):
                cell = ws.cell(row=row_idx, column=col_idx, value=value)
                cell.border = border

        # Adjust column widths
        for col_idx in range(1, len(cols_to_keep) + 1):
            ws.column_dimensions[get_column_letter(col_idx)].width = 20
    except Exception:
        ws["A1"] = "Unable to load exception data"


def _build_toc_tod_sheet(ws, sample_records, header_fill, header_font, border):
    """Build Test of Controls / Test of Details sheet."""
    # Headers
    headers = [
        "Sample#",
        "Row_Index",
        "Criticality",
        "Selection_Reason",
        "Tested_By",
        "Date",
        "Sign_Off",
        "Notes",
    ]
    for col_idx, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col_idx, value=header)
        cell.fill = header_fill
        cell.font = header_font

    # Sample rows
    for sample_idx, sample in enumerate(sample_records, 2):
        ws[f"A{sample_idx}"] = sample_idx - 1
        ws[f"B{sample_idx}"] = sample["row_index"]
        ws[f"C{sample_idx}"] = sample["criticality"]
        ws[f"D{sample_idx}"] = sample["selection_reason"]
        # E, F, G left blank for tester sign-off

    # Adjust column widths
    for col_idx, width in enumerate([10, 12, 12, 30, 15, 12, 12, 20], 1):
        ws.column_dimensions[get_column_letter(col_idx)].width = width


def _build_methodology_sheet(
    ws, engagement, run, wide_csv_path, sample_records, title_font, subheader_font
):
    """Build Sampling Methodology Note sheet."""
    row = 1

    ws[f"A{row}"] = "Deterministic Risk-Based Sampling Methodology"
    ws[f"A{row}"].font = title_font
    row += 2

    ws[f"A{row}"] = "Approach"
    ws[f"A{row}"].font = subheader_font
    row += 1

    methodology_text = [
        "• Stratified by exception severity (CRITICAL > HIGH > MEDIUM > LOW)",
        "• ICAI-ICFR attribute sampling table (95% confidence, 5% tolerable deviation)",
        "• Seeded random selection for reproducibility across re-runs",
        "• Each sample tagged with selection reason and criticality level",
    ]

    for text in methodology_text:
        ws[f"A{row}"] = text
        row += 1

    row += 1
    ws[f"A{row}"] = "Strata & Severity Weights"
    ws[f"A{row}"].font = subheader_font
    row += 1

    strata_desc = [
        "CRITICAL: PAN, Aadhaar, UCID inconsistencies (identity fraud risk)",
        "HIGH: Voter ID, Address, Bank Account duplicates (fraud indicators)",
        "MEDIUM: Email domain, age range, account length (data quality issues)",
        "LOW: PIN/address mismatches (lower audit impact)",
    ]

    for text in strata_desc:
        ws[f"A{row}"] = text
        row += 1

    row += 1
    ws[f"A{row}"] = "Confidence & Precision"
    ws[f"A{row}"].font = subheader_font
    row += 1

    try:
        df = pl.read_csv(wide_csv_path, infer_schema_length=0)
        population = len(df)
        exception_count = sum(1 for val in df["overall_status"] if val != "OK")
        sample_size = len(sample_records)

        ws[f"A{row}"] = f"Population: {population:,} records"
        row += 1
        ws[f"A{row}"] = (
            f"Exceptions: {exception_count:,} records ({exception_count/population*100:.1f}%)"
        )
        row += 1
        ws[f"A{row}"] = f"Sample Size: {sample_size} (from ICAI table, 95% confidence)"
        row += 1
        ws[f"A{row}"] = f"Sample Rate: {sample_size/population*100:.2f}%"
        row += 2
    except Exception:
        row += 4

    ws[f"A{row}"] = "International Standards Alignment"
    ws[f"A{row}"].font = subheader_font
    row += 1

    standards = [
        "• ICAI Audit Sampling Guidance (India)",
        "• ISA 530 Audit Sampling (IAASB - International)",
        "• RBI Know Your Customer (KYC) Guidelines",
        "• NFRA fraud-risk indicators for NBFC audits",
        "• QRB recommendations for independent audits",
    ]

    for text in standards:
        ws[f"A{row}"] = text
        row += 1

    row += 1
    ws[f"A{row}"] = "Fraud-Risk Focus"
    ws[f"A{row}"].font = subheader_font
    row += 1

    fraud_focus = (
        "Loan account duplication, identity fraud, KYC weaknesses, undisclosed "
        "conflicts of interest, and cash-flow anomalies are weighted highest in "
        "this sampling design. The deterministic approach ensures reproducibility "
        "and defensibility in regulatory reviews."
    )
    ws[f"A{row}"] = fraud_focus
    ws[f"A{row}"].alignment = Alignment(wrap_text=True)

    # Adjust column width
    ws.column_dimensions["A"].width = 90
