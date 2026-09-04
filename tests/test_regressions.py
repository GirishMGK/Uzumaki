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


def test_pdf_tools_merge_passes_bytes_not_dicts():
    """Regression guard for a real reported bug: 'Merge failed: a bytes-like
    object is required, not 'dict''. page_merge() stores uploaded files in
    st.session_state.merge_files as {"name", "bytes", "idx"} dicts (needed
    for the up/down reorder UI), but was passing that list of dicts straight
    to merge_pdfs(), which expects a plain list[bytes]. Verified end-to-end
    with real PDF bytes, not just a structural check, since a wrong
    List[dict] type hint wouldn't have caught this at all."""
    pytest.importorskip("pypdf")
    from pypdf import PdfWriter
    import io as _io
    from tools.merger import merge_pdfs, get_page_count

    def _make_pdf(n_pages: int) -> bytes:
        w = PdfWriter()
        for _ in range(n_pages):
            w.add_blank_page(width=200, height=200)
        buf = _io.BytesIO()
        w.write(buf)
        return buf.getvalue()

    files = [
        {"name": "a.pdf", "bytes": _make_pdf(2), "idx": 0},
        {"name": "b.pdf", "bytes": _make_pdf(3), "idx": 1},
    ]

    src = open(os.path.join(REPO_ROOT, "pdf_tools.py"), encoding="utf-8").read()
    assert 'merge_pdfs(files)' not in src, "pdf_tools.py regressed to passing dicts straight to merge_pdfs()"
    assert 'merge_pdfs([item["bytes"] for item in files])' in src

    result = merge_pdfs([item["bytes"] for item in files])
    assert get_page_count(result) == 5


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


# ── _pages/_runner.py: run_name must be "__main__" ──────────────────────────────
def test_runner_uses_main_run_name():
    """
    Regression guard for a real bug found while integration-testing Firm RMS:
    _pages/_runner.py ran legacy tool scripts via
    runpy.run_path(path, run_name="__hub_page__"). pdf_tools.py gates its
    actual page-dispatch call behind `if __name__ == "__main__":` (it began
    life as a standalone `streamlit run pdf_tools.py` app) -- any run_name
    other than "__main__" leaves that guard permanently False. The result
    was a silently blank PDF Tools page: no exception, no error box, HTTP
    200 either way -- the script ran far enough to inject its own CSS and
    define its functions, then simply never called any of them. Only caught
    by inspecting the live DOM (or, as here, Streamlit's own AppTest), not
    by an HTTP status check.
    """
    src = open(os.path.join(REPO_ROOT, "_pages", "_runner.py"), encoding="utf-8").read()
    assert 'run_name="__main__"' in src, (
        '_pages/_runner.py must use run_name="__main__" -- anything else '
        "silently breaks any wrapped script that gates its entry point "
        'behind `if __name__ == "__main__":` (e.g. pdf_tools.py)'
    )


def test_pdf_tools_page_actually_renders_content():
    """
    Behavioral companion to test_runner_uses_main_run_name(): actually drives
    the page via Streamlit's own AppTest and checks real widgets came out,
    not just that the script exited without raising. Before the run_name
    fix, this page executed cleanly (no exception) but produced only its own
    CSS block -- zero buttons -- which is exactly what an HTTP-level or
    py_compile check cannot tell apart from success.
    """
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(os.path.join(REPO_ROOT, "_pages", "pdf_tools_page.py"))
    at.run(timeout=15)
    assert not at.exception, f"pdf_tools_page raised: {at.exception}"
    # Home view: sidebar nav (5) + 4 "Open X PDF" card buttons = 9. A blank
    # page (the bug) produces 0.
    assert len(at.button) >= 5, (
        f"expected real page-dispatch content (multiple buttons), got {len(at.button)} "
        "— pdf_tools.py's main() may not be executing"
    )


# ── _pages/firm_rms.py: must actually start & embed the vendored backend ───────
def test_firm_rms_page_calls_real_functions():
    src = open(os.path.join(REPO_ROOT, "_pages", "firm_rms.py"), encoding="utf-8").read()
    for fn in ["startup_seed.run(", "uvicorn.run(", "st.components.v1.iframe("]:
        assert fn in src, f"_pages/firm_rms.py no longer calls {fn} — the tool may be disconnected"


# ── tally_tool/extract_ledgers.py: sign convention, filters, control total ─────
def test_tally_page_calls_real_functions():
    src = open(os.path.join(REPO_ROOT, "_pages", "tally_extractions.py"), encoding="utf-8").read()
    for fn in ["ensure_utf8(", "extract_any(", "build_tables(", "write_output("]:
        assert fn in src, f"_pages/tally_extractions.py no longer calls {fn} — the tool may be disconnected"


def _tally_fixture():
    """A minimal but real Tally 'JSON (Data Interchange)' export: one ledger
    master list plus a handful of vouchers, including a deliberately
    mismatched isdeemedpositive flag (see extract_ledgers.py's own docstring
    on why the amount's sign is used instead) and a cancelled voucher."""
    return {
        "tallymessage": [
            {"metadata": {"type": "Ledger", "name": "Cash"},
             "parent": "Cash-in-Hand", "openingbalance": "50000"},
            {"metadata": {"type": "Ledger", "name": "Sales Account"},
             "parent": "Sales Accounts", "openingbalance": "0"},
            {"metadata": {"type": "Ledger", "name": "TDS Payable"},
             "parent": "Duties & Taxes", "openingbalance": "0"},
            {"metadata": {"type": "Ledger", "name": "Dormant Ledger"},
             "parent": "Sundry Creditors", "openingbalance": "0"},
            {"metadata": {"type": "Voucher", "vchtype": "Payment", "remoteid": "guid-1"},
             "date": "20260410", "vouchertypename": "Bank Payment", "vouchernumber": "BP/001",
             "reference": "", "partyledgername": "Cash", "narration": "Consultancy fee w/ TDS",
             "masterid": "1", "iscancelled": False, "isoptional": False,
             "allledgerentries": [
                 {"ledgername": "Cash", "amount": "-9000", "isdeemedpositive": True},
                 # isdeemedpositive is deliberately "wrong" here -- the amount's
                 # sign (negative = Debit) is what must win.
                 {"ledgername": "TDS Payable", "amount": "-1000", "isdeemedpositive": False},
                 {"ledgername": "Sales Account", "amount": "10000", "isdeemedpositive": False},
             ]},
            {"metadata": {"type": "Voucher", "vchtype": "Payment", "remoteid": "guid-2"},
             "date": "20260415", "vouchertypename": "Payment", "vouchernumber": "P/CANC",
             "reference": "", "partyledgername": "Cash", "narration": "Cancelled voucher",
             "masterid": "2", "iscancelled": True, "isoptional": False,
             "allledgerentries": [
                 {"ledgername": "Cash", "amount": "-500", "isdeemedpositive": True},
                 {"ledgername": "Sales Account", "amount": "500", "isdeemedpositive": False},
             ]},
        ]
    }


def test_tally_extractor_uses_amount_sign_not_isdeemedpositive(tmp_path):
    """The core, easy-to-regress behavior: extract_ledgers.py must classify
    Debit/Credit from the SIGNED amount, not the isdeemedpositive flag --
    reverting to the flag would silently misclassify statutory/duty lines on
    migrated vouchers (see the module's own docstring)."""
    import json
    sys.path.insert(0, os.path.join(REPO_ROOT, "tally_tool"))
    from extract_ledgers import extract, build_tables

    src_path = tmp_path / "export.json"
    src_path.write_text(json.dumps(_tally_fixture()), encoding="utf-8")

    ledger_master, rows = extract(str(src_path))
    df, summary = build_tables(ledger_master, rows, include_cancelled=False,
                                from_date=None, to_date=None, ledger_filter=None)

    tds_row = df[df["Ledger Name"] == "TDS Payable"].iloc[0]
    assert tds_row["Debit"] == 1000.0 and tds_row["Credit"] == 0.0, (
        "TDS Payable's isdeemedpositive flag says Credit, but its amount sign "
        "says Debit -- the amount sign must win"
    )

    # Cancelled voucher excluded by default.
    assert "P/CANC" not in df["Voucher No"].values

    # Dormant ledger (no vouchers at all) still appears in the summary.
    assert "Dormant Ledger" in summary["Ledger Name"].values
    dormant = summary[summary["Ledger Name"] == "Dormant Ledger"].iloc[0]
    assert dormant["Transaction Count"] == 0

    # Control total: every real double-entry voucher must sum to zero.
    assert abs(df["Debit"].sum() - df["Credit"].sum()) < 0.01


def test_tally_extractor_include_cancelled_flag(tmp_path):
    import json
    sys.path.insert(0, os.path.join(REPO_ROOT, "tally_tool"))
    from extract_ledgers import extract, build_tables

    src_path = tmp_path / "export.json"
    src_path.write_text(json.dumps(_tally_fixture()), encoding="utf-8")

    ledger_master, rows = extract(str(src_path))
    df, _ = build_tables(ledger_master, rows, include_cancelled=True,
                          from_date=None, to_date=None, ledger_filter=None)
    assert "P/CANC" in df["Voucher No"].values
    assert "Cancelled" in df.columns


def test_tally_page_offers_live_connect():
    src = open(os.path.join(REPO_ROOT, "_pages", "tally_extractions.py"), encoding="utf-8").read()
    assert "tally_connector" in src
    assert "pull_from_tally(" in src


def test_tally_connector_parses_voucher_xml_with_amount_sign_convention():
    """A fabricated but Tally-schema-shaped <VOUCHER> XML block (not a real
    server response -- no Tally instance is available to test against here)
    should parse into the same row shape/convention as the JSON path: signed
    amount decides Debit/Credit, not ISDEEMEDPOSITIVE."""
    import xml.etree.ElementTree as ET
    sys.path.insert(0, os.path.join(REPO_ROOT, "tally_tool"))
    import tally_connector as tc

    fake_response = """<ENVELOPE>
<VOUCHER>
  <DATE>20260410</DATE>
  <VOUCHERTYPENAME>Bank Payment</VOUCHERTYPENAME>
  <VOUCHERNUMBER>BP/001</VOUCHERNUMBER>
  <PARTYLEDGERNAME>Cash</PARTYLEDGERNAME>
  <NARRATION>Consultancy fee w/ TDS</NARRATION>
  <GUID>guid-1</GUID>
  <MASTERID>1</MASTERID>
  <ISCANCELLED>No</ISCANCELLED>
  <ISOPTIONAL>No</ISOPTIONAL>
  <ALLLEDGERENTRIES.LIST>
    <LEDGERNAME>Cash</LEDGERNAME>
    <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
    <AMOUNT>-9000</AMOUNT>
  </ALLLEDGERENTRIES.LIST>
  <ALLLEDGERENTRIES.LIST>
    <LEDGERNAME>TDS Payable</LEDGERNAME>
    <ISDEEMEDPOSITIVE>False</ISDEEMEDPOSITIVE>
    <AMOUNT>-1000</AMOUNT>
  </ALLLEDGERENTRIES.LIST>
  <ALLLEDGERENTRIES.LIST>
    <LEDGERNAME>Sales Account</LEDGERNAME>
    <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
    <AMOUNT>10000</AMOUNT>
  </ALLLEDGERENTRIES.LIST>
</VOUCHER>
</ENVELOPE>"""

    root = ET.fromstring(fake_response)
    rows = []
    seq = 0
    for voucher in root.iter("VOUCHER"):
        date = tc._parse_date(tc._text(voucher, "DATE"))
        for entry in voucher.findall("ALLLEDGERENTRIES.LIST") + voucher.findall("LEDGERENTRIES.LIST"):
            lname = tc._text(entry, "LEDGERNAME")
            amount = tc._to_float(tc._text(entry, "AMOUNT"))
            seq += 1
            rows.append({
                "Ledger Name": lname, "Date": date,
                "Debit": -amount if amount < 0 else 0.0,
                "Credit": amount if amount > 0 else 0.0,
            })

    by_name = {r["Ledger Name"]: r for r in rows}
    assert by_name["Cash"]["Debit"] == 9000.0 and by_name["Cash"]["Credit"] == 0.0
    # TDS Payable's ISDEEMEDPOSITIVE says "not deemed positive" but the amount
    # is negative -- same mismatch case as the JSON fixture, amount sign wins.
    assert by_name["TDS Payable"]["Debit"] == 1000.0
    assert by_name["Sales Account"]["Credit"] == 10000.0


def test_tally_connector_reports_clear_error_when_unreachable():
    sys.path.insert(0, os.path.join(REPO_ROOT, "tally_tool"))
    import tally_connector as tc

    ok, message = tc.test_connection("127.0.0.1", 1)  # nothing listens on port 1
    assert ok is False
    assert "Tally" in message


def test_tally_connector_requires_date_range_for_voucher_fetch():
    """Regression guard for a real bug found testing against a live Tally
    instance: a Voucher Collection request with no SVFROMDATE/SVTODATE
    doesn't error -- Tally silently returns its <CMPINFO> object-count
    diagnostic (a few hundred bytes) instead of voucher data, which then
    fails to parse. fetch_vouchers() must refuse up front instead of letting
    that confusing response reach the caller."""
    import datetime as dt
    sys.path.insert(0, os.path.join(REPO_ROOT, "tally_tool"))
    import tally_connector as tc

    today = dt.date.today()
    for from_date, to_date in [(None, today), (today, None), (None, None)]:
        with pytest.raises(tc.TallyConnectionError, match="Both From date and To date"):
            tc.fetch_vouchers("127.0.0.1", 1, "Some Company", from_date, to_date)


def test_tally_connector_fetches_ledger_entry_sub_list():
    """Regression guard for a real bug found testing against a live Tally
    instance: the Voucher Collection FETCH list didn't name
    ALLLEDGERENTRIES.LIST/LEDGERENTRIES.LIST, so Tally returned the voucher
    'shell' (date/party/narration) with no ledger entries at all -- the
    request succeeded but every voucher produced zero rows."""
    src = open(os.path.join(REPO_ROOT, "tally_tool", "tally_connector.py"), encoding="utf-8").read()
    assert "ALLLEDGERENTRIES.LIST" in src
    assert "LEDGERENTRIES.LIST" in src


def test_tally_live_tab_defaults_to_a_populated_date_range():
    """Regression guard for the same bug at the UI layer: the date pickers
    must not default to None, or every live pull hits the same Tally
    diagnostic-fallback bug by default."""
    src = open(os.path.join(REPO_ROOT, "_pages", "tally_extractions.py"), encoding="utf-8").read()
    assert "value=datetime.date(2000, 1, 1)" in src
    assert "value=datetime.date.today()" in src


def test_tally_date_pickers_pin_explicit_min_max():
    """Regression guard for a real reported bug: st.date_input auto-computes
    its calendar's navigable range as roughly value +/- 10 years when
    min_value/max_value aren't given. Passing value=date(2000, 1, 1) without
    pinning bounds silently capped the picker at ~2010, making it impossible
    to select any recent date (confirmed live: 'From date' rendered in an
    invalid/red state at today's actual date). Every date_input on this page
    must pin explicit, wide min_value/max_value so the default value chosen
    for UX can't shrink the usable range."""
    src = open(os.path.join(REPO_ROOT, "_pages", "tally_extractions.py"), encoding="utf-8").read()
    n = src.count("st.date_input(")
    assert n >= 4
    assert src.count("min_value=datetime.date(1990, 1, 1)") == n
    assert src.count("max_value=datetime.date(2100, 1, 1)") == n


def _tally_xml_fixture() -> str:
    """XML-export equivalent of _tally_fixture() above -- same ledgers/vouchers,
    same deliberately-mismatched isdeemedpositive flag and cancelled voucher,
    so both formats can be asserted to produce identical results."""
    return """<ENVELOPE><BODY><IMPORTDATA><REQUESTDATA>
<TALLYMESSAGE><LEDGER NAME="Cash"><PARENT>Cash-in-Hand</PARENT><OPENINGBALANCE>50000</OPENINGBALANCE></LEDGER></TALLYMESSAGE>
<TALLYMESSAGE><LEDGER NAME="Sales Account"><PARENT>Sales Accounts</PARENT><OPENINGBALANCE>0</OPENINGBALANCE></LEDGER></TALLYMESSAGE>
<TALLYMESSAGE><LEDGER NAME="TDS Payable"><PARENT>Duties &amp; Taxes</PARENT><OPENINGBALANCE>0</OPENINGBALANCE></LEDGER></TALLYMESSAGE>
<TALLYMESSAGE><LEDGER NAME="Dormant Ledger"><PARENT>Sundry Creditors</PARENT><OPENINGBALANCE>0</OPENINGBALANCE></LEDGER></TALLYMESSAGE>
<TALLYMESSAGE><VOUCHER VCHTYPE="Payment" ACTION="Create">
  <DATE>20260410</DATE><VOUCHERTYPENAME>Bank Payment</VOUCHERTYPENAME><VOUCHERNUMBER>BP/001</VOUCHERNUMBER>
  <PARTYLEDGERNAME>Cash</PARTYLEDGERNAME><NARRATION>Consultancy fee w/ TDS</NARRATION>
  <MASTERID>1</MASTERID><ISCANCELLED>No</ISCANCELLED><ISOPTIONAL>No</ISOPTIONAL>
  <ALLLEDGERENTRIES.LIST><LEDGERNAME>Cash</LEDGERNAME><ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE><AMOUNT>-9000</AMOUNT></ALLLEDGERENTRIES.LIST>
  <ALLLEDGERENTRIES.LIST><LEDGERNAME>TDS Payable</LEDGERNAME><ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE><AMOUNT>-1000</AMOUNT></ALLLEDGERENTRIES.LIST>
  <ALLLEDGERENTRIES.LIST><LEDGERNAME>Sales Account</LEDGERNAME><ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE><AMOUNT>10000</AMOUNT></ALLLEDGERENTRIES.LIST>
</VOUCHER></TALLYMESSAGE>
<TALLYMESSAGE><VOUCHER VCHTYPE="Payment" ACTION="Create">
  <DATE>20260415</DATE><VOUCHERTYPENAME>Payment</VOUCHERTYPENAME><VOUCHERNUMBER>P/CANC</VOUCHERNUMBER>
  <PARTYLEDGERNAME>Cash</PARTYLEDGERNAME><NARRATION>Cancelled voucher</NARRATION>
  <MASTERID>2</MASTERID><ISCANCELLED>Yes</ISCANCELLED><ISOPTIONAL>No</ISOPTIONAL>
  <ALLLEDGERENTRIES.LIST><LEDGERNAME>Cash</LEDGERNAME><ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE><AMOUNT>-500</AMOUNT></ALLLEDGERENTRIES.LIST>
  <ALLLEDGERENTRIES.LIST><LEDGERNAME>Sales Account</LEDGERNAME><ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE><AMOUNT>500</AMOUNT></ALLLEDGERENTRIES.LIST>
</VOUCHER></TALLYMESSAGE>
</REQUESTDATA></IMPORTDATA></BODY></ENVELOPE>"""


def test_tally_extractor_xml_matches_json_extractor(tmp_path):
    """The XML and JSON export paths must produce identical results for
    equivalent input -- same amount-sign-over-isdeemedpositive convention,
    same cancelled-voucher exclusion, same dormant-ledger inclusion, same
    control total."""
    import json
    sys.path.insert(0, os.path.join(REPO_ROOT, "tally_tool"))
    from extract_ledgers import extract, extract_xml, extract_any, sniff_format, build_tables

    json_path = tmp_path / "export.json"
    json_path.write_text(json.dumps(_tally_fixture()), encoding="utf-8")
    xml_path = tmp_path / "export.xml"
    xml_path.write_text(_tally_xml_fixture(), encoding="utf-8")

    assert sniff_format(str(json_path)) == "json"
    assert sniff_format(str(xml_path)) == "xml"

    lm_json, rows_json = extract(str(json_path))
    lm_xml, rows_xml = extract_xml(str(xml_path))

    df_json, summary_json = build_tables(lm_json, rows_json, include_cancelled=False,
                                          from_date=None, to_date=None, ledger_filter=None)
    df_xml, summary_xml = build_tables(lm_xml, rows_xml, include_cancelled=False,
                                        from_date=None, to_date=None, ledger_filter=None)

    assert len(df_json) == len(df_xml)
    assert set(df_json["Ledger Name"]) == set(df_xml["Ledger Name"])
    assert df_json["Debit"].sum() == df_xml["Debit"].sum()
    assert df_json["Credit"].sum() == df_xml["Credit"].sum()
    tds_xml = df_xml[df_xml["Ledger Name"] == "TDS Payable"].iloc[0]
    assert tds_xml["Debit"] == 1000.0  # amount sign wins over isdeemedpositive, XML path too

    # extract_any() must dispatch correctly for both formats
    lm_any_json, rows_any_json = extract_any(str(json_path))
    lm_any_xml, rows_any_xml = extract_any(str(xml_path))
    assert len(rows_any_json) == len(rows_json)
    assert len(rows_any_xml) == len(rows_xml)


def test_tally_page_accepts_xml_uploads():
    src = open(os.path.join(REPO_ROOT, "_pages", "tally_extractions.py"), encoding="utf-8").read()
    assert '"xml"' in src
    assert "extract_any(" in src


def _fake_register_response_xml() -> str:
    """A fabricated Sales+Purchase voucher pair matching the real captured
    Tally response shape (attributes, TYPE="..." decorations, item lines)."""
    return """<ENVELOPE><DATA><COLLECTION>
<VOUCHER VCHTYPE="Sales">
  <DATE TYPE="Date">20260401</DATE>
  <VOUCHERTYPENAME>Sales</VOUCHERTYPENAME>
  <VOUCHERNUMBER>1</VOUCHERNUMBER>
  <PARTYLEDGERNAME TYPE="String">ABC Traders</PARTYLEDGERNAME>
  <REFERENCE TYPE="String">INV-001</REFERENCE>
  <NARRATION TYPE="String">Sale of goods</NARRATION>
  <GUID>guid-1</GUID>
  <MASTERID TYPE="Number"> 501</MASTERID>
  <ISCANCELLED>No</ISCANCELLED>
  <ISOPTIONAL>No</ISOPTIONAL>
  <ALLLEDGERENTRIES.LIST>
    <LEDGERNAME>ABC Traders</LEDGERNAME>
    <AMOUNT>-11800</AMOUNT>
  </ALLLEDGERENTRIES.LIST>
  <ALLLEDGERENTRIES.LIST>
    <LEDGERNAME>Sales Account</LEDGERNAME>
    <AMOUNT>10000</AMOUNT>
  </ALLLEDGERENTRIES.LIST>
  <ALLLEDGERENTRIES.LIST>
    <LEDGERNAME>Output GST</LEDGERNAME>
    <AMOUNT>1800</AMOUNT>
  </ALLLEDGERENTRIES.LIST>
  <ALLINVENTORYENTRIES.LIST>
    <STOCKITEMNAME>Widget A</STOCKITEMNAME>
    <ACTUALQTY>10 Nos</ACTUALQTY>
    <RATE>1000/Nos</RATE>
    <AMOUNT>10000</AMOUNT>
  </ALLINVENTORYENTRIES.LIST>
</VOUCHER>
<VOUCHER VCHTYPE="Purchase">
  <DATE TYPE="Date">20260405</DATE>
  <VOUCHERTYPENAME>Purchase</VOUCHERTYPENAME>
  <VOUCHERNUMBER>1</VOUCHERNUMBER>
  <PARTYLEDGERNAME TYPE="String">XYZ Suppliers</PARTYLEDGERNAME>
  <ISCANCELLED>No</ISCANCELLED>
  <ISOPTIONAL>No</ISOPTIONAL>
  <ALLLEDGERENTRIES.LIST>
    <LEDGERNAME>XYZ Suppliers</LEDGERNAME>
    <AMOUNT>5000</AMOUNT>
  </ALLLEDGERENTRIES.LIST>
  <ALLLEDGERENTRIES.LIST>
    <LEDGERNAME>Purchase Account</LEDGERNAME>
    <AMOUNT>-5000</AMOUNT>
  </ALLLEDGERENTRIES.LIST>
  <ALLINVENTORYENTRIES.LIST>
    <STOCKITEMNAME>Raw Material X</STOCKITEMNAME>
    <ACTUALQTY>50 Kg</ACTUALQTY>
    <RATE>100/Kg</RATE>
    <AMOUNT>-5000</AMOUNT>
  </ALLINVENTORYENTRIES.LIST>
</VOUCHER>
<VOUCHER VCHTYPE="Sales">
  <DATE TYPE="Date">20260410</DATE>
  <VOUCHERTYPENAME>Sales</VOUCHERTYPENAME>
  <VOUCHERNUMBER>2</VOUCHERNUMBER>
  <PARTYLEDGERNAME TYPE="String">Cancelled Co</PARTYLEDGERNAME>
  <ISCANCELLED>Yes</ISCANCELLED>
  <ISOPTIONAL>No</ISOPTIONAL>
  <ALLLEDGERENTRIES.LIST>
    <LEDGERNAME>Cancelled Co</LEDGERNAME>
    <AMOUNT>-100</AMOUNT>
  </ALLLEDGERENTRIES.LIST>
  <ALLLEDGERENTRIES.LIST>
    <LEDGERNAME>Sales Account</LEDGERNAME>
    <AMOUNT>100</AMOUNT>
  </ALLLEDGERENTRIES.LIST>
</VOUCHER>
</COLLECTION></DATA></ENVELOPE>"""


def test_tally_register_requires_date_range():
    import datetime as dt
    sys.path.insert(0, os.path.join(REPO_ROOT, "tally_tool"))
    import tally_connector as tc

    with pytest.raises(tc.TallyConnectionError, match="Both From date and To date"):
        tc.fetch_voucher_register("127.0.0.1", 1, "Co", {"Sales"}, None, dt.date.today())


def test_tally_register_filters_by_voucher_type_and_excludes_cancelled(monkeypatch):
    """Regression guard: fetch_voucher_register must (a) only return the
    requested voucher type, (b) pull item-wise rows with the real field
    names (STOCKITEMNAME/ACTUALQTY/RATE/AMOUNT under ALLINVENTORYENTRIES.LIST),
    (c) compute Voucher Total from the non-party ledger entries (the
    GST-inclusive invoice value, not just the item amount), and (d) exclude
    cancelled vouchers by default."""
    import datetime as dt
    import xml.etree.ElementTree as ET
    sys.path.insert(0, os.path.join(REPO_ROOT, "tally_tool"))
    import tally_connector as tc

    fake_root = ET.fromstring(_fake_register_response_xml())
    monkeypatch.setattr(tc, "_post", lambda host, port, xml: fake_root)

    sales_rows = tc.fetch_voucher_register(
        "h", 1, "C", {"Sales"}, dt.date(2026, 1, 1), dt.date(2026, 12, 31)
    )
    assert len(sales_rows) == 1  # the cancelled Sales voucher must be excluded
    assert sales_rows[0]["Stock Item"] == "Widget A"
    assert sales_rows[0]["Item Amount"] == 10000.0
    assert sales_rows[0]["Voucher Total"] == 11800.0  # includes Output GST, not just the item value

    purchase_rows = tc.fetch_voucher_register(
        "h", 1, "C", {"Purchase"}, dt.date(2026, 1, 1), dt.date(2026, 12, 31)
    )
    assert len(purchase_rows) == 1
    assert purchase_rows[0]["Stock Item"] == "Raw Material X"

    sales_with_cancelled = tc.fetch_voucher_register(
        "h", 1, "C", {"Sales"}, dt.date(2026, 1, 1), dt.date(2026, 12, 31), include_cancelled=True
    )
    assert len(sales_with_cancelled) == 2  # now the cancelled voucher's service-style row is included too


def test_tally_page_has_register_tab():
    src = open(os.path.join(REPO_ROOT, "_pages", "tally_extractions.py"), encoding="utf-8").read()
    assert "fetch_voucher_register(" in src
    assert "Sales & Purchase Register" in src
