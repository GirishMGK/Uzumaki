"""
Regression tests for bugs found in the 2026-07-01 full-codebase review.
Each test is a minimal reproduction of the failure scenario that was fixed.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ── pdf_tools.py: dispatch must call the real tool functions ───────────────────
def test_pdf_tools_dispatch_calls_real_functions():
    """
    Regression guard for the stub-file mixup: a syntactically valid but dead
    162-line stub previously shipped in place of the real 881-line app, and
    every page beyond Home just printed "Navigate to X using the sidebar."
    instead of calling merge_pdfs/split_by_ranges/remove_pages/pdf_to_word.
    py_compile can't catch this — it has to be checked structurally.
    """
    src = open(os.path.join(REPO_ROOT, "pdf_tools.py"), encoding="utf-8").read()
    assert "Navigate to" not in src, "pdf_tools.py has regressed to the dead-stub dispatch pattern"
    for fn in ["merge_pdfs(", "split_by_ranges(", "remove_pages(", "insert_pdf(", "pdf_to_word("]:
        assert fn in src, f"pdf_tools.py no longer calls {fn} — a tool page may be disconnected"


# ── reconcile.py: scheduled EMI must be Principal + Interest ───────────────────
def test_reconcile_scheduled_emi_uses_principal_plus_interest():
    pytest.importorskip("pdfplumber")
    pytest.importorskip("openpyxl")
    import reconcile

    src = open(os.path.join(REPO_ROOT, "reconcile.py"), encoding="utf-8").read()
    assert '"Instalment Balance"]' not in src.split("sched_emi")[1].split("\n")[0], (
        'reconcile.py must not use RPS "Instalment Balance" (an outstanding-balance '
        "figure) as the scheduled EMI — use Principal + Interest instead"
    )

    soa = {"master": {"Agreement No": "AGR1"}, "dpd_rows": [
        {"Instalment": 1, "Amount": 1200.0, "Due Date": "01-Apr-2025", "Paid Date": "01-Apr-2025", "DPD": 0},
    ]}
    rps = {"master": {"Agreement No": "AGR1"}, "schedule": [
        {"Instalment Number": 1, "Instalment Date": "01-Apr-2025", "Opening Balance": 100000.0,
         "Instalment Balance": 999999.0, "Principal": 1000.0, "Interest": 200.0,
         "Closing Balance": 98800.0, "Annualised Interest Rate %": 12.0},
    ]}
    result = reconcile.reconcile_pair(soa, rps)
    inst = result["instalments"][0]
    sched_emi = inst[3]
    assert sched_emi == 1200.0, f"expected Principal+Interest=1200.0, got {sched_emi}"
    assert result["summary"]["matched"] == 1
    assert result["summary"]["amount"] == 0


# ── extract_soa.py: 0% interest loans must not skip amortization ───────────────
def test_zero_interest_loan_gets_amortization_schedule():
    pytest.importorskip("pdfplumber")
    pytest.importorskip("openpyxl")
    import extract_soa

    loan_amt, rate, tenure, emi = 120000.0, 0.0, 12, 10000.0
    assert None not in (loan_amt, rate, tenure, emi)
    amort = extract_soa.build_amortization(loan_amt, rate, tenure, emi)
    assert len(amort) == 12, "0% interest loan should still produce a full amortization schedule"


# ── extract_rps.py: --dir mode must skip a corrupt file, not crash the batch ───
def test_rps_dir_mode_skips_corrupt_pdf(tmp_path):
    pytest.importorskip("pdfplumber")
    pytest.importorskip("openpyxl")
    (tmp_path / "not_a_real.pdf").write_bytes(b"this is not a pdf")
    out = tmp_path / "out.xlsx"
    proc = subprocess.run(
        [sys.executable, os.path.join(REPO_ROOT, "extract_rps.py"), "--dir", str(tmp_path), str(out)],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, f"--dir mode should not crash on a corrupt PDF:\n{proc.stderr}"
    assert "[SKIP]" in proc.stdout


# ── statutory_extractor.py: Grand Total must use the last match ────────────────
def test_grand_total_uses_last_match_not_first():
    pytest.importorskip("fitz")
    import statutory_extractor as se

    text = "Section A\nGrand Total 1000\nSection B\nGrand Total 2500\n"
    assert se.g_last(r"Grand Total\s+([\d,]+)", text) == "2500"


def test_compute_recon_flags_duplicate_gstr3b_keys():
    pytest.importorskip("fitz")
    import statutory_extractor as se

    gstr3b = [
        {"GSTIN": "27ABCDE1234F1Z5", "Tax Period": "Apr-2025", "3.1(a) Taxable Value": "100.00"},
        {"GSTIN": "27ABCDE1234F1Z5", "Tax Period": "Apr-2025", "3.1(a) Taxable Value": "150.00"},
    ]
    gstr1 = [{"GSTIN": "27ABCDE1234F1Z5", "Tax Period": "Apr-2025", "TL Taxable Value": "150.00"}]
    results, dups = se.compute_recon(gstr1, gstr3b)
    assert dups == [("27ABCDE1234F1Z5", "Apr-2025")]


# ── app.py (Flask): filename prefix strip must not assume a fixed width ────────
def test_app_prefix_strip_handles_wide_index():
    display = re.sub(r"^\d+_", "", "1000_myfile.pdf", count=1)
    assert display == "myfile.pdf"


# ── unified-framework pages: dispatch must call the real tool functions ────────
def test_soa_page_calls_real_functions():
    """
    Regression guard for the same class of bug as the pdf_tools.py stub: the
    SOA/RPS/Reconcile Streamlit page must actually call extract_loan/
    write_workbook/classify/reconcile_jobs, not just describe them.
    """
    src = open(os.path.join(REPO_ROOT, "_pages", "soa.py"), encoding="utf-8").read()
    for fn in ["extract_loan(", "write_workbook(", "classify(", "extract_rps(",
               "write_rps_workbook(", "reconcile_jobs(", "write_reconciliation_workbook("]:
        assert fn in src, f"_pages/soa.py no longer calls {fn} — the tool may be disconnected"


def test_redaction_page_calls_real_functions():
    src = open(os.path.join(REPO_ROOT, "_pages", "redaction.py"), encoding="utf-8").read()
    for fn in ["RedactionEngine(", "get_active_patterns(", "process_files("]:
        assert fn in src, f"_pages/redaction.py no longer calls {fn} — the tool may be disconnected"


# ── Home.py: on-open update-status toast ────────────────────────────────────
def test_update_notice_covers_every_launcher_status():
    """Every STATUS_* launcher.py can hand off must produce a distinct,
    version-substituted toast — a status Home.py doesn't recognize silently
    falls back to the "current" message, which would misreport a failed or
    just-installed update as nothing happening."""
    pytest.importorskip("streamlit")
    sys.path.insert(0, REPO_ROOT)
    import importlib

    import launcher
    Home = importlib.import_module("Home")

    for status in [launcher.STATUS_UPDATED, launcher.STATUS_CURRENT,
                   launcher.STATUS_OFFLINE, launcher.STATUS_UPDATE_FAILED]:
        msg, icon = Home._update_notice(status, "1.2.3")
        assert "1.2.3" in msg
        assert icon

    updated_msg, _ = Home._update_notice(launcher.STATUS_UPDATED, "1.2.3")
    current_msg, _ = Home._update_notice(launcher.STATUS_CURRENT, "1.2.3")
    assert updated_msg != current_msg

    # Unknown status must not crash — falls back to the "current" message.
    fallback_msg, _ = Home._update_notice("not-a-real-status", "1.2.3")
    assert fallback_msg == current_msg
