"""Tests for the PF TRRN ("Payment Confirmation Receipt") extraction in
Combined_PF_Statutory.py -- real feedback: every such PDF must land in the
TRRN sheet, and when it carries 7Q (interest for delay) / 14B (damages for
delay) amounts alongside the base per-account Amount, those need their own
separate columns, not folded into "Total Amount (Rs)".

The previous version of extract_pf_trrn() in this file was an abbreviated
copy that dropped the per-account (Account-1/2/10/21/22) breakdown and the
7Q/14B columns entirely, even though PF.py's standalone version already had
them -- this ports that fuller implementation over and verifies it against
REAL generated PDFs (reportlab tables), not just plain-string input, since
the actual bug risk here is in how PDF table cells extract to text, not in
the regex logic in isolation.
"""
from __future__ import annotations

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

import pytest

pytest.importorskip("reportlab")

from common.statutory_extractors import read_pdf_text
from Combined_PF_Statutory import extract_pf_trrn


def _build_trrn_pdf(path: str, header_rows: list, acct_rows: list, footer_rows: list) -> None:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    styles = getSampleStyleSheet()
    elements = [Paragraph("Payment Confirmation Receipt", styles["Title"]), Spacer(1, 12)]

    t1 = Table(header_rows, colWidths=[180, 250])
    t1.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.grey)]))
    elements += [t1, Spacer(1, 12)]

    t2 = Table(acct_rows, colWidths=[180, 100, 60, 60])
    t2.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.grey)]))
    elements += [t2, Spacer(1, 12)]

    t3 = Table(footer_rows, colWidths=[180, 250])
    t3.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.grey)]))
    elements.append(t3)

    SimpleDocTemplate(path, pagesize=A4).build(elements)


def _default_acct_rows(q7=("0", "0", "0", "0", "0"), damages=("0", "0", "0", "0", "0")):
    amounts = ["702560", "22240", "364972", "22241", "0"]
    labels = ["Account-1", "Account-2", "Account-10", "Account-21", "Account-22"]
    rows = [["Accounts", "Amount (Rs)", "7Q", "14B"]]
    for lbl, amt, q, d in zip(labels, amounts, q7, damages):
        rows.append([f"{lbl} Amount (Rs) :", amt, q, d])
    return rows


def _build_trrn_pdf_no_7q14b(path: str, header_rows: list, amounts: list, footer_rows: list) -> None:
    """Same as _build_trrn_pdf(), but the accounts table has only the
    Amount column -- no 7Q/14B at all -- matching the many real receipts/
    challans that never carry those (most only show them when there's an
    actual delay in payment)."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    styles = getSampleStyleSheet()
    elements = [Paragraph("Payment Confirmation Receipt", styles["Title"]), Spacer(1, 12)]

    t1 = Table(header_rows, colWidths=[180, 250])
    t1.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.grey)]))
    elements += [t1, Spacer(1, 12)]

    labels = ["Account-1", "Account-2", "Account-10", "Account-21", "Account-22"]
    acct_rows = [["Accounts", "Amount (Rs)"]]
    for lbl, amt in zip(labels, amounts):
        acct_rows.append([f"{lbl} Amount (Rs) :", amt])
    t2 = Table(acct_rows, colWidths=[180, 100])
    t2.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.grey)]))
    elements += [t2, Spacer(1, 12)]

    t3 = Table(footer_rows, colWidths=[180, 250])
    t3.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.grey)]))
    elements.append(t3)

    SimpleDocTemplate(path, pagesize=A4).build(elements)


def test_trrn_extraction_from_real_pdf_zero_7q_14b(tmp_path):
    pdf_path = str(tmp_path / "trrn_zero.pdf")
    _build_trrn_pdf(
        pdf_path,
        header_rows=[
            ["TRRN :", "2604240123926"],
            ["Challan Status :", "Payment Confirmed"],
            ["Establishment ID :", "BGBNG2410422000"],
            ["Establishment Name :", "TENET DIAGNOSTICS PRIVATE LIMITED"],
            ["Challan Type :", "Monthly Contribution"],
            ["Wage Month :", "MAR-2026"],
            ["Total Amount (Rs) :", "11,12,013"],
        ],
        acct_rows=_default_acct_rows(),
        footer_rows=[
            ["Payment Confirmation Bank :", "Axis Bank"],
            ["CRN :", "211150426200368"],
            ["Payment Confirmation Date :", "15-APR-2026"],
        ],
    )
    with open(pdf_path, "rb") as f:
        text = read_pdf_text(f.read())
    row = extract_pf_trrn("receipt.pdf", text)

    assert row["TRRN No"] == "2604240123926"
    assert row["Challan Status"] == "Payment Confirmed"
    assert row["Establishment ID"] == "BGBNG2410422000"
    assert row["Client Name"] == "TENET DIAGNOSTICS PRIVATE LIMITED"
    assert row["Wage Month"] == "Mar-2026"
    assert row["Total Amount (Rs)"] == "11,12,013"
    assert row["Account-1 (EPF)"] == "702560"
    assert row["Account-2 (Admin EPF)"] == "22240"
    assert row["Account-10 (EPS)"] == "364972"
    assert row["Account-21 (EDLI)"] == "22241"
    assert row["Account-22 (Admin)"] == "0"
    assert row["7Q Total"] == "0"
    assert row["14B Total"] == "0"
    assert row["Bank"] == "Axis Bank"
    assert row["CRN"] == "211150426200368"


def test_trrn_extraction_nonzero_7q_14b_are_separate_columns(tmp_path):
    """The actual real-world case this was fixed for: a receipt carrying
    non-zero 7Q (interest for delay) and 14B (damages for delay) amounts
    must have those summed into their own columns, distinct from each
    other and from the base per-account Amount."""
    pdf_path = str(tmp_path / "trrn_nonzero.pdf")
    _build_trrn_pdf(
        pdf_path,
        header_rows=[
            ["TRRN :", "2604240123927"],
            ["Establishment ID :", "BGBNG2410422000"],
            ["Wage Month :", "FEB-2026"],
        ],
        acct_rows=_default_acct_rows(
            q7=("1500", "50", "800", "50", "0"),
            damages=("3000", "100", "1600", "100", "0"),
        ),
        footer_rows=[["CRN :", "211150426200369"]],
    )
    with open(pdf_path, "rb") as f:
        text = read_pdf_text(f.read())
    row = extract_pf_trrn("receipt2.pdf", text)

    assert row["Account-1 (EPF)"] == "702560"  # base amount unaffected by 7Q/14B
    assert row["7Q Total"] == "2400"   # 1500+50+800+50+0
    assert row["14B Total"] == "4800"  # 3000+100+1600+100+0
    assert row["7Q Total"] != row["14B Total"]  # kept genuinely separate


def test_trrn_extraction_reads_correct_amounts_when_no_7q_14b_columns(tmp_path):
    """Real regression caught while testing this exact request ("keep the
    existing code as where it should read the challan even if the challan
    doesn't contain 7Q, 14B"): a fallback regex meant for an "amount
    precedes label" layout (`r"([\\d,]+)\\s+Account-{n}\\s+Amount..."`) was
    too eager -- with no 7Q/14B table, each account's line is just "Account-
    N Amount (Rs) : <amount>", and that fallback happily matched the
    PREVIOUS account's trailing amount as if it were the number right
    before the current account's label, shifting every value by one row
    (Account-2 showed Account-1's amount, Account-10 showed Account-2's,
    etc). Removed that unsafe fallback -- the amount-after-label regex
    already immediately below it handles this layout correctly and safely,
    anchored at each account's own label."""
    pdf_path = str(tmp_path / "trrn_no_7q14b.pdf")
    amounts = ["650000", "20000", "300000", "20000", "10000"]
    _build_trrn_pdf_no_7q14b(
        pdf_path,
        header_rows=[
            ["TRRN :", "2604240199999"],
            ["Establishment ID :", "BGBNG2410422000"],
            ["Wage Month :", "JAN-2026"],
            ["Total Amount (Rs) :", "10,00,000"],
        ],
        amounts=amounts,
        footer_rows=[["CRN :", "211150426299999"]],
    )
    with open(pdf_path, "rb") as f:
        text = read_pdf_text(f.read())
    row = extract_pf_trrn("challan_no_7q14b.pdf", text)

    assert row["Account-1 (EPF)"] == "650000"
    assert row["Account-2 (Admin EPF)"] == "20000"
    assert row["Account-10 (EPS)"] == "300000"
    assert row["Account-21 (EDLI)"] == "20000"
    assert row["Account-22 (Admin)"] == "10000"
    assert row["7Q Total"] == ""
    assert row["14B Total"] == ""
    assert row["TRRN No"] == "2604240199999"
    assert row["Total Amount (Rs)"] == "10,00,000"


def test_trrn_detection_routes_payment_confirmation_receipt_to_trrn():
    """Regression guard for the routing half of the request: any PDF whose
    text contains "Payment Confirmation Receipt" must be classified as
    TRRN, not CHALLAN/RETURN, regardless of what else is on the page."""
    from Combined_PF_Statutory import _detect_pf_type

    assert _detect_pf_type("... Payment Confirmation Receipt ... TRRN : 123") == "TRRN"
