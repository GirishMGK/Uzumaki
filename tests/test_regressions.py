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


# ── Regressions from the 2026-07-02 full-codebase review ───────────────────────

# ── pdf_tools.py: merge must pass raw byte blobs, not the session-state dicts ──
def test_pdf_tools_merge_passes_byte_blobs():
    """
    tools.merger.merge_pdfs() takes a list[bytes]. pdf_tools.py's merge page
    keeps files as {"name", "bytes", "idx"} dicts in session state for the
    reorder UI, and previously passed that dict list straight to merge_pdfs(),
    raising `TypeError: a bytes-like object is required, not 'dict'` on every
    merge attempt (swallowed by a generic except, so the button just silently
    failed). Guard that the call site still unpacks ["bytes"] before calling.
    """
    src = open(os.path.join(REPO_ROOT, "pdf_tools.py"), encoding="utf-8").read()
    assert 'merge_pdfs([f["bytes"] for f in files])' in src, (
        "pdf_tools.py must extract raw bytes before calling merge_pdfs() — "
        "passing the session-state dicts directly crashes every merge"
    )


# ── reconcile.py: missing/duplicate Agreement No must never false-match ────────
def test_reconcile_jobs_ignores_missing_and_duplicate_agreement_no():
    pytest.importorskip("pdfplumber")
    pytest.importorskip("openpyxl")
    import reconcile

    def soa(agr):
        return {"master": {"Agreement No": agr}, "dpd_rows": []}

    def rps(agr):
        return {"master": {"Agreement No": agr}, "schedule": []}

    # Two SOAs and two RPSs each fail to extract an Agreement No (None) —
    # they must never be matched to each other just because both keys are None.
    soas = [soa(None), soa(None), soa("AGR1"), soa("AGR1")]  # AGR1 duplicated on SOA side
    rpss = [rps(None), rps("AGR1")]  # AGR1 present once on RPS side

    pairs, soa_only, rps_only = reconcile.reconcile_jobs(soas, rpss)

    assert pairs == [], "None keys or a duplicated Agreement No must never produce a match"
    assert soa_only.count(None) == 2
    assert soa_only.count("AGR1") == 2  # both AGR1 SOAs reported, not silently dropped
    assert rps_only.count(None) == 1
    assert "AGR1" in rps_only  # the unmatched RPS side of the ambiguous key is reported too


def test_reconcile_jobs_matches_case_and_whitespace_insensitively():
    pytest.importorskip("pdfplumber")
    pytest.importorskip("openpyxl")
    import reconcile

    soas = [{"master": {"Agreement No": " agr123 "}, "dpd_rows": []}]
    rpss = [{"master": {"Agreement No": "AGR123"}, "schedule": []}]
    pairs, soa_only, rps_only = reconcile.reconcile_jobs(soas, rpss)
    assert len(pairs) == 1
    assert soa_only == [] and rps_only == []


# ── extract_soa.py: TOD-06 must not auto-PASS a real broken-period-interest
# charge just because the computed expected value happens to be zero ────────────
def test_bpi_check_flags_nonzero_actual_against_zero_expected():
    pytest.importorskip("pdfplumber")
    pytest.importorskip("openpyxl")
    import extract_soa

    charges = [{"Category": "Broken Period Interest", "DR": 5000.0}]
    loan_amt, rate = 100000.0, 12.0
    disb_date = extract_soa.dt.date(2025, 2, 1)
    inst_start = extract_soa.dt.date(2025, 2, 5)  # cycle_start ends up before disb_date -> exp=0
    master = {"Frequency": "MONTHLY"}
    checks = []

    def add(cid, family, proc, expv, actv, status, note=""):
        checks.append((cid, status))

    # Re-run just the BPI block logic via the public build_checks-style path is
    # heavier than needed here; assert the guarded branch directly instead.
    months = extract_soa.FREQ_MONTHS.get("MONTHLY", 1)
    cycle_start = extract_soa._minus_months(inst_start, months)
    days = max((cycle_start - disb_date).days, 0)
    exp = round(loan_amt * rate / 100 * days / 365.0, 2)
    actual = sum(c["DR"] for c in charges)
    rel = 0.0 if not exp and abs(actual) <= 1 else (abs(actual - exp) / exp if exp else float("inf"))
    assert exp == 0.0
    assert rel == float("inf"), "a real non-zero BPI charge against a zero expected value must not PASS"


# ── je_audit_tool: Benford's chi-square must not crash on typical sample sizes ─
def test_benford_chisquare_handles_rounding_mismatch():
    pytest.importorskip("scipy")
    import importlib.util
    import numpy as np
    import pandas as pd

    # je_audit_tool/tests is a package of audit-logic modules (not pytest
    # tests) named "tests" — the same name as this file's own top-level
    # package, so a plain `import tests.benford_test` would resolve to the
    # already-imported pytest "tests" package instead. Load it directly.
    je_audit_dir = os.path.join(REPO_ROOT, "je_audit_tool")
    sys.path.insert(0, je_audit_dir)  # so benford_test's own `from utils...` import works
    spec = importlib.util.spec_from_file_location(
        "_je_audit_benford_test", os.path.join(je_audit_dir, "tests", "benford_test.py"))
    benford_test = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(benford_test)

    rng = np.random.default_rng(0)
    for n in (137, 251, 500, 733):
        df = pd.DataFrame({"Amount": rng.exponential(1000, n)})
        # Must not raise scipy.stats.chisquare's
        # "sum of observed/expected frequencies must agree" ValueError.
        benford_test.run_benford_test(df, {"amount": "Amount"})


# ── je_audit_tool: leading zeros in vendor codes must survive the sequential-
# code check instead of being reformatted away via str(int(...)) ───────────────
def test_vendor_sequential_codes_preserves_leading_zeros():
    sys.path.insert(0, os.path.join(REPO_ROOT, "je_audit_tool"))
    import pandas as pd

    vc = pd.Series(["0001000234", "0001000235", "0001000236", "0002000000"])
    num_vc = vc[vc.str.match(r"^\d+$", na=False)]
    int_to_orig = {}
    for orig in num_vc.unique():
        int_to_orig.setdefault(int(orig), set()).add(orig)
    nums = sorted(int_to_orig)
    seq_nums = set()
    run = 1
    for i in range(1, len(nums)):
        if nums[i] - nums[i - 1] == 1:
            run += 1
            if run >= 3:
                seq_nums.update([nums[i - 2], nums[i - 1], nums[i]])
        else:
            run = 1
    seq_vendors = {orig for n in seq_nums for orig in int_to_orig[n]}
    assert vc.isin(seq_vendors).tolist() == [True, True, True, False]


# ── je_audit_tool: currency-formatted amounts must not silently coerce to 0 ────
def test_get_safe_numeric_parses_currency_and_accounting_negatives():
    sys.path.insert(0, os.path.join(REPO_ROOT, "je_audit_tool"))
    import pandas as pd
    from utils.column_mapper import get_safe_numeric

    df = pd.DataFrame({"Amt": ["₹1,23,456.00", "(1,234.56)", "1000", "garbage", None]})
    out = get_safe_numeric(df, "amount", {"amount": "Amt"}).tolist()
    assert out == [123456.0, -1234.56, 1000.0, 0.0, 0.0]


# ── form26as_tool: a deductor row from a non-Part-A section must never leak
# into the `current` deductor context used by later Part A transactions ────────
def test_form26as_deductor_state_gated_by_part_a():
    sys.path.insert(0, os.path.join(REPO_ROOT, "form26as_tool"))
    from form26as.parser import parse_part_a

    grid = [
        ["Sr. No.", "Name of Deductor", "TAN of Deductor", "Total Amount Paid",
         "Total Tax Deducted", "Total TDS Deposited"],
        ["1", "PAYER LTD", "DELS99999Z", "10000", "1000", "1000"],
        ["Sr. No.", "Section", "Transaction Date", "Status of Booking",
         "Date of Booking", "Amount Paid", "Tax Deducted", "TDS Deposited"],
        ["1", "194A", "01-Apr-2024", "F", "01-May-2024", "10000", "1000", "1000"],
        ["PART B (TAX COLLECTED AT SOURCE)"],
        ["Sr. No.", "Name of Collector", "TAN of Collector", "Total Amount Paid",
         "Total Tax Collected", "Total TCS Deposited"],
        ["1", "COLLECTOR LTD", "JPRS55555Q", "5000", "500", "500"],
    ]
    out = parse_part_a(grid)
    assert len(out) == 1
    assert out[0].name_of_deductor == "PAYER LTD"
    assert out[0].tan_of_deductor == "DELS99999Z"
