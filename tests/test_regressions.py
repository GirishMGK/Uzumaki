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


def _spatial_text(page) -> str:
    """Reconstruct a page's text in visual reading order via word bboxes,
    sorted by (rounded y, x) -- more robust than get_text('text') for
    checking a redaction-based edit landed in the right place, since
    PyMuPDF's own block/line grouping heuristic can split apart words whose
    inserted-text bbox has a slightly different line-height envelope than
    the surrounding text (confirmed while building replace_text() below:
    get_text('text') put a same-line replacement on its own line even
    though its bbox was genuinely adjacent/overlapping the rest of the
    line)."""
    words = page.get_text("words")
    words_sorted = sorted(words, key=lambda w: (round(w[1]), w[0]))
    return " ".join(w[4] for w in words_sorted)


def test_pdf_tools_edit_find_and_replace_real_text():
    """New feature: change words inside a PDF, not just page-level ops.
    Verified end-to-end against real PyMuPDF-rendered text (not a mock),
    including a real gotcha found while building this: PyMuPDF's
    page.search_for() is case-INsensitive no matter what case you pass it
    (confirmed directly: searching 'Hello' matches 'Hello', 'hello', AND
    'HELLO' alike) -- case_sensitive must be enforced by checking each
    match's actual literal text via get_textbox(), not by how search_for()
    itself was called."""
    pytest.importorskip("fitz")
    import fitz
    from tools.editor import replace_text

    def _make_pdf(lines: list[tuple[tuple[float, float], str]]) -> bytes:
        doc = fitz.open()
        page = doc.new_page()
        for pos, text in lines:
            page.insert_text(pos, text)
        buf = doc.tobytes()
        doc.close()
        return buf

    src_pdf = _make_pdf([
        ((72, 72), "Hello World, this is a test document."),
        ((72, 100), "hello again in lowercase, and HELLO in caps."),
    ])

    # Case-sensitive: only the capital-H "Hello" is replaced.
    out, n = replace_text(src_pdf, "Hello", "Goodbye", case_sensitive=True)
    assert n == 1
    doc = fitz.open(stream=out, filetype="pdf")
    text = _spatial_text(doc[0])
    doc.close()
    assert text.startswith("Goodbye World,")
    assert "hello again" in text  # lowercase left untouched
    assert "HELLO in caps" in text  # uppercase left untouched

    # Case-insensitive: all three variants replaced.
    out2, n2 = replace_text(src_pdf, "hello", "X", case_sensitive=False)
    assert n2 == 3

    # Whole-word: "est" must not match inside "test"/"document".
    out3, n3 = replace_text(src_pdf, "est", "ZZZ", case_sensitive=True, whole_word=True)
    assert n3 == 0

    # Whole-word: "test" as its own word does match.
    out4, n4 = replace_text(src_pdf, "test", "ZZZ", case_sensitive=True, whole_word=True)
    assert n4 == 1
    doc4 = fitz.open(stream=out4, filetype="pdf")
    text4 = _spatial_text(doc4[0])
    doc4.close()
    assert "ZZZ document." in text4

    # Not-found text raises no error, just reports 0 replacements.
    out5, n5 = replace_text(src_pdf, "nonexistent-phrase-xyz", "Q")
    assert n5 == 0

    # Empty find string is rejected up front with a clear message.
    with pytest.raises(ValueError):
        replace_text(src_pdf, "", "Q")


def test_pdf_tools_edit_page_wires_up_replace_text():
    src = open(os.path.join(REPO_ROOT, "pdf_tools.py"), encoding="utf-8").read()
    assert "replace_text(" in src
    assert "Find & Replace Text" in src


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


# ── _pages/hrm.py: must actually start & embed the vendored backend ───────
def test_hrm_page_calls_real_functions():
    src = open(os.path.join(REPO_ROOT, "_pages", "hrm.py"), encoding="utf-8").read()
    for fn in ["startup_seed.run(", "uvicorn.run(", "st.components.v1.iframe("]:
        assert fn in src, f"_pages/hrm.py no longer calls {fn} — the tool may be disconnected"


def test_firm_rms_removed_and_replaced_by_hrm():
    """Regression guard for a real user request: remove the "Firm RMS" tool
    entirely (not sync/rename it in place) and add the same underlying
    Manpower-Tracker code as a brand-new tool called "HRM" instead -- a
    distinct nav entry, own vendored copy under hrm_tool/ (not firm_rms_tool/,
    which must be fully gone), own page/port/data-dir so it doesn't collide
    with any lingering local Firm RMS install on a user's machine."""
    assert not os.path.exists(os.path.join(REPO_ROOT, "firm_rms_tool"))
    assert not os.path.exists(os.path.join(REPO_ROOT, "_pages", "firm_rms.py"))
    assert os.path.exists(os.path.join(REPO_ROOT, "hrm_tool", "backend"))
    assert os.path.exists(os.path.join(REPO_ROOT, "hrm_tool", "frontend_dist"))

    home_src = open(os.path.join(REPO_ROOT, "Home.py"), encoding="utf-8").read()
    assert "firm_rms" not in home_src.lower()
    assert '"_pages/hrm.py"' in home_src
    assert '"HRM"' in home_src

    spec_src = open(os.path.join(REPO_ROOT, "Uzumaki.spec"), encoding="utf-8").read()
    assert "firm_rms" not in spec_src.lower()
    assert '_tree("hrm_tool")' in spec_src
    assert os.path.join("hrm_tool", "backend", "app", "main.py") in spec_src


# ── tally_tool/extract_ledgers.py: sign convention, filters, control total ─────
def test_tally_page_calls_real_functions():
    src = open(os.path.join(REPO_ROOT, "_pages", "tally_hub.py"), encoding="utf-8").read()
    # extract_any_with_progress() (tally_common.py) wraps extract_any() with
    # a real progress bar -- tally_hub.py calls that wrapper now, not
    # extract_any() directly, but the wrapper itself must still call the
    # real thing (checked separately below).
    for fn in ["ensure_utf8(", "extract_any_with_progress(", "build_tables(", "write_output("]:
        assert fn in src, f"_pages/tally_hub.py no longer calls {fn} — the tool may be disconnected"

    common_src = open(os.path.join(REPO_ROOT, "_pages", "tally_common.py"), encoding="utf-8").read()
    assert "extract_any(" in common_src, (
        "tally_common.extract_any_with_progress() no longer calls the real extract_any() "
        "— every Tally hub activity's Upload tab goes through this wrapper"
    )


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
    src = open(os.path.join(REPO_ROOT, "_pages", "tally_hub.py"), encoding="utf-8").read()
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


def test_tally_connector_reports_clear_error_when_unreachable(monkeypatch):
    sys.path.insert(0, os.path.join(REPO_ROOT, "tally_tool"))
    import tally_connector as tc

    monkeypatch.setattr(tc.time, "sleep", lambda *_: None)  # skip the real retry delay
    ok, message = tc.test_connection("127.0.0.1", 1)  # nothing listens on port 1
    assert ok is False
    assert "Tally" in message


def test_tally_connector_disables_compression_negotiation(monkeypatch):
    """Regression guard for a real bug found live: an otherwise-identical
    request succeeded over plain curl (17.5MB of real data) but failed
    through this code with Tally's <CMPINFO> diagnostic fallback -- traced
    to requests' default "Accept-Encoding: gzip, deflate" header (curl sends
    none unless given --compressed), which Tally's embedded HTTP server
    appears to mishandle. Every request must explicitly ask for no
    compression to match curl's plain-request behavior."""
    sys.path.insert(0, os.path.join(REPO_ROOT, "tally_tool"))
    import tally_connector as tc

    captured = {}

    def _fake_post(url, **kwargs):
        captured.update(kwargs)
        class _FakeResp:
            status_code = 200
            text = "<ENVELOPE><OK/></ENVELOPE>"
            headers = {}
        return _FakeResp()

    monkeypatch.setattr(tc.requests, "post", _fake_post)
    tc._post("h", 1, "<x/>", context="test")
    assert captured["headers"]["Accept-Encoding"] == "identity"


def test_tally_connector_error_messages_name_which_request_failed(monkeypatch):
    """Regression guard: pull_from_tally() calls fetch_ledger_master() then
    fetch_vouchers() -- both share _post(), so without a context label a
    failure in either one produced an identical, undiagnosable error
    message. Confirmed live this ambiguity mattered: couldn't tell which of
    the two requests was actually failing from the UI alone."""
    sys.path.insert(0, os.path.join(REPO_ROOT, "tally_tool"))
    import tally_connector as tc

    def _fake_post(url, **kwargs):
        class _FakeResp:
            status_code = 200
            text = "not xml at all"
            headers = {}
        return _FakeResp()

    monkeypatch.setattr(tc.requests, "post", _fake_post)
    monkeypatch.setattr(tc.time, "sleep", lambda *_: None)  # skip the real retry delay
    with pytest.raises(tc.TallyConnectionError, match="Voucher Collection request"):
        tc._post("h", 1, "<x/>", context="Voucher Collection request")


def test_tally_connector_retries_once_then_succeeds(monkeypatch):
    """Regression guard for a real bug found live: an otherwise
    byte-identical request to Tally's XML server failed on one attempt
    (through this code) and succeeded moments later via a fresh curl call
    with no change on either side -- consistent with occasional flakiness
    in Tally's embedded HTTP server. _post() now retries once after a short
    pause before giving up."""
    sys.path.insert(0, os.path.join(REPO_ROOT, "tally_tool"))
    import tally_connector as tc

    calls = {"n": 0}

    def _fake_post(url, **kwargs):
        calls["n"] += 1
        class _FakeResp:
            status_code = 200
            headers = {}
            if calls["n"] == 1:
                text = "not xml at all"  # first attempt: malformed
            else:
                text = "<ENVELOPE><OK/></ENVELOPE>"  # second attempt: succeeds
        return _FakeResp()

    monkeypatch.setattr(tc.requests, "post", _fake_post)
    monkeypatch.setattr(tc.time, "sleep", lambda *_: None)
    root = tc._post("h", 1, "<x/>", context="test")
    assert root.find("OK") is not None
    assert calls["n"] == 2


def test_tally_connector_does_not_retry_a_timeout(monkeypatch):
    """Regression guard for a real bug found live: "Pull from Tally" over a
    wide date range failed with "did not respond in time" -- the request
    was genuinely still slow, not flaky, so the existing blind retry-once
    logic would have silently DOUBLED an already-long wait (e.g. 300s ->
    600s) instead of failing promptly with an actionable message. A timeout
    specifically must raise immediately, with no retry."""
    sys.path.insert(0, os.path.join(REPO_ROOT, "tally_tool"))
    import tally_connector as tc
    import requests as req

    calls = {"n": 0}

    def _fake_post(url, **kwargs):
        calls["n"] += 1
        raise req.exceptions.Timeout()

    monkeypatch.setattr(tc.requests, "post", _fake_post)
    monkeypatch.setattr(tc.time, "sleep", lambda *_: None)
    with pytest.raises(tc.TallyTimeoutError):
        tc._post("h", 1, "<x/>", context="test")
    assert calls["n"] == 1  # not retried


def test_tally_connector_uses_a_long_timeout_for_voucher_and_register_pulls(monkeypatch):
    """Regression guard for a real bug found live: the flat 15s timeout used
    for every request (including a wide-date-range Voucher Collection pull)
    made a genuinely slow-but-working Tally query fail with "did not
    respond in time" -- a quick company/ledger metadata request and a
    date-range-driven voucher pull are not the same kind of request and
    must not share the same short timeout."""
    sys.path.insert(0, os.path.join(REPO_ROOT, "tally_tool"))
    import tally_connector as tc
    import datetime as dt

    seen_timeouts = []

    def _fake_post(url, **kwargs):
        seen_timeouts.append(kwargs.get("timeout"))
        class _FakeResp:
            status_code = 200
            headers = {}
            text = "<ENVELOPE><DATA><COLLECTION></COLLECTION></DATA></ENVELOPE>"
        return _FakeResp()

    monkeypatch.setattr(tc.requests, "post", _fake_post)
    monkeypatch.setattr(tc.time, "sleep", lambda *_: None)

    tc.fetch_vouchers("h", 1, None, dt.date(2025, 4, 1), dt.date(2026, 3, 31))
    tc.fetch_voucher_register(
        "h", 1, None, {"Sales"}, dt.date(2025, 4, 1), dt.date(2026, 3, 31)
    )
    assert seen_timeouts == [tc._LONG_TIMEOUT, tc._LONG_TIMEOUT]


def test_tally_page_defaults_to_current_financial_year_not_a_26_year_span():
    """Regression guard for a real bug found live: the live-pull date
    pickers defaulted "From date" to 2000-01-01, so a routine "Pull from
    Tally" click queried a 26-year span by default -- easily enough for
    Tally to genuinely take longer than the (then 15s, now still bounded)
    request timeout on any company with real transaction history. Defaults
    should cover one financial year, not multiple decades."""
    src = open(os.path.join(REPO_ROOT, "_pages", "tally_hub.py"), encoding="utf-8").read()
    assert "datetime.date(2000, 1, 1)" not in src
    assert "_current_fy_start(" in src


def test_tally_hub_page_renders_without_exception():
    """Regression guard for a real bug found live: a botched merge-conflict
    resolution left a stale, superseded "Sales/Purchase Register" tab block
    behind in the (now-consolidated) Tally extraction page, referencing an
    undefined `tab_register` variable and using pd/io without importing
    them -- NameError on every single load of that page in the shipped
    .exe. Only a real render (not a source-text grep) catches this class of
    bug. Also guards the six-pages-into-one consolidation (see
    test_tally_pages_are_consolidated_into_one_hub_page below): every
    activity tab must still actually render."""
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(os.path.join(REPO_ROOT, "_pages", "tally_hub.py"))
    at.run(timeout=20)
    assert not at.exception


def test_tally_pages_are_consolidated_into_one_hub_page():
    """Regression guard for a real user request: six separate Tally sidebar
    pages (Extraction, Sales & Purchase Register, GST Summary, TDS Summary,
    Bank Reconciliation, Inventory Closing Stock), each with its own
    "connect to Tally" block, meant connecting once didn't carry over to
    the next activity -- every page required reconnecting from scratch.
    Consolidated into one page (_pages/tally_hub.py) with ONE connection
    picker shared by every activity tab below it; the six old standalone
    pages were removed rather than left as unreachable dead code."""
    for old_file in [
        "tally_extractions.py", "tally_registers.py", "tally_gst_summary.py",
        "tally_tds_summary.py", "tally_bank_recon.py", "tally_inventory.py",
    ]:
        assert not os.path.exists(os.path.join(REPO_ROOT, "_pages", old_file)), (
            f"_pages/{old_file} should have been removed -- its activity now "
            "lives as a tab in the consolidated _pages/tally_hub.py"
        )

    home_src = open(os.path.join(REPO_ROOT, "Home.py"), encoding="utf-8").read()
    assert '"_pages/tally_hub.py"' in home_src
    assert home_src.count('"_pages/tally_') == 1  # exactly one Tally nav entry, not six

    hub_src = open(os.path.join(REPO_ROOT, "_pages", "tally_hub.py"), encoding="utf-8").read()
    # One shared connection picker call, used by every activity below it.
    assert hub_src.count("render_connection_picker(") == 1
    for activity in ["Extraction", "Register", "GST", "TDS", "Bank Recon", "Inventory"]:
        assert activity.split()[0].lower() in hub_src.lower()



def test_tally_connector_defuses_unbound_namespace_prefixes(monkeypatch):
    """Regression guard for a real bug found live, and a FOURTH distinct
    shape of the same underlying problem: "unbound prefix: line 101628,
    column 5 ... the exact character expat stopped at is '<'". Tally's own
    User Defined Fields (UDFs) come back as tags like
    "<UDF:_UDF_788531506.LIST ...>" -- a namespace-prefixed tag name with
    no "xmlns:UDF=..." declared anywhere, which Python's namespace-aware
    XML parser rejects outright. Nothing here reads UDF fields, so the fix
    just stops the tag name from looking like a namespaced name at all."""
    sys.path.insert(0, os.path.join(REPO_ROOT, "tally_tool"))
    import tally_connector as tc
    import datetime as dt

    broken_response = (
        "<ENVELOPE><HEADER><VERSION>1</VERSION></HEADER><BODY><DATA><COLLECTION>"
        "<VOUCHER><DATE>20260410</DATE><VOUCHERTYPENAME>Payment</VOUCHERTYPENAME>"
        "<VOUCHERNUMBER>P/001</VOUCHERNUMBER>"
        '<UDF:_UDF_788531506.LIST DESC="" ISLIST="YES" TYPE="String" INDEX="2353">'
        "<UDF:_UDF_788531506>Some custom value</UDF:_UDF_788531506>"
        "</UDF:_UDF_788531506.LIST>"
        "<ALLLEDGERENTRIES.LIST><LEDGERNAME>Cash</LEDGERNAME><AMOUNT>-1000</AMOUNT></ALLLEDGERENTRIES.LIST>"
        "</VOUCHER>"
        "</COLLECTION></DATA></BODY></ENVELOPE>"
    )

    def _fake_post(url, **kwargs):
        class _FakeResp:
            status_code = 200
            text = broken_response
            headers = {}
        return _FakeResp()

    monkeypatch.setattr(tc.requests, "post", _fake_post)
    monkeypatch.setattr(tc.time, "sleep", lambda *_: None)
    rows = tc.fetch_vouchers("h", 1, None, dt.date(2025, 4, 1), dt.date(2026, 3, 31))
    assert len(rows) == 1
    assert rows[0]["Ledger Name"] == "Cash"
    assert rows[0]["Debit"] == 1000.0


def test_tally_connector_strips_numeric_char_refs_to_illegal_codepoints(monkeypatch):
    """Regression guard for a real bug found live, and a THIRD distinct shape
    of the same underlying problem: a "wasn't valid XML" failure whose exact
    diagnosis (from the improved error message shipped for this exact
    purpose) read "reference to invalid character number ... the exact
    character expat stopped at is '&'". Tally emitted a properly-formed
    numeric character reference like "&#3;" -- syntactically valid XML
    entity syntax, which _BARE_AMPERSAND_RE deliberately leaves alone as
    "already escaped" -- but referencing a codepoint (a C0 control char)
    that XML itself forbids regardless of how it's spelled. Confirmed
    directly against Python's own expat: a raw embedded control BYTE at that
    same codepoint parses fine, but a "&#N;" reference to it does not --
    _ILLEGAL_XML_CHARS_RE (which only matches raw bytes) can't catch this."""
    sys.path.insert(0, os.path.join(REPO_ROOT, "tally_tool"))
    import tally_connector as tc

    broken_response = (
        "<ENVELOPE><HEADER><VERSION>1</VERSION></HEADER><BODY><DATA><COLLECTION>"
        '<LEDGER NAME="X"><NAME>X</NAME><PARENT TYPE="String">&#3; Primary</PARENT>'
        "<OPENINGBALANCE>0.00</OPENINGBALANCE></LEDGER>"
        "</COLLECTION></DATA></BODY></ENVELOPE>"
    )

    def _fake_post(url, **kwargs):
        class _FakeResp:
            status_code = 200
            text = broken_response
            headers = {}
        return _FakeResp()

    monkeypatch.setattr(tc.requests, "post", _fake_post)
    monkeypatch.setattr(tc.time, "sleep", lambda *_: None)
    master = tc.fetch_ledger_master("h", 1)
    assert master["X"]["group"] == "Primary"


def test_tally_connector_preserves_legitimate_numeric_char_refs(monkeypatch):
    """Companion to the test above: a numeric reference to a LEGAL codepoint
    -- e.g. "&#8377;" for the Rupee sign, plausible in real ledger names --
    must be left completely untouched, not stripped alongside the illegal
    ones."""
    sys.path.insert(0, os.path.join(REPO_ROOT, "tally_tool"))
    import tally_connector as tc

    response = (
        "<ENVELOPE><HEADER><VERSION>1</VERSION></HEADER><BODY><DATA><COLLECTION>"
        '<LEDGER NAME="Y"><NAME>Cash &#8377;</NAME><PARENT>Primary</PARENT>'
        "<OPENINGBALANCE>0.00</OPENINGBALANCE></LEDGER>"
        "</COLLECTION></DATA></BODY></ENVELOPE>"
    )

    def _fake_post(url, **kwargs):
        class _FakeResp:
            status_code = 200
            text = response
            headers = {}
        return _FakeResp()

    monkeypatch.setattr(tc.requests, "post", _fake_post)
    monkeypatch.setattr(tc.time, "sleep", lambda *_: None)
    master = tc.fetch_ledger_master("h", 1)
    assert "Cash ₹" in master


def test_tally_connector_repairs_ampersands_across_a_large_multi_occurrence_response(monkeypatch):
    """Regression guard for a real bug found live right after shipping the
    single-ampersand fix: the first fix was verified against one bad ledger
    name, but a real Ledger Collection pull has hundreds of ledgers under
    Tally's own default "Duties & Taxes" group (virtually every GST ledger),
    so the same unescaped "&" repeats throughout a 600KB+ response. Confirms
    the sanitizer isn't a one-shot fix that only catches the first
    occurrence."""
    sys.path.insert(0, os.path.join(REPO_ROOT, "tally_tool"))
    import tally_connector as tc

    ledgers = "".join(
        f'<LEDGER NAME="GST Ledger {i}"><NAME>GST Ledger {i}</NAME>'
        f"<PARENT>Duties & Taxes</PARENT><OPENINGBALANCE>0.00</OPENINGBALANCE></LEDGER>"
        for i in range(200)
    )
    big_response = (
        "<ENVELOPE><HEADER><VERSION>1</VERSION><STATUS>1</STATUS></HEADER>"
        f"<BODY><DATA><COLLECTION>{ledgers}</COLLECTION></DATA></BODY></ENVELOPE>"
    )

    def _fake_post(url, **kwargs):
        class _FakeResp:
            status_code = 200
            text = big_response
            headers = {}
        return _FakeResp()

    monkeypatch.setattr(tc.requests, "post", _fake_post)
    monkeypatch.setattr(tc.time, "sleep", lambda *_: None)
    master = tc.fetch_ledger_master("h", 1)
    assert len(master) == 200
    assert all(v["group"] == "Duties & Taxes" for v in master.values())


def test_tally_connector_parse_error_pinpoints_the_bad_text_not_a_full_dump(monkeypatch):
    """Regression guard for a real usability bug found live: when a response
    is still malformed after sanitizing (a genuinely new/unhandled bad
    character), the error used to dump the ENTIRE response -- unusable at
    600KB+, since nobody can spot one bad character by eye in half a million
    characters shown in a UI error box. The error must instead point at the
    exact line/column and show a short surrounding snippet."""
    sys.path.insert(0, os.path.join(REPO_ROOT, "tally_tool"))
    import tally_connector as tc

    # An embedded literal quote inside an attribute value -- genuinely
    # ambiguous to auto-repair, so it should still raise, but with a short,
    # precise diagnostic instead of the whole response.
    broken_response = (
        "<ENVELOPE><HEADER><VERSION>1</VERSION></HEADER><BODY><DATA><COLLECTION>"
        '<LEDGER NAME="Deal "Special" Account"><NAME>Deal "Special" Account</NAME>'
        "<PARENT>Sundry Debtors</PARENT></LEDGER>"
        "</COLLECTION></DATA></BODY></ENVELOPE>"
    )

    def _fake_post(url, **kwargs):
        class _FakeResp:
            status_code = 200
            text = broken_response
            headers = {}
        return _FakeResp()

    monkeypatch.setattr(tc.requests, "post", _fake_post)
    monkeypatch.setattr(tc.time, "sleep", lambda *_: None)
    with pytest.raises(tc.TallyConnectionError) as exc_info:
        tc.fetch_ledger_master("h", 1)
    message = str(exc_info.value)
    assert "line" in message and "column" in message
    assert "Deal" in message  # the short context snippet, not the whole response
    assert len(message) < 1000  # nowhere near a full 600KB+ dump
    # Regression guard for a second real gap found live: the error used to
    # drop expat's own exception message (e.g. "not well-formed (invalid
    # token)" vs "mismatched tag" vs "duplicate attribute") entirely -- with
    # no way to tell which distinct failure category a report was even
    # hitting. That text, and the single exact character expat stopped at,
    # must both be present.
    assert "not well-formed" in message or "token" in message
    assert "exact character" in message


def test_tally_connector_repairs_unescaped_ampersands(monkeypatch):
    """Regression guard for a real bug found live: Tally's XML server does
    NOT escape a bare "&" in field values -- its own default "Profit & Loss
    A/c" ledger comes back as literal, invalid XML ("...A/c</NAME>...").
    ET.fromstring() rejected the whole response with "not well-formed
    (invalid token)" -- easy to miss on a short "List of Companies" response,
    near-certain on a full Ledger Collection pull with hundreds of ledgers.
    Bare "&" must be repaired to "&amp;" before parsing, without touching
    already-valid escapes."""
    sys.path.insert(0, os.path.join(REPO_ROOT, "tally_tool"))
    import tally_connector as tc

    bad_response = (
        "<ENVELOPE><HEADER><VERSION>1</VERSION><STATUS>1</STATUS></HEADER>"
        "<BODY><DATA><COLLECTION>"
        '<LEDGER NAME="Profit & Loss A/c"><NAME>Profit & Loss A/c</NAME>'
        "<PARENT>Primary</PARENT><OPENINGBALANCE>0</OPENINGBALANCE></LEDGER>"
        '<LEDGER NAME="R &amp; D Expenses"><NAME>R &amp; D Expenses</NAME>'
        "<PARENT>Indirect Expenses</PARENT><OPENINGBALANCE>1000</OPENINGBALANCE></LEDGER>"
        "</COLLECTION></DATA></BODY></ENVELOPE>"
    )

    def _fake_post(url, **kwargs):
        class _FakeResp:
            status_code = 200
            text = bad_response
            headers = {}
        return _FakeResp()

    monkeypatch.setattr(tc.requests, "post", _fake_post)
    monkeypatch.setattr(tc.time, "sleep", lambda *_: None)
    master = tc.fetch_ledger_master("h", 1)
    assert master["Profit & Loss A/c"]["group"] == "Primary"
    assert master["R & D Expenses"]["opening_balance"] == 1000.0


def test_tally_connector_treats_cmpinfo_fallback_as_a_failure(monkeypatch):
    """Regression guard for a real bug found live: Tally can return valid,
    parseable XML that is nonetheless the wrong thing -- its <CMPINFO>
    object-count diagnostic instead of the requested collection data (e.g.
    <LEDGER>0</LEDGER>) -- with no error status. Before this fix,
    list_companies() would silently iterate zero real <COMPANY> elements out
    of a CMPINFO block's unrelated <COMPANY>N</COMPANY> count tag and just
    return an empty list; now _post() raises clearly instead."""
    sys.path.insert(0, os.path.join(REPO_ROOT, "tally_tool"))
    import tally_connector as tc

    cmpinfo_response = (
        "<ENVELOPE><HEADER><VERSION>1</VERSION><STATUS>1</STATUS></HEADER>"
        "<BODY><DESC><CMPINFO><COMPANY>0</COMPANY><GROUP>0</GROUP>"
        "<LEDGER>0</LEDGER></CMPINFO></DESC></BODY></ENVELOPE>"
    )

    def _fake_post(url, **kwargs):
        class _FakeResp:
            status_code = 200
            text = cmpinfo_response
            headers = {}
        return _FakeResp()

    monkeypatch.setattr(tc.requests, "post", _fake_post)
    monkeypatch.setattr(tc.time, "sleep", lambda *_: None)
    with pytest.raises(tc.TallyConnectionError, match="diagnostic company-info summary"):
        tc._post("h", 1, "<x/>", context="Ledger Collection request")


def test_tally_connector_ignores_cmpinfo_when_real_data_is_also_present(monkeypatch):
    """Regression guard for a real bug found live right after shipping the
    CMPINFO-as-failure fix above: Tally's actual response to a working "List
    of Companies" request includes an all-zero <CMPINFO> block *and* a
    populated <COLLECTION> with the real company data in the very same
    envelope -- so treating any <CMPINFO> presence as fatal broke a request
    that was genuinely succeeding. Only an all-zero CMPINFO with NO
    accompanying collection data should raise."""
    sys.path.insert(0, os.path.join(REPO_ROOT, "tally_tool"))
    import tally_connector as tc

    mixed_response = (
        "<ENVELOPE><HEADER><VERSION>1</VERSION><STATUS>1</STATUS></HEADER>"
        "<BODY><DESC><CMPINFO><COMPANY>0</COMPANY><GROUP>0</GROUP>"
        "<LEDGER>0</LEDGER></CMPINFO></DESC>"
        "<DATA><COLLECTION>"
        '<COMPANY NAME="Databricks India Private Limited FY 24-25 (KA)">'
        '<NAME TYPE="String">Databricks India Private Limited FY 24-25 (KA)</NAME>'
        "</COMPANY>"
        "</COLLECTION></DATA></BODY></ENVELOPE>"
    )

    def _fake_post(url, **kwargs):
        class _FakeResp:
            status_code = 200
            text = mixed_response
            headers = {}
        return _FakeResp()

    monkeypatch.setattr(tc.requests, "post", _fake_post)
    monkeypatch.setattr(tc.time, "sleep", lambda *_: None)
    root = tc._post("h", 1, "<x/>", context="List of Companies request")
    names = [c.find("NAME").text for c in root.iter("COMPANY") if c.find("NAME") is not None]
    assert "Databricks India Private Limited FY 24-25 (KA)" in names


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
    diagnostic-fallback bug by default.

    The specific default was later changed (see
    test_tally_page_defaults_to_current_financial_year_not_a_26_year_span):
    2000-01-01 -- a 26-year span -- made a routine pull genuinely slow
    enough to trip Tally's request timeout, confirmed live. This test now
    only guards the original concern (populated, not None), not the exact
    literal value."""
    src = open(os.path.join(REPO_ROOT, "_pages", "tally_hub.py"), encoding="utf-8").read()
    assert "value=_current_fy_start()" in src
    assert "value=datetime.date.today()" in src


def test_tally_date_pickers_pin_explicit_min_max():
    """Regression guard for a real reported bug: st.date_input auto-computes
    its calendar's navigable range as roughly value +/- 10 years when
    min_value/max_value aren't given. Passing value=date(2000, 1, 1) without
    pinning bounds silently capped the picker at ~2010, making it impossible
    to select any recent date (confirmed live: 'From date' rendered in an
    invalid/red state at today's actual date). Every date_input on this page
    must pin explicit, wide min_value/max_value so the default value chosen
    for UX can't shrink the usable range -- consolidated into one shared
    `_DATE_KW` dict (format + min_value=1990 + max_value=2100) that every
    date_input on the page spreads in, rather than repeating the bounds
    inline at each call site."""
    src = open(os.path.join(REPO_ROOT, "_pages", "tally_hub.py"), encoding="utf-8").read()
    n = src.count("st.date_input(")
    assert n >= 4
    assert 'min_value=datetime.date(1990, 1, 1)' in src
    assert 'max_value=datetime.date(2100, 1, 1)' in src
    assert src.count("**_DATE_KW") == n


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
    src = open(os.path.join(REPO_ROOT, "_pages", "tally_hub.py"), encoding="utf-8").read()
    assert '"xml"' in src
    assert "extract_any_with_progress(" in src


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
    monkeypatch.setattr(tc, "_post", lambda host, port, xml, **kwargs: fake_root)

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
    """The Sales & Purchase Register originally moved out of
    tally_extractions.py into its own page (_pages/tally_registers.py), then
    both were folded back together into the consolidated _pages/tally_hub.py
    (see test_tally_pages_are_consolidated_into_one_hub_page) -- this guard
    now checks the register activity lives in the hub rather than assuming
    either of those now-removed standalone files."""
    src = open(os.path.join(REPO_ROOT, "_pages", "tally_hub.py"), encoding="utf-8").read()
    assert "fetch_voucher_register(" in src
    assert "Sales & Purchase Register" in src

    home_src = open(os.path.join(REPO_ROOT, "Home.py"), encoding="utf-8").read()
    assert "_pages/tally_hub.py" in home_src


def test_home_page_offers_in_app_update_check():
    """New feature: a 'Check for Updates' control in the sidebar, not just
    the silent at-launch check -- users asked to be able to see/trigger it
    themselves rather than only finding out via a screenshot-driven bug
    report weeks after a fix shipped."""
    src = open(os.path.join(REPO_ROOT, "Home.py"), encoding="utf-8").read()
    assert "Check for Updates" in src
    assert "updater.check_update_status(" in src


def test_logo_banner_renders_at_top_of_every_page():
    """User request: an "Uzumaki" logo/brand mark at the top of the tool.
    _render_logo_banner() is called from Home.py -- the st.navigation()
    entry script that runs on every page -- so it's not just on the
    landing page."""
    home_src = open(os.path.join(REPO_ROOT, "Home.py"), encoding="utf-8").read()
    assert "sa-logo-banner" in home_src
    assert "UZUMAKI" in home_src
    assert "_render_logo_banner()" in home_src

    theme_src = open(os.path.join(REPO_ROOT, "_pages", "theme.py"), encoding="utf-8").read()
    assert ".sa-logo-banner" in theme_src


def test_logo_banner_uses_the_real_uploaded_artwork():
    """Follow-up user request: replace the emoji placeholder mark with the
    user's actual logo file (uploaded to the repo as Picture1.png, cropped
    here to just the icon -- the wordmark/"SINCE 2026" text baked into that
    original image was dropped since this banner already renders its own
    "UZUMAKI" text alongside it, and "Since 2026" was explicitly asked to
    be removed). Guards the asset exists, is wired into Home.py's banner,
    and that _logo_mark_b64() has a graceful (non-crashing) fallback to the
    original emoji mark if the asset is ever missing."""
    asset_path = os.path.join(REPO_ROOT, "_pages", "assets", "logo_mark.png")
    assert os.path.exists(asset_path), "the real logo asset is missing from _pages/assets/"
    assert os.path.getsize(asset_path) > 0

    home_src = open(os.path.join(REPO_ROOT, "Home.py"), encoding="utf-8").read()
    assert "logo_mark.png" in home_src
    assert "base64" in home_src
    assert "🥷" in home_src  # fallback mark, kept for when the asset is missing


def test_girish_credit_appears_in_sidebar_and_footer():
    """User request: a personal branding credit somewhere visually pleasing
    -- placed in the sidebar (near "Check for Updates") and in the shared
    page footer so it shows up consistently across the app."""
    home_src = open(os.path.join(REPO_ROOT, "Home.py"), encoding="utf-8").read()
    assert "sa-credit" in home_src
    assert "Girish" in home_src

    theme_src = open(os.path.join(REPO_ROOT, "_pages", "theme.py"), encoding="utf-8").read()
    assert ".sa-credit" in theme_src
    assert "Girish" in theme_src


def test_about_page_exists_and_is_linked_from_home():
    """New feature: a non-technical 'How it's built' page -- tech stack,
    architecture, and connectors in plain language, no code -- so people
    can understand the effort without needing to read the repo."""
    assert os.path.exists(os.path.join(REPO_ROOT, "_pages", "about.py"))

    about_src = open(os.path.join(REPO_ROOT, "_pages", "about.py"), encoding="utf-8").read()
    # Should describe the stack conceptually, not show any actual source.
    assert "Streamlit" in about_src
    assert "pywebview" in about_src

    home_src = open(os.path.join(REPO_ROOT, "Home.py"), encoding="utf-8").read()
    assert '"_pages/about.py"' in home_src
    assert "How it's built" in home_src


def test_about_page_renders_without_exception():
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(os.path.join(REPO_ROOT, "_pages", "about.py"))
    at.run(timeout=15)
    assert not at.exception


def test_sidebar_expander_css_is_readable_on_dark_background():
    """Regression guard for a real reported bug: the 'Check for Updates'
    panel was nearly invisible -- pale text (forced light by the sidebar's
    blanket `color: #eef1fb !important` rule) on top of st.expander's own
    hardcoded white background, which wasn't scoped to exclude the sidebar.
    Any future sidebar content wrapped in st.expander depends on this
    override still being present."""
    src = open(os.path.join(REPO_ROOT, "_pages", "theme.py"), encoding="utf-8").read()
    assert '[data-testid="stSidebar"] div[data-testid="stExpander"]' in src
    # Must not still be plain white -- that's the exact bug being guarded against.
    sidebar_expander_block = src.split('[data-testid="stSidebar"] div[data-testid="stExpander"] {')[1]
    sidebar_expander_block = sidebar_expander_block.split("}")[0]
    assert "background: white" not in sidebar_expander_block


def test_sidebar_inline_code_is_readable_on_dark_background():
    """Regression guard for a real reported bug: the version number in
    "Current version: `677bcaa`" (and the "Update available: `...`" /
    Check-for-Updates error-diagnostic lines, all rendered as Markdown
    inline `code`) was invisible in the sidebar -- same root cause as the
    expander fix above, different element: Streamlit's default `code` span
    keeps its own light background regardless of surrounding theme, and the
    sidebar's blanket `color: #eef1fb !important` rule sits pale-on-pale on
    top of it."""
    src = open(os.path.join(REPO_ROOT, "_pages", "theme.py"), encoding="utf-8").read()
    assert '[data-testid="stSidebar"] code' in src
    sidebar_code_block = src.split('[data-testid="stSidebar"] code {')[1].split("}")[0]
    assert "background" in sidebar_code_block


def test_sidebar_stays_fully_opaque_during_reruns():
    """Regression guard for a real reported bug: the 'Check for Updates'
    panel (and its download progress bar) looked washed out/hard to see --
    not the earlier white-background bug, but Streamlit's own "stale
    element" dimming applied while any script rerun is in flight (the
    update download itself is one long blocking rerun, so the whole panel
    sat dimmed for its entire duration). Forces full opacity in the sidebar
    so this can't recur regardless of which Streamlit internal mechanism
    causes the dimming."""
    src = open(os.path.join(REPO_ROOT, "_pages", "theme.py"), encoding="utf-8").read()
    assert 'opacity: 1 !important' in src
    sidebar_opacity_rule = src.split('[data-testid="stSidebar"],')[1].split("}")[0]
    assert "opacity: 1 !important" in sidebar_opacity_rule


# ── Uzumaki.spec: Windows Defender/SmartScreen false-positive mitigations ──
def test_spec_disables_upx_and_embeds_version_metadata():
    """
    UPX-compressed executables are disproportionately flagged by Windows
    Defender/AV heuristics (malware also uses UPX to evade signature
    scanning), and an .exe with no version/company/product metadata at all
    is another small signal those heuristics weigh. Neither alone clears a
    SmartScreen warning (only code-signing + download reputation do that),
    but both are free, no-cost reductions in false-positive risk -- guard
    against them silently regressing back to upx=True / no version resource.
    """
    src = open(os.path.join(REPO_ROOT, "Uzumaki.spec"), encoding="utf-8").read()
    assert "upx=False" in src, "Uzumaki.spec must not re-enable UPX -- see Defender false-positive note"
    assert 'version=os.path.join(ROOT, "version_info.txt")' in src, (
        "Uzumaki.spec must embed version_info.txt as the EXE's version resource"
    )
    assert os.path.exists(os.path.join(REPO_ROOT, "version_info.txt")), (
        "version_info.txt is referenced by Uzumaki.spec but missing from the repo"
    )


def test_perform_update_and_restart_stages_a_sentinel_instead_of_swapping_itself(
    monkeypatch, tmp_path
):
    """Regression guard for a real bug found live: the app is actually TWO
    processes -- a parent that owns the pywebview window, and a headless
    Streamlit child it spawns (see launcher.py's module docstring). The
    in-app "Download & Restart" button runs inside Home.py, i.e. the CHILD.
    The old code had that process spawn the swap-and-relaunch helper AND
    hard-exit itself -- but the exe is one shared file, and the PARENT was
    still running and still had it open, so the helper's delete-then-replace
    loop could never succeed and just spun forever, while the parent's
    window sat frozen on a now-dead connection. Confirmed live: a flickering
    console window and an unresponsive app needing a manual close/reopen.

    perform_update_and_restart() must not touch the swap helper at all --
    only write a sentinel file naming the downloaded exe, for launcher.py's
    parent process to act on once it's the last one standing."""
    import updater as up

    exe_path = tmp_path / "Uzumaki.exe"
    exe_path.write_bytes(b"old exe")

    monkeypatch.setattr(up.sys, "executable", str(exe_path))
    monkeypatch.setattr(up, "is_frozen", lambda: True)
    monkeypatch.setattr(up, "_download", lambda url, dest, on_progress=None: True)

    helper_calls = []
    monkeypatch.setattr(up, "_write_and_launch_helper", lambda *a: helper_calls.append(a))

    class _StopExit(BaseException):
        pass

    def _fake_exit(code):
        raise _StopExit()

    monkeypatch.setattr(up.os, "_exit", _fake_exit)

    with pytest.raises(_StopExit):
        up.perform_update_and_restart()

    # The child must NOT have tried to perform the swap itself.
    assert helper_calls == []

    sentinel = up.update_sentinel_path(str(tmp_path))
    assert os.path.exists(sentinel)
    with open(sentinel, encoding="utf-8") as f:
        staged_new_path = f.read().strip()
    assert staged_new_path == str(tmp_path / "Uzumaki_new.exe")


def test_launcher_enables_downloads_before_creating_the_webview_window():
    """Regression guard for a real bug found live: every "⬇ Download ..."
    button across the app (workbook exports throughout the Tally hub,
    redaction/PDF tools, etc.) silently did nothing in the packaged .exe --
    no error, no save dialog, nothing. Root cause: pywebview defaults
    ALLOW_DOWNLOADS to False, so its embedded WebView2 window swallows the
    browser-side download click st.download_button() triggers. This is a
    window-level setting that must be set before webview.create_window()
    is called, not a Streamlit-side bug at all -- guard both facts:
    ALLOW_DOWNLOADS is set, and it happens before window creation."""
    src = open(os.path.join(REPO_ROOT, "launcher.py"), encoding="utf-8").read()
    assert 'webview.settings["ALLOW_DOWNLOADS"] = True' in src
    assert src.index('webview.settings["ALLOW_DOWNLOADS"] = True') < src.index("webview.create_window(")


def test_launcher_finishes_a_staged_update_once_it_is_the_last_process(monkeypatch, tmp_path):
    """Companion to the test above: launcher.py's parent process, once its
    own webview window has closed, must notice the sentinel
    perform_update_and_restart() left and THEN perform the actual
    swap-and-relaunch -- since by that point it really is the last process
    holding the exe file open."""
    import launcher as lch

    exe_path = tmp_path / "Uzumaki.exe"
    exe_path.write_bytes(b"old exe")
    new_path = tmp_path / "Uzumaki_new.exe"
    new_path.write_bytes(b"new exe")

    sentinel = lch.update_sentinel_path(str(tmp_path))
    with open(sentinel, "w", encoding="utf-8") as f:
        f.write(str(new_path))

    monkeypatch.setattr(lch.sys, "executable", str(exe_path))
    helper_calls = []
    monkeypatch.setattr(lch, "_write_and_launch_helper", lambda *a: helper_calls.append(a))

    lch._finish_pending_update()

    assert helper_calls == [(str(exe_path), str(new_path))]
    assert not os.path.exists(sentinel)  # consumed, not left behind


def test_launcher_does_nothing_when_no_update_is_staged(monkeypatch, tmp_path):
    """The common case (no update pending): _finish_pending_update() must be
    a no-op, not e.g. crash on a missing sentinel file or call the swap
    helper with garbage."""
    import launcher as lch

    exe_path = tmp_path / "Uzumaki.exe"
    exe_path.write_bytes(b"old exe")
    monkeypatch.setattr(lch.sys, "executable", str(exe_path))
    helper_calls = []
    monkeypatch.setattr(lch, "_write_and_launch_helper", lambda *a: helper_calls.append(a))

    lch._finish_pending_update()  # sentinel doesn't exist -- must not raise

    assert helper_calls == []


def test_update_swap_helper_hides_its_console_window_on_windows():
    """Regression guard for a real bug found live: the swap-and-relaunch
    helper (a "cmd /c <script>.bat") flashed a visible console window during
    an update even with DETACHED_PROCESS set -- confirmed live on a real
    Windows machine that flag alone isn't reliable for suppressing a console
    host for a spawned cmd.exe. CREATE_NO_WINDOW + a hidden STARTUPINFO is
    the combination that actually works."""
    src = open(os.path.join(REPO_ROOT, "updater.py"), encoding="utf-8").read()
    helper_src = src.split("def _write_and_launch_helper")[1].split("def ")[0]
    assert "CREATE_NO_WINDOW" in helper_src
    assert "STARTUPINFO" in helper_src
    assert "SW_HIDE" in helper_src


# ── updater.py / build-exe.yml: releases must publish to the public repo ───
def test_updater_downloads_from_public_releases_repo_not_private_source():
    """
    Regression guard for the private-repo migration: `Uzumaki` (source) is
    private, and GitHub Release assets on a private repo require an
    authenticated request -- an anonymous download (what updater.py does)
    would just fail, forever, for every distributed .exe. Releases must
    come from the separate public Uzumaki-releases repo instead, and
    build-exe.yml must publish there using a scoped PAT, never the default
    same-repo GITHUB_TOKEN (which can't write to a different repo anyway).
    """
    src = open(os.path.join(REPO_ROOT, "updater.py"), encoding="utf-8").read()
    assert 'RELEASES_REPO = "Uzumaki-releases"' in src, (
        "updater.py must download releases from the separate public "
        "Uzumaki-releases repo, not the private Uzumaki source repo"
    )
    assert '{RELEASES_OWNER}/{RELEASES_REPO}' in src.split("_GH_RELEASE_BASE")[1].split("\n")[0], (
        "_GH_RELEASE_BASE must be built from RELEASES_OWNER/RELEASES_REPO"
    )

    workflow = open(
        os.path.join(REPO_ROOT, ".github", "workflows", "build-exe.yml"), encoding="utf-8"
    ).read()
    assert "repository: GirishMGK/Uzumaki-releases" in workflow, (
        "build-exe.yml must publish releases to GirishMGK/Uzumaki-releases explicitly"
    )
    assert "secrets.RELEASES_REPO_TOKEN" in workflow, (
        "build-exe.yml must authenticate to Uzumaki-releases with the scoped "
        "RELEASES_REPO_TOKEN secret, not the default same-repo GITHUB_TOKEN"
    )


# ── _pages/loans.py: must actually start & embed the vendored backend ──────
def test_loans_page_calls_real_functions():
    src = open(os.path.join(REPO_ROOT, "_pages", "loans.py"), encoding="utf-8").read()
    for fn in ["uvicorn.run(", "st.components.v1.iframe(", "LOANS_TRUST_HOST_AUTH"]:
        assert fn in src, f"_pages/loans.py no longer calls {fn} — the tool may be disconnected"


def test_loans_backend_package_not_named_app_to_avoid_hrm_collision():
    """
    Regression guard for a real bug caught before it ever shipped: FCMR's
    own repo layout has its FastAPI app at a top-level package literally
    named `app` -- but hrm_tool/backend already has its own top-level `app`
    package, and _pages/hrm.py and _pages/loans.py both do
    sys.path.insert(0, <their own backend dir>) then `from app.main import
    app`. With two same-named top-level packages both on sys.path,
    Python's sys.modules cache means whichever tool's page happened to
    import first would silently win for BOTH -- the second tool would
    quietly run the wrong backend. Vendored under "loan_app" instead so the
    two coexist safely; guard against this regressing back to "app".
    """
    backend_dir = os.path.join(REPO_ROOT, "loans_tool", "backend")
    assert not os.path.isdir(os.path.join(backend_dir, "app")), (
        "loans_tool/backend must not have a top-level 'app' package -- "
        "collides with hrm_tool/backend's own 'app' package on sys.path"
    )
    assert os.path.isdir(os.path.join(backend_dir, "loan_app"))
    assert os.path.isfile(os.path.join(backend_dir, "loan_app", "main.py"))

    main_src = open(
        os.path.join(backend_dir, "loan_app", "main.py"), encoding="utf-8"
    ).read()
    assert "from loan_app.api import" in main_src
    assert 'settings.base_dir / "loan_app" / "web" / "static"' in main_src, (
        "static-file mount must use the renamed 'loan_app' path too, not the "
        "original 'app' -- a literal string, invisible to an import-only check"
    )

    loans_page_src = open(os.path.join(REPO_ROOT, "_pages", "loans.py"), encoding="utf-8").read()
    assert "from loan_app.main import app" in loans_page_src


def test_loans_trust_host_auth_actually_bypasses_login():
    """
    Functional test (not just a source-string check) of the security-
    relevant part of the Uzumaki integration: with LOANS_TRUST_HOST_AUTH=1
    (set by _pages/loans.py because Uzumaki's own per-user login + role-
    based tool access already gate whether this page is reachable at all),
    a request with no session must NOT be redirected to /login -- it must
    reach the real page directly, with a session auto-populated the same
    shape a real login would produce. And the flag must be OFF by default
    (standalone/Vercel/Electron-desktop deployments keep their own real
    login) -- both directions verified against the actual middleware, not
    a mock.
    """
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")

    backend_dir = os.path.join(REPO_ROOT, "loans_tool", "backend")
    sys.path.insert(0, backend_dir)
    os.environ.setdefault("FCMR_AADHAAR_HASH_SALT", "test-salt-for-pytest")
    try:
        import importlib

        os.environ.pop("LOANS_TRUST_HOST_AUTH", None)
        import loan_app.main as loan_main
        importlib.reload(loan_main)

        from fastapi.testclient import TestClient

        # Default (standalone) behavior: no bypass, real login still required.
        with TestClient(loan_main.app) as client:
            resp = client.get("/", follow_redirects=False)
            assert resp.status_code == 303
            assert resp.headers["location"] == "/login"

        # Uzumaki-embedded behavior: bypass active, no redirect, real content.
        os.environ["LOANS_TRUST_HOST_AUTH"] = "1"
        with TestClient(loan_main.app) as client:
            resp = client.get("/", follow_redirects=False)
            assert resp.status_code == 200
            assert "Engagements" in resp.text
    finally:
        os.environ.pop("LOANS_TRUST_HOST_AUTH", None)
        sys.path.remove(backend_dir)
        for mod in list(sys.modules):
            if mod == "loan_app" or mod.startswith("loan_app.") or mod == "app" or mod.startswith("app."):
                del sys.modules[mod]


# ── Home.py / Uzumaki.spec / auth.py: Loan Analytics wiring ────────────────
def test_loans_wired_into_home_spec_and_auth():
    assert os.path.exists(os.path.join(REPO_ROOT, "loans_tool", "backend", "loan_app"))
    assert os.path.exists(os.path.join(REPO_ROOT, "loans_tool", "backend", "fcmr_core"))

    home_src = open(os.path.join(REPO_ROOT, "Home.py"), encoding="utf-8").read()
    assert '"_pages/loans.py"' in home_src
    assert '"Loan Analytics"' in home_src

    spec_src = open(os.path.join(REPO_ROOT, "Uzumaki.spec"), encoding="utf-8").read()
    assert '_tree("loans_tool")' in spec_src
    assert os.path.join("loans_tool", "backend", "loan_app", "main.py") in spec_src

    auth_src = open(os.path.join(REPO_ROOT, "auth.py"), encoding="utf-8").read()
    assert '"Loan Analytics"' in auth_src, (
        "Loan Analytics must be in auth.TOOL_KEYS or an admin can never "
        "grant/restrict a role's access to it"
    )


# ── loan_app/api/ead_consolidate.py: Parquet download ───────────────────────
def test_ead_consolidate_parquet_download_round_trips():
    """
    Functional test of the EAD Consolidation "Download Parquet" button:
    hits the real route through the real app/middleware (LOANS_TRUST_HOST_AUTH
    bypass, same as production when embedded in Uzumaki), with
    _build_consolidated_df monkeypatched to a known small DataFrame so the
    test doesn't need to drive the full upload/column-mapping pipeline just
    to exercise the new serialization endpoint. Verifies the response is
    genuinely valid Parquet bytes that round-trip to the same data, not just
    a 200 status.
    """
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    pytest.importorskip("polars")

    backend_dir = os.path.join(REPO_ROOT, "loans_tool", "backend")
    sys.path.insert(0, backend_dir)
    os.environ.setdefault("FCMR_AADHAAR_HASH_SALT", "test-salt-for-pytest")
    os.environ["LOANS_TRUST_HOST_AUTH"] = "1"
    try:
        import importlib

        import loan_app.main as loan_main
        importlib.reload(loan_main)

        import io

        import polars as pl
        from fastapi.testclient import TestClient
        from loan_app.api import ead_consolidate

        expected = pl.DataFrame({"pan": ["ABCDE1234F"], "outstanding_principal": [100000.5]})
        ead_consolidate._build_consolidated_df = lambda engagement_id: expected

        with TestClient(loan_main.app) as client:
            resp = client.get("/dashboard/ead/download/parquet")
            assert resp.status_code == 200
            assert resp.headers["content-type"] == "application/octet-stream"
            assert ".parquet" in resp.headers["content-disposition"]

            round_tripped = pl.read_parquet(io.BytesIO(resp.content))
            assert round_tripped.to_dicts() == expected.to_dicts()

            # Empty result still 404s, same as the existing CSV/Excel routes.
            ead_consolidate._build_consolidated_df = lambda engagement_id: pl.DataFrame()
            resp = client.get("/dashboard/ead/download/parquet")
            assert resp.status_code == 404
    finally:
        os.environ.pop("LOANS_TRUST_HOST_AUTH", None)
        sys.path.remove(backend_dir)
        for mod in list(sys.modules):
            if mod == "loan_app" or mod.startswith("loan_app.") or mod == "app" or mod.startswith("app."):
                del sys.modules[mod]


def test_ead_download_routes_send_full_body_not_chunked():
    """
    Regression guard for a real reported bug: EAD downloads (Parquet
    especially) were painfully slow -- ~500 KB/s for a same-machine
    localhost transfer of an already-fully-buffered response. Root cause:
    StreamingResponse(io.BytesIO(data), ...) iterates the BytesIO object,
    and Python's file-iterator protocol splits it on newline bytes
    (0x0A) -- for binary data like Parquet/Excel, that's roughly one
    "chunk" every 256 bytes by pure chance, so a 150MB file became
    ~580,000 tiny ASGI send() calls. There's no actual streaming benefit
    to lose here since the full content is always built in memory first
    anyway, so the fix is a plain Response with the complete bytes.

    A chunked/streaming response never sets Content-Length (it can't know
    the total size upfront); a plain Response always does. That header's
    presence is the black-box signal this test checks, on data specifically
    engineered to contain many embedded newline bytes -- the exact
    pathological case that made this slow.
    """
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    pytest.importorskip("polars")

    backend_dir = os.path.join(REPO_ROOT, "loans_tool", "backend")
    sys.path.insert(0, backend_dir)
    os.environ.setdefault("FCMR_AADHAAR_HASH_SALT", "test-salt-for-pytest")
    os.environ["LOANS_TRUST_HOST_AUTH"] = "1"
    try:
        import importlib

        import loan_app.main as loan_main
        importlib.reload(loan_main)

        import polars as pl
        from fastapi.testclient import TestClient
        from loan_app.api import ead_consolidate

        # A string containing lots of "\n" bytes, so any accidental
        # BytesIO-line-iteration would fragment this into many chunks.
        newline_heavy = "\n".join(f"row{i}" for i in range(5000))
        expected = pl.DataFrame({"loan_id": ["L1"], "notes": [newline_heavy]})
        ead_consolidate._build_consolidated_df = lambda engagement_id: expected

        with TestClient(loan_main.app) as client:
            for path in ("csv", "parquet", "excel"):
                resp = client.get(f"/dashboard/ead/download/{path}")
                assert resp.status_code == 200
                assert "content-length" in resp.headers, (
                    f"/{path} download has no Content-Length -- looks chunked/streamed again"
                )
                assert int(resp.headers["content-length"]) == len(resp.content)
    finally:
        os.environ.pop("LOANS_TRUST_HOST_AUTH", None)
        sys.path.remove(backend_dir)
        for mod in list(sys.modules):
            if mod == "loan_app" or mod.startswith("loan_app.") or mod == "app" or mod.startswith("app."):
                del sys.modules[mod]


# ── loan_app/api/sql_analytics.py: ad-hoc SQL over ingested reports ────────
def test_sql_analytics_runs_real_query_across_report_types():
    """
    Functional test of the SQL Analytics feature end to end through the
    real app/middleware: monkeypatches store.build_consolidated_df (the
    same helper EAD Consolidation uses, now generalized) to return two
    known small DataFrames standing in for two different report types,
    then verifies a real JOIN across them executes correctly through the
    actual /run and /export routes -- not just that a table lookup
    happens, but that DuckDB genuinely joins data registered from two
    separate report types together, which is the entire point of the
    feature. Also checks the bad-SQL and no-data-yet error paths return
    a clean 400 rather than a 500.
    """
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    pytest.importorskip("polars")
    pytest.importorskip("duckdb")

    backend_dir = os.path.join(REPO_ROOT, "loans_tool", "backend")
    sys.path.insert(0, backend_dir)
    os.environ.setdefault("FCMR_AADHAAR_HASH_SALT", "test-salt-for-pytest")
    os.environ["LOANS_TRUST_HOST_AUTH"] = "1"
    try:
        import importlib

        import loan_app.main as loan_main
        importlib.reload(loan_main)

        import polars as pl
        from fastapi.testclient import TestClient
        from fcmr_core.catalog import store as catalog_store

        customer_df = pl.DataFrame({"pan": ["ABCDE1234F", "FGHIJ5678K"], "full_name": ["Alice", "Bob"]})
        ead_df = pl.DataFrame({"pan": ["ABCDE1234F"], "outstanding_principal": [100000.0]})

        def fake_build(engagement_id, report_type):
            if report_type == "customer_master":
                return customer_df
            if report_type == "ead_files":
                return ead_df
            return pl.DataFrame()

        real_build_consolidated_df = catalog_store.build_consolidated_df
        catalog_store.build_consolidated_df = fake_build

        with TestClient(loan_main.app) as client:
            # Page loads and lists both tables.
            resp = client.get("/dashboard/analytics/sql")
            assert resp.status_code == 200
            assert "customer_master" in resp.text
            assert "ead_files" in resp.text

            # A real cross-report-type JOIN, not just a single-table select.
            join_sql = (
                "SELECT c.pan, c.full_name, e.outstanding_principal "
                "FROM customer_master c LEFT JOIN ead_files e ON c.pan = e.pan "
                "ORDER BY c.pan"
            )
            resp = client.post("/dashboard/analytics/sql/run", data={"sql": join_sql})
            assert resp.status_code == 200
            body = resp.json()
            assert body["total_rows"] == 2
            assert body["rows"][0] == {
                "pan": "ABCDE1234F", "full_name": "Alice", "outstanding_principal": 100000.0,
            }
            assert body["rows"][1]["outstanding_principal"] is None  # Bob has no EAD row

            # Export produces genuine CSV of the same result.
            resp = client.post("/dashboard/analytics/sql/export", data={"sql": join_sql})
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/csv")
            assert "Alice" in resp.text and "Bob" in resp.text

            # Bad SQL -> clean 400, not a 500.
            resp = client.post("/dashboard/analytics/sql/run", data={"sql": "SELECT * FROM no_such_table"})
            assert resp.status_code == 400
            assert "error" in resp.json()

            # No tables ready yet -> clean 400 on run.
            catalog_store.build_consolidated_df = lambda engagement_id, report_type: pl.DataFrame()
            resp = client.post("/dashboard/analytics/sql/run", data={"sql": "SELECT 1"})
            assert resp.status_code == 400
    finally:
        catalog_store.build_consolidated_df = real_build_consolidated_df
        os.environ.pop("LOANS_TRUST_HOST_AUTH", None)
        sys.path.remove(backend_dir)
        for mod in list(sys.modules):
            if mod == "loan_app" or mod.startswith("loan_app.") or mod == "app" or mod.startswith("app."):
                del sys.modules[mod]


# ── loan_app/main.py: unhandled exceptions must be visible, not silent ─────
def test_unhandled_exception_is_logged_and_surfaced_not_a_bare_500():
    """
    Regression guard for a real bug report: a user's file upload failed
    with "Upload failed (status 500). Please try again." and nothing else
    -- no detail in the browser, and (before this fix) nothing in any log
    file either, since this runs as a packaged desktop app with no visible
    console for uvicorn's own stderr traceback to land on. Verifies the new
    global exception handler in main.py actually does both things: writes
    the real traceback to error.log, and returns the exception type/message
    in the JSON body instead of a bare status code. Also checks the existing
    intentional HTTPException path (e.g. "No files provided.") still
    behaves exactly as before -- the new catch-all must not swallow or
    reshape FastAPI's own 4xx handling.
    """
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")

    backend_dir = os.path.join(REPO_ROOT, "loans_tool", "backend")
    sys.path.insert(0, backend_dir)
    os.environ.setdefault("FCMR_AADHAAR_HASH_SALT", "test-salt-for-pytest")
    os.environ["LOANS_TRUST_HOST_AUTH"] = "1"
    try:
        import importlib

        import loan_app.main as loan_main
        importlib.reload(loan_main)

        from fastapi.testclient import TestClient
        from fcmr_core.config import settings as fcmr_settings

        error_log = fcmr_settings.logs_dir / "error.log"
        error_log.parent.mkdir(parents=True, exist_ok=True)
        before_size = error_log.stat().st_size if error_log.exists() else 0

        @loan_main.app.get("/__test_boom")
        def _boom():
            raise ValueError("synthetic failure for the regression test")

        with TestClient(loan_main.app, raise_server_exceptions=False) as client:
            resp = client.get("/__test_boom")
            assert resp.status_code == 500
            assert resp.json() == {"detail": "ValueError: synthetic failure for the regression test"}

            # The existing intentional-error path is untouched by the new
            # catch-all: still a real 400 with its own message, not 500.
            resp = client.post("/dashboard/upload", data={"report_type": "ead_files"})
            assert resp.status_code == 400
            assert resp.json() == {"detail": "No files provided."}

        assert error_log.exists()
        new_content = error_log.read_text(encoding="utf-8")[before_size:]
        assert "synthetic failure for the regression test" in new_content
        assert "Traceback" in new_content
    finally:
        os.environ.pop("LOANS_TRUST_HOST_AUTH", None)
        sys.path.remove(backend_dir)
        for mod in list(sys.modules):
            if mod == "loan_app" or mod.startswith("loan_app.") or mod == "app" or mod.startswith("app."):
                del sys.modules[mod]


# ── loan_app/api/uploads.py: checkpoint logging for a real bug report ──────
def test_upload_checkpoint_logging_pinpoints_progress():
    """
    Regression guard for a real bug report: an upload's progress bar hit
    100% and then just sat there -- no error, no response, ever (unlike
    the sibling unhandled-exception test above, this isn't something the
    global exception handler can help with, since nothing ever fails or
    returns). With no way to reproduce the hang itself, the actionable fix
    is to log a checkpoint at each real step of do_upload() (file read
    started/finished, disk write started/finished, DB record created) so
    that whichever line is LAST in processing.log the next time this
    happens tells us exactly where it got stuck, instead of guessing.
    Verifies a real upload through the real route produces all of those
    checkpoint lines, in order, for a real (if small) file.
    """
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")

    backend_dir = os.path.join(REPO_ROOT, "loans_tool", "backend")
    sys.path.insert(0, backend_dir)
    os.environ.setdefault("FCMR_AADHAAR_HASH_SALT", "test-salt-for-pytest")
    os.environ["LOANS_TRUST_HOST_AUTH"] = "1"
    try:
        import importlib

        import loan_app.main as loan_main
        importlib.reload(loan_main)

        from fastapi.testclient import TestClient
        from fcmr_core.config import settings as fcmr_settings

        processing_log = fcmr_settings.logs_dir / "processing.log"
        processing_log.parent.mkdir(parents=True, exist_ok=True)
        before_size = processing_log.stat().st_size if processing_log.exists() else 0

        with TestClient(loan_main.app) as client:
            resp = client.post(
                "/dashboard/upload",
                data={"report_type": "ead_files"},
                files={"files": ("checkpoint_test.csv", b"PAN,DrsPOS\nABCDE1234F,1000\n", "text/csv")},
                follow_redirects=False,
            )
            assert resp.status_code == 303

        assert processing_log.exists()
        new_content = processing_log.read_text(encoding="utf-8")[before_size:]
        expected_in_order = [
            "Upload request received: 1 file(s)",
            "Reading uploaded file: checkpoint_test.csv",
            "Read checkpoint_test.csv",
            "Creating upload record for checkpoint_test.csv",
            "Writing checkpoint_test.csv to disk",
            "Finished writing checkpoint_test.csv to disk",
            "ready for column mapping",
            "Upload request complete: 1 file(s) processed",
        ]
        positions = [new_content.find(line) for line in expected_in_order]
        assert all(p != -1 for p in positions), (expected_in_order, new_content)
        assert positions == sorted(positions), "checkpoint lines out of order"
    finally:
        os.environ.pop("LOANS_TRUST_HOST_AUTH", None)
        sys.path.remove(backend_dir)
        for mod in list(sys.modules):
            if mod == "loan_app" or mod.startswith("loan_app.") or mod == "app" or mod.startswith("app."):
                del sys.modules[mod]


# ── loan_app/api/uploads.py + fcmr_core/catalog/store.py: delete upload ────
def test_delete_upload_removes_db_row_and_file_from_disk():
    """
    Regression guard for a real feature gap: there was no way to remove an
    upload once created -- a user who uploaded the wrong file (or 49 of
    them at once) had no cleanup option. Verifies the real DELETE route,
    through the real app, actually removes both the DB row (so it
    disappears from the dashboard) and the raw CSV file still on disk
    (mapping_pending uploads keep theirs until ingested), not just one or
    the other. Also checks deleting a nonexistent upload_id 404s instead
    of silently succeeding or crashing.
    """
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")

    backend_dir = os.path.join(REPO_ROOT, "loans_tool", "backend")
    sys.path.insert(0, backend_dir)
    os.environ.setdefault("FCMR_AADHAAR_HASH_SALT", "test-salt-for-pytest")
    os.environ["LOANS_TRUST_HOST_AUTH"] = "1"
    try:
        import importlib

        import loan_app.main as loan_main
        importlib.reload(loan_main)

        from pathlib import Path

        from fastapi.testclient import TestClient
        from fcmr_core.catalog import store as catalog_store

        with TestClient(loan_main.app) as client:
            resp = client.post(
                "/dashboard/upload",
                data={"report_type": "ead_files"},
                files={"files": ("to_delete.csv", b"PAN,DrsPOS\nABCDE1234F,1000\n", "text/csv")},
                follow_redirects=False,
            )
            assert resp.status_code == 303

            uploads = catalog_store.list_uploads()
            match = [u for u in uploads if u["filename"] == "to_delete.csv"]
            assert len(match) == 1
            upload_id = match[0]["upload_id"]
            csv_path = Path(match[0]["csv_path"])
            assert csv_path.exists()

            resp = client.post(f"/dashboard/uploads/{upload_id}/delete", follow_redirects=False)
            assert resp.status_code == 303
            assert resp.headers["location"] == "/dashboard"

            assert catalog_store.get_upload(upload_id) is None
            assert not csv_path.exists()

            # Deleting again (already gone) is a clean 404, not a crash.
            resp = client.post(f"/dashboard/uploads/{upload_id}/delete")
            assert resp.status_code == 404
    finally:
        os.environ.pop("LOANS_TRUST_HOST_AUTH", None)
        sys.path.remove(backend_dir)
        for mod in list(sys.modules):
            if mod == "loan_app" or mod.startswith("loan_app.") or mod == "app" or mod.startswith("app."):
                del sys.modules[mod]


# ── fcmr_core/catalog/store.py: batch upload reuses one connection ─────────
def test_batch_upload_reuses_one_connection_not_one_per_call():
    """
    Regression guard for the perceived-hang-on-large-batch report: a
    49-file upload took long enough (49 files x 2 store calls each x a
    fresh duckdb.connect() per call = 98 connection cycles) that the user
    assumed it had frozen, even though it eventually completed. Verifies
    do_upload() now passes one shared connection through to
    create_upload()/set_mapping_pending() for every file in a batch, by
    counting real store.open_connection() calls during a 5-file upload --
    should be exactly 1, not 5 or 10.
    """
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")

    backend_dir = os.path.join(REPO_ROOT, "loans_tool", "backend")
    sys.path.insert(0, backend_dir)
    os.environ.setdefault("FCMR_AADHAAR_HASH_SALT", "test-salt-for-pytest")
    os.environ["LOANS_TRUST_HOST_AUTH"] = "1"
    try:
        import importlib

        import loan_app.main as loan_main
        importlib.reload(loan_main)

        from fastapi.testclient import TestClient
        from fcmr_core.catalog import store as catalog_store

        call_count = 0
        real_open_connection = catalog_store.open_connection

        def counting_open_connection():
            nonlocal call_count
            call_count += 1
            return real_open_connection()

        catalog_store.open_connection = counting_open_connection

        with TestClient(loan_main.app) as client:
            files = [
                ("files", (f"batch_{i}.csv", b"PAN,DrsPOS\nABCDE1234F,1000\n", "text/csv"))
                for i in range(5)
            ]
            resp = client.post(
                "/dashboard/upload",
                data={"report_type": "ead_files"},
                files=files,
                follow_redirects=False,
            )
            assert resp.status_code == 303
        assert call_count == 1, f"expected 1 shared connection for a 5-file batch, got {call_count}"
    finally:
        catalog_store.open_connection = real_open_connection
        os.environ.pop("LOANS_TRUST_HOST_AUTH", None)
        sys.path.remove(backend_dir)
        for mod in list(sys.modules):
            if mod == "loan_app" or mod.startswith("loan_app.") or mod == "app" or mod.startswith("app."):
                del sys.modules[mod]


# ── loan_app/api/uploads.py: map once, apply to all matching files ─────────
def test_map_columns_apply_to_matching_ingests_same_layout_files_only():
    """
    Regression guard for the real complaint: uploading many files with the
    identical column layout meant clicking through the same column-mapping
    confirmation once per file. Verifies the real "apply to matching" flow
    end to end through the real routes: three files with identical headers
    plus one with different headers, all pending. Mapping the first file
    with apply_to_matching=1 must ingest all three identical-layout files
    (status -> ready) and leave the differently-shaped fourth one alone
    (still mapping_pending) -- matching must be based on actual header
    content, not just report_type. Also checks the GET page reports the
    correct matching_count (2, not counting itself or the mismatched file).
    """
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")

    backend_dir = os.path.join(REPO_ROOT, "loans_tool", "backend")
    sys.path.insert(0, backend_dir)
    os.environ.setdefault("FCMR_AADHAAR_HASH_SALT", "test-salt-for-pytest")
    os.environ["LOANS_TRUST_HOST_AUTH"] = "1"
    try:
        import importlib

        import loan_app.main as loan_main
        importlib.reload(loan_main)

        from fastapi.testclient import TestClient
        from fcmr_core.catalog import store as catalog_store

        same_layout_csv = b"loan_id,DrsPOS\nLN0001,1000\n"
        different_layout_csv = b"loan_id,DrsPOS,ExtraColumn\nLN0001,1000,x\n"

        with TestClient(loan_main.app) as client:
            resp = client.post(
                "/dashboard/upload",
                data={"report_type": "ead_files"},
                files=[
                    ("files", ("match_a.csv", same_layout_csv, "text/csv")),
                    ("files", ("match_b.csv", same_layout_csv, "text/csv")),
                    ("files", ("match_c.csv", same_layout_csv, "text/csv")),
                    ("files", ("no_match.csv", different_layout_csv, "text/csv")),
                ],
                follow_redirects=False,
            )
            assert resp.status_code == 303

            uploads = {u["filename"]: u for u in catalog_store.list_uploads()}
            first_id = uploads["match_a.csv"]["upload_id"]

            # The GET page reports exactly 2 other matching files (b and c),
            # not the mismatched file and not itself.
            resp = client.get(f"/dashboard/uploads/{first_id}/map-columns")
            assert resp.status_code == 200
            assert "Apply this mapping to <strong>2</strong> other pending file" in resp.text

            resp = client.post(
                f"/dashboard/uploads/{first_id}/map-columns",
                data={"map_loan_id": "loan_id", "map_outstanding_principal": "DrsPOS", "apply_to_matching": "1"},
                follow_redirects=False,
            )
            assert resp.status_code == 303
            assert resp.headers["location"] == "/dashboard"

            uploads = {u["filename"]: u for u in catalog_store.list_uploads()}
            assert uploads["match_a.csv"]["status"] == "ready"
            assert uploads["match_b.csv"]["status"] == "ready"
            assert uploads["match_c.csv"]["status"] == "ready"
            # The differently-shaped file was never touched by the batch apply.
            assert uploads["no_match.csv"]["status"] == "mapping_pending"
    finally:
        os.environ.pop("LOANS_TRUST_HOST_AUTH", None)
        sys.path.remove(backend_dir)
        for mod in list(sys.modules):
            if mod == "loan_app" or mod.startswith("loan_app.") or mod == "app" or mod.startswith("app."):
                del sys.modules[mod]


# ── loan_app/api/uploads.py: bulk delete uploads ────────────────────────────
def test_bulk_delete_removes_selected_uploads_and_ignores_bad_ids():
    """
    Regression guard for a real cleanup problem: a batch upload that
    appeared stuck got retried several times before the user realized each
    attempt had actually succeeded (see the checkpoint-logging fix), piling
    up many duplicate mapping_pending uploads that would be painful to
    remove one Delete click at a time. Verifies the real bulk-delete route,
    through the real app: removes exactly the selected uploads (DB row +
    on-disk CSV, same as the single-delete path), leaves an unselected
    upload untouched, and doesn't error on a bogus/already-gone id mixed
    into the same request.
    """
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")

    backend_dir = os.path.join(REPO_ROOT, "loans_tool", "backend")
    sys.path.insert(0, backend_dir)
    os.environ.setdefault("FCMR_AADHAAR_HASH_SALT", "test-salt-for-pytest")
    os.environ["LOANS_TRUST_HOST_AUTH"] = "1"
    try:
        import importlib

        import loan_app.main as loan_main
        importlib.reload(loan_main)

        from pathlib import Path

        from fastapi.testclient import TestClient
        from fcmr_core.catalog import store as catalog_store

        with TestClient(loan_main.app) as client:
            resp = client.post(
                "/dashboard/upload",
                data={"report_type": "ead_files"},
                files=[
                    ("files", ("delete_me_1.csv", b"loan_id\nLN1\n", "text/csv")),
                    ("files", ("delete_me_2.csv", b"loan_id\nLN2\n", "text/csv")),
                    ("files", ("keep_me.csv", b"loan_id\nLN3\n", "text/csv")),
                ],
                follow_redirects=False,
            )
            assert resp.status_code == 303

            uploads = {u["filename"]: u for u in catalog_store.list_uploads()}
            id_1 = uploads["delete_me_1.csv"]["upload_id"]
            id_2 = uploads["delete_me_2.csv"]["upload_id"]
            keep_id = uploads["keep_me.csv"]["upload_id"]
            csv_path_1 = Path(uploads["delete_me_1.csv"]["csv_path"])
            assert csv_path_1.exists()

            resp = client.post(
                "/dashboard/uploads/bulk-delete",
                data={"upload_ids": [id_1, id_2, "not-a-real-upload-id"]},
                follow_redirects=False,
            )
            assert resp.status_code == 303
            assert resp.headers["location"] == "/dashboard"

            assert catalog_store.get_upload(id_1) is None
            assert catalog_store.get_upload(id_2) is None
            assert not csv_path_1.exists()
            # The upload that wasn't selected is untouched.
            assert catalog_store.get_upload(keep_id) is not None
    finally:
        os.environ.pop("LOANS_TRUST_HOST_AUTH", None)
        sys.path.remove(backend_dir)
        for mod in list(sys.modules):
            if mod == "loan_app" or mod.startswith("loan_app.") or mod == "app" or mod.startswith("app."):
                del sys.modules[mod]


# ── loan_app: Confirm/Run buttons give feedback for slow synchronous work ──
def test_slow_form_buttons_disable_and_relabel_on_submit():
    """
    Regression guard for "the button isn't working": Confirm Mapping &
    Ingest, Run Analytics, and Run Selected Rules all POST a plain HTML
    form and do real synchronous work server-side (CSV ingestion / rule
    execution) with no other progress indicator. Without a submit handler
    that visibly disables and relabels the button, a slow response looks
    identical to a click that was never registered. This checks the
    rendered pages actually wire each button up.
    """
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")

    backend_dir = os.path.join(REPO_ROOT, "loans_tool", "backend")
    sys.path.insert(0, backend_dir)
    os.environ.setdefault("FCMR_AADHAAR_HASH_SALT", "test-salt-for-pytest")
    os.environ["LOANS_TRUST_HOST_AUTH"] = "1"
    try:
        import importlib

        import loan_app.main as loan_main
        importlib.reload(loan_main)

        from fastapi.testclient import TestClient

        csv_bytes = b"loan_id,DrsPOS\nLN0001,1000\n"

        with TestClient(loan_main.app) as client:
            resp = client.post(
                "/dashboard/upload",
                data={"report_type": "ead_files"},
                files=[("files", ("feedback.csv", csv_bytes, "text/csv"))],
                follow_redirects=False,
            )
            assert resp.status_code == 303

            from fcmr_core.catalog import store as catalog_store

            upload_id = next(
                u["upload_id"] for u in catalog_store.list_uploads() if u["filename"] == "feedback.csv"
            )

            # Column-mapping page: Confirm button disables + relabels on submit.
            resp = client.get(f"/dashboard/uploads/{upload_id}/map-columns")
            assert resp.status_code == 200
            assert 'id="confirm-mapping-btn"' in resp.text
            assert "getElementById('map-columns-form')" in resp.text
            assert "btn.textContent = 'Ingesting" in resp.text

            resp = client.post(
                f"/dashboard/uploads/{upload_id}/map-columns",
                data={"map_loan_id": "loan_id", "map_outstanding_principal": "DrsPOS"},
                follow_redirects=False,
            )
            assert resp.status_code == 303

            # Upload-detail page: Run Analytics button disables + relabels on submit.
            resp = client.get(f"/dashboard/uploads/{upload_id}")
            assert resp.status_code == 200
            assert 'id="run-all-btn"' in resp.text
            assert "getElementById('run_form')" in resp.text
            assert "btn.textContent = 'Running" in resp.text
    finally:
        os.environ.pop("LOANS_TRUST_HOST_AUTH", None)
        sys.path.remove(backend_dir)
        for mod in list(sys.modules):
            if mod == "loan_app" or mod.startswith("loan_app.") or mod == "app" or mod.startswith("app."):
                del sys.modules[mod]


# ── fcmr_core/config.py: DuckDB OOM during EAD consolidation ───────────────
def test_apply_duckdb_limits_sets_higher_memory_ceiling_and_disables_order():
    """
    Regression guard for a real "OutOfMemoryException: ArrowBuffer: failed
    to allocate ... bytes" crash surfaced (via the global exception
    handler) while consolidating/downloading a batch of real-world EAD
    files -- several hundred thousand rows each, a dozen-plus files.
    Materializing a plain `SELECT *` result to Arrow/Polars can't spill to
    the configured temp_directory the way an intermediate sort or join
    can, so the original 3/6/12 GB per-tier caps were tight enough to OOM
    on that in practice. Verifies the raised per-tier ceilings and that
    preserve_insertion_order (one of DuckDB's own suggested remedies in
    that error message, and safe here since nothing relies on row order
    out of a plain scan) is actually applied to a real connection.
    """
    pytest.importorskip("duckdb")

    backend_dir = os.path.join(REPO_ROOT, "loans_tool", "backend")
    sys.path.insert(0, backend_dir)
    try:
        import duckdb

        from fcmr_core.config import _DUCK_LIMITS, apply_duckdb_limits

        # Every tier keeps some headroom above the old 3/6/12 GB caps.
        assert _DUCK_LIMITS["low"]["memory_gb"] > 3
        assert _DUCK_LIMITS["mid"]["memory_gb"] > 6
        assert _DUCK_LIMITS["high"]["memory_gb"] > 12

        con = duckdb.connect(":memory:")
        try:
            apply_duckdb_limits(con)
            assert con.execute(
                "SELECT current_setting('preserve_insertion_order')"
            ).fetchone() == (False,)
        finally:
            con.close()
    finally:
        sys.path.remove(backend_dir)


# ── loan_app: EAD "System" -> Product Type tagging (Product Helper) ────────
def test_product_helper_blocks_on_unmapped_system_then_tags_after_settings_add():
    """
    Regression guard for the new "Product Helper" feature: EAD/Technical
    Writeoff rows get tagged with a Product Type derived from their
    `System` column, via a persisted System -> Type lookup (seeded with 22
    known values, editable at Settings). Verifies the full real flow:
    1. A file with one known System value and one unknown one blocks
       ingestion at the mapping-confirm step with the unmapped value named.
    2. Adding that mapping via the real Settings route, then re-confirming
       the same mapping, ingests successfully.
    3. The ingested data's `product_helper` column is correct for both the
       pre-seeded value (OneLMS_TW -> TW) and the newly-added one.
    """
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")

    backend_dir = os.path.join(REPO_ROOT, "loans_tool", "backend")
    sys.path.insert(0, backend_dir)
    os.environ.setdefault("FCMR_AADHAAR_HASH_SALT", "test-salt-for-pytest")
    os.environ["LOANS_TRUST_HOST_AUTH"] = "1"
    try:
        import importlib

        import loan_app.main as loan_main
        importlib.reload(loan_main)

        from fastapi.testclient import TestClient
        from fcmr_core.catalog import store as catalog_store

        csv_bytes = (
            b"loan_id,System\n"
            b"LN0001,OneLMS_TW\n"
            b"LN0002,MyBrandNewSystem\n"
        )

        with TestClient(loan_main.app) as client:
            resp = client.post(
                "/dashboard/upload",
                data={"report_type": "ead_files"},
                files=[("files", ("product_helper.csv", csv_bytes, "text/csv"))],
                follow_redirects=False,
            )
            assert resp.status_code == 303

            upload_id = next(
                u["upload_id"]
                for u in catalog_store.list_uploads()
                if u["filename"] == "product_helper.csv"
            )

            # Confirming the mapping should block: "MyBrandNewSystem" isn't
            # in the System -> Type lookup yet.
            resp = client.post(
                f"/dashboard/uploads/{upload_id}/map-columns",
                data={"map_loan_id": "loan_id", "map_system": "System"},
                follow_redirects=False,
            )
            assert resp.status_code == 200
            assert "MyBrandNewSystem" in resp.text
            assert "OneLMS_TW" not in resp.text  # only the *unmapped* value is listed

            upload = catalog_store.get_upload(upload_id)
            assert upload["status"] == "mapping_pending"  # never touched, not marked failed

            # Add the missing mapping via the real Settings route.
            resp = client.post(
                "/settings/system-types",
                data={"system_value": "MyBrandNewSystem", "type_value": "CUSTOM"},
            )
            assert resp.status_code == 200
            assert catalog_store.get_system_type_map()["MyBrandNewSystem"] == "CUSTOM"

            # Re-confirming the same mapping now succeeds.
            resp = client.post(
                f"/dashboard/uploads/{upload_id}/map-columns",
                data={"map_loan_id": "loan_id", "map_system": "System"},
                follow_redirects=False,
            )
            assert resp.status_code == 303

            df = catalog_store.get_upload_df(upload_id).sort("loan_id")
            assert df["product_helper"].to_list() == ["TW", "CUSTOM"]
    finally:
        os.environ.pop("LOANS_TRUST_HOST_AUTH", None)
        sys.path.remove(backend_dir)
        for mod in list(sys.modules):
            if mod == "loan_app" or mod.startswith("loan_app.") or mod == "app" or mod.startswith("app."):
                del sys.modules[mod]


def test_system_type_map_seeded_with_known_defaults():
    """The 22-entry System -> Type lookup ships pre-seeded (from
    fcmr_core.catalog.store._DEFAULT_SYSTEM_TYPE_MAP) so a fresh install
    doesn't need every known system mapped by hand."""
    backend_dir = os.path.join(REPO_ROOT, "loans_tool", "backend")
    sys.path.insert(0, backend_dir)
    try:
        from fcmr_core.catalog import store as catalog_store

        catalog_store.init_catalog()
        mapping = catalog_store.get_system_type_map()
        assert mapping["OneLMS_TW"] == "TW"
        assert mapping["SCF"] == "BL"
        assert mapping["FEDERAL_BANK"] == "FEDERAL_BANK"
        assert len(mapping) >= 22
    finally:
        sys.path.remove(backend_dir)


# ── ead_consolidator.py: standalone EAD upload/map/consolidate tool ────────
def test_ead_consolidator_maps_consolidates_and_tags_product_helper():
    """
    Regression guard for the new standalone EAD Consolidator tool
    (replaces Parquet Tool in the hub sidebar): upload -> map columns ->
    consolidate -> download, with no login/engagement, reusing the same
    canonical EAD schema and System -> Product Type lookup as Loan
    Analytics. Verifies the real functions end to end: auto-suggested
    mapping picks up known aliases, an unmapped System value is detected
    (not silently tagged null), and once mapped, consolidating multiple
    files stacks them and tags product_helper correctly for both a
    pre-seeded and a newly-added System value.
    """
    pytest.importorskip("polars")

    sys.path.insert(0, REPO_ROOT)
    backend_dir = os.path.join(REPO_ROOT, "loans_tool", "backend")
    sys.path.insert(0, backend_dir)
    os.environ.setdefault("FCMR_AADHAAR_HASH_SALT", "test-salt-for-pytest")
    try:
        import polars as pl

        from fcmr_core.catalog import store as loan_store

        loan_store.init_catalog()

        import ead_consolidator as ec

        df1 = pl.DataFrame(
            {"AgreementNo": ["L1", "L2"], "System": ["OneLMS_TW", "BrandNewTestSys"], "EAD": [100.0, 200.0]}
        )
        df2 = pl.DataFrame({"AgreementNo": ["L3"], "System": ["SCF"], "EAD": [50.0]})

        suggested = ec._suggested_mapping(df1.columns)
        assert suggested == {"loan_id": "AgreementNo", "system": "System", "ead": "EAD"}
        user_mapping = {raw: canonical for canonical, raw in suggested.items()}

        unmapped = ec._unmapped_system_values([df1, df2], "System")
        assert unmapped == ["BrandNewTestSys"]

        loan_store.set_system_type("BrandNewTestSys", "CUSTOM_TYPE")
        assert ec._unmapped_system_values([df1, df2], "System") == []

        consolidated = ec._consolidate([df1, df2], ["f1.csv", "f2.csv"], user_mapping)
        assert consolidated["loan_id"].to_list() == ["L1", "L2", "L3"]
        assert consolidated["product_helper"].to_list() == ["TW", "CUSTOM_TYPE", "BL"]
        assert consolidated["_source_file"].to_list() == ["f1.csv", "f1.csv", "f2.csv"]
    finally:
        os.environ.pop("FCMR_AADHAAR_HASH_SALT", None)
        sys.path.remove(backend_dir)
        sys.path.remove(REPO_ROOT)
        for mod in list(sys.modules):
            if mod == "ead_consolidator":
                del sys.modules[mod]


def test_ead_consolidator_retires_uploader_widget_to_avoid_memory_error():
    """Regression guard for a real production crash: MemoryError raised
    inside Streamlit's own st.file_uploader registration (copy.deepcopy of
    the widget's buffered value), reported after consolidating a large
    multi-file batch. Streamlit deep-copies a widget's current value on
    every script rerun for its own change-detection bookkeeping; for a
    many-GB batch of EAD files sitting in one long-lived file_uploader,
    every later rerun (Confirm Mapping, saving a System Type mapping,
    Start Over, ...) re-duplicated the whole payload and eventually
    exhausted memory. The fix: capture the uploaded bytes into a plain
    session_state key (never deep-copied by that mechanism) exactly once,
    then rotate the uploader's `key` so later reruns register an empty
    widget instead of the huge one. This checks the source for that
    pattern rather than driving a full Streamlit rerun cycle, since
    AppTest cannot simulate a real multi-rerun file_uploader session.
    """
    with open(os.path.join(REPO_ROOT, "ead_consolidator.py"), encoding="utf-8") as f:
        src = f.read()
    assert 'st.session_state.get("ec_uploader_gen"' in src
    assert 'key=f"ec_uploader_{uploader_gen}"' in src
    assert 'st.session_state["ec_raw_uploads"] = [(f.name, f.getvalue()) for f in new_uploads]' in src
    assert 'st.session_state["ec_uploader_gen"] = uploader_gen + 1' in src
    # The uploader widget's own return value must not be read again for
    # downstream processing -- only the plain session_state copy should be.
    assert 'raw_uploads = st.session_state.get("ec_raw_uploads")' in src


def test_ead_consolidator_download_buttons_survive_excel_row_limit():
    """Regression guard: consolidating enough rows to exceed Excel's
    1,048,576-rows-per-sheet limit used to make to_excel() raise inside a
    single build pass shared with CSV/Parquet, killing the whole script
    before any of the three download buttons rendered -- so a real
    23-file/8.8M-row consolidation showed *no* download option at all,
    even though CSV/Parquet would have been fine. _build_downloads()
    builds each format independently: Excel is skipped with a clear
    reason instead of raising, and CSV/Parquet are still produced.
    """
    pytest.importorskip("polars")
    sys.path.insert(0, REPO_ROOT)
    try:
        import polars as pl

        import ead_consolidator as ec

        small = pl.DataFrame({"loan_id": ["L1", "L2"], "ead": [10.0, 20.0]})
        small_downloads = ec._build_downloads(small)
        assert small_downloads["csv"]["error"] is None
        assert small_downloads["csv"]["data"]
        assert small_downloads["excel"]["skipped_reason"] is None
        assert small_downloads["excel"]["error"] is None
        assert small_downloads["excel"]["data"]
        assert small_downloads["parquet"]["error"] is None
        assert small_downloads["parquet"]["data"]

        big = pl.DataFrame({"loan_id": ["L1"] * (ec.EXCEL_ROW_LIMIT + 1)})
        big_downloads = ec._build_downloads(big)
        assert big_downloads["excel"]["skipped_reason"] is not None
        assert big_downloads["excel"]["data"] is None
        assert big_downloads["excel"]["error"] is None
        # CSV and Parquet must still succeed even though Excel was skipped.
        assert big_downloads["csv"]["error"] is None
        assert big_downloads["csv"]["data"]
        assert big_downloads["parquet"]["error"] is None
        assert big_downloads["parquet"]["data"]
    finally:
        sys.path.remove(REPO_ROOT)
        for mod in list(sys.modules):
            if mod == "ead_consolidator":
                del sys.modules[mod]


def test_ead_consolidator_mapping_form_places_label_beside_dropdown():
    """Regression guard: the ~45-field mapping form used to stack each
    field's label above its dropdown (st.selectbox(label, ...) with the
    default visible label), making the form very tall to scroll through.
    It should instead put the label and the dropdown side by side via
    st.columns, with the widget's own label collapsed so it isn't shown
    twice."""
    with open(os.path.join(REPO_ROOT, "ead_consolidator.py"), encoding="utf-8") as f:
        src = f.read()
    assert 'label_col, field_col = st.columns([1, 2])' in src
    assert 'label_visibility="collapsed"' in src


def test_parquet_tool_removed_and_ead_consolidator_registered():
    """Parquet Tool was retired in favor of EAD Consolidator; guards
    against either the old files coming back or the new tool being
    dropped from the hub's nav/permissions wiring."""
    assert not os.path.exists(os.path.join(REPO_ROOT, "parquet_tool.py"))
    assert not os.path.exists(os.path.join(REPO_ROOT, "_pages", "parquet.py"))
    assert os.path.exists(os.path.join(REPO_ROOT, "ead_consolidator.py"))
    assert os.path.exists(os.path.join(REPO_ROOT, "_pages", "ead_consolidator.py"))

    with open(os.path.join(REPO_ROOT, "auth.py"), encoding="utf-8") as f:
        auth_src = f.read()
    assert '"Parquet Tool"' not in auth_src
    assert '"EAD Consolidator"' in auth_src

    with open(os.path.join(REPO_ROOT, "Home.py"), encoding="utf-8") as f:
        home_src = f.read()
    assert '"Parquet Tool"' not in home_src
    assert '"EAD Consolidator"' in home_src
    # Loan Analytics moved to the top of both the card catalogue and the
    # sidebar nav dict -- it's the first title to appear in each.
    assert home_src.index('"title": "Loan Analytics"') < home_src.index('"title": "EAD Consolidator"')
    assert home_src.index('"Loan Analytics": st.Page') < home_src.index('"EAD Consolidator": st.Page')


def test_ead_consolidator_expands_zip_and_bare_csv_uploads():
    """Regression guard for "select a folder or zip files, 2GB per file":
    a .zip in the upload list is extracted in place (every .csv inside it
    becomes its own entry, non-.csv entries ignored) and a bare .csv passes
    through unchanged -- both end up as flat (filename, csv_bytes) pairs
    ready for _consolidate(), regardless of which form they arrived in.
    """
    import io
    import zipfile
    from dataclasses import dataclass

    sys.path.insert(0, REPO_ROOT)
    try:
        import ead_consolidator as ec

        @dataclass
        class _FakeUpload:
            name: str
            _data: bytes

            def getvalue(self) -> bytes:
                return self._data

        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w") as zf:
            zf.writestr("folder/a.csv", "loan_id\nL1\n")
            zf.writestr("folder/b.csv", "loan_id\nL2\n")
            zf.writestr("folder/readme.txt", "not a csv")

        uploads = [
            _FakeUpload("batch.zip", zip_buf.getvalue()),
            _FakeUpload("standalone.csv", b"loan_id\nL3\n"),
        ]

        expanded = ec._expand_uploads(uploads)
        names = sorted(name for name, _ in expanded)
        assert names == ["a.csv", "b.csv", "standalone.csv"]
        assert dict(expanded)["a.csv"] == b"loan_id\nL1\n"
    finally:
        sys.path.remove(REPO_ROOT)
        for mod in list(sys.modules):
            if mod == "ead_consolidator":
                del sys.modules[mod]


def test_launcher_and_streamlit_config_allow_2gb_uploads():
    """Regression guard: EAD exports can be up to 2 GB each, so both the
    packaged app's launch flags and the dev-mode .streamlit/config.toml
    need to actually raise Streamlit's default 200MB upload cap, not just
    the ead_consolidator.py UI copy claiming they do."""
    with open(os.path.join(REPO_ROOT, "launcher.py"), encoding="utf-8") as f:
        launcher_src = f.read()
    assert "--server.maxUploadSize=2048" in launcher_src

    config_path = os.path.join(REPO_ROOT, ".streamlit", "config.toml")
    assert os.path.exists(config_path)
    with open(config_path, encoding="utf-8") as f:
        config_src = f.read()
    assert "maxUploadSize" in config_src and "2048" in config_src


# ── loan_app/api/uploads.py: accept Excel/Parquet, not just CSV, up to 5GB ──
def test_loan_app_upload_accepts_excel_and_parquet_not_just_csv():
    """
    Regression guard: the main Loan Analytics upload screen only accepted
    .csv/.zip before -- real EAD exports sometimes arrive as .xlsx or
    .parquet directly. Both get converted to CSV internally (so the rest
    of the pipeline -- column mapping, ingestion, Product Helper tagging
    -- is unchanged) and end up ingestible exactly like a native CSV
    upload of the same data would. Also checks the 5 GB limit (up from
    2 GB) is what's actually configured, not just claimed in the UI copy.
    """
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    pytest.importorskip("openpyxl")
    pytest.importorskip("polars")

    backend_dir = os.path.join(REPO_ROOT, "loans_tool", "backend")
    sys.path.insert(0, backend_dir)
    os.environ.setdefault("FCMR_AADHAAR_HASH_SALT", "test-salt-for-pytest")
    os.environ["LOANS_TRUST_HOST_AUTH"] = "1"
    try:
        import importlib

        import loan_app.main as loan_main
        importlib.reload(loan_main)

        import io
        import json as json_mod

        import openpyxl
        import polars as pl
        from fastapi.testclient import TestClient
        from fcmr_core.catalog import store as catalog_store
        from fcmr_core.config import settings as fcmr_settings

        assert fcmr_settings.max_upload_bytes == 5 * 1024**3

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["loan_id", "DrsPOS"])
        ws.append(["LN0001", 1000])
        xlsx_buf = io.BytesIO()
        wb.save(xlsx_buf)

        parquet_buf = io.BytesIO()
        pl.DataFrame({"loan_id": ["LN0002"], "DrsPOS": [2000]}).write_parquet(parquet_buf)

        with TestClient(loan_main.app) as client:
            resp = client.post(
                "/dashboard/upload",
                data={"report_type": "ead_files"},
                files=[
                    (
                        "files",
                        (
                            "batch_a.xlsx",
                            xlsx_buf.getvalue(),
                            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        ),
                    ),
                    ("files", ("batch_b.parquet", parquet_buf.getvalue(), "application/octet-stream")),
                ],
                follow_redirects=False,
            )
            assert resp.status_code == 303

            uploads = {u["filename"]: u for u in catalog_store.list_uploads()}
            # Converted to CSV filenames, and both landed as real, mappable uploads.
            assert uploads["batch_a.csv"]["status"] == "mapping_pending"
            assert uploads["batch_b.csv"]["status"] == "mapping_pending"
            assert json_mod.loads(uploads["batch_a.csv"]["sniffed_headers"]) == ["loan_id", "DrsPOS"]
    finally:
        os.environ.pop("LOANS_TRUST_HOST_AUTH", None)
        sys.path.remove(backend_dir)
        for mod in list(sys.modules):
            if mod == "loan_app" or mod.startswith("loan_app.") or mod == "app" or mod.startswith("app."):
                del sys.modules[mod]


# ── fcmr_core/schemas/loader.py: best_raw_for_canonical dedup ───────────────
def test_best_raw_for_canonical_prefers_exact_match_over_near_duplicate():
    """
    Regression guard for a real crash: a file with both
    `zero_90_days_interest` (an exact alias match) and
    `zero_90_days_interest_Hist` (a "_Hist" variant that also fuzzy-matches
    the same canonical, score 0.89) caused the naive
    {canonical: raw for raw, (canonical, _) in scored.items()} inversion
    to keep whichever header was seen *last* -- in the real file, that was
    the fuzzy "_Hist" variant, so the exact match was suggested as "Skip"
    while "_Hist" got suggested for that canonical instead. Confirming
    that mapping later crashed ingestion with polars'
    "column ... is duplicate", since the untouched exact-match column was
    still sitting under that exact name.
    """
    backend_dir = os.path.join(REPO_ROOT, "loans_tool", "backend")
    sys.path.insert(0, backend_dir)
    os.environ.setdefault("FCMR_AADHAAR_HASH_SALT", "test-salt-for-pytest")
    try:
        from fcmr_core.catalog import store as catalog_store
        from fcmr_core.schemas.loader import get_schema

        catalog_store.init_catalog()
        schema = get_schema("ead_files")
        raw_headers = ["zero_90_days_interest", "zero_90_days_interest_Hist", "zero_90_int_Final"]

        scored = schema.map_headers_with_scores(raw_headers)
        # Confirm the real-world scoring collision still reproduces as expected.
        assert scored["zero_90_days_interest"][0] == "zero_90_days_interest"
        assert scored["zero_90_days_interest_Hist"][0] == "zero_90_days_interest"

        best = schema.best_raw_for_canonical(raw_headers)
        assert best["zero_90_days_interest"] == "zero_90_days_interest"  # the exact match wins
        assert best["zero_90_int_final"] == "zero_90_int_Final"
    finally:
        sys.path.remove(backend_dir)


def test_resolve_column_renames_never_drops_a_correct_explicit_mapping():
    """
    Regression guard for a real bug caught in code review: an earlier
    version of the DuplicateError fix dropped *any* rename whose target
    already existed as a different raw column -- which silently discarded
    a genuinely correct, deliberate mapping whenever a file happened to
    also carry an unrelated column that coincidentally shared the target's
    exact name. Concretely: a file has a decoy `loan_id` column *and* the
    real agreement number under `AgreementNo`; the user explicitly maps
    AgreementNo -> loan_id (the correct choice). The old guard kept the
    decoy's "JUNK1" as loan_id and threw the real "REAL-AGMT-001" away
    with no warning. resolve_column_renames() must give the explicit
    mapping its requested name and push the decoy aside under a
    disambiguated name instead, so both survive and the correct data
    actually lands on the canonical field.
    """
    backend_dir = os.path.join(REPO_ROOT, "loans_tool", "backend")
    sys.path.insert(0, backend_dir)
    os.environ.setdefault("FCMR_AADHAAR_HASH_SALT", "test-salt-for-pytest")
    try:
        from fcmr_core.schemas.loader import resolve_column_renames

        raw_columns = ["loan_id", "AgreementNo"]
        rename_map = {"AgreementNo": "loan_id"}

        final_names = resolve_column_renames(raw_columns, rename_map)
        assert final_names["AgreementNo"] == "loan_id"  # explicit mapping wins the clean name
        assert final_names["loan_id"] == "loan_id_2"  # decoy survives, disambiguated

        # Two different raw headers both explicitly targeting the same
        # canonical: first-registered wins the clean name, the second
        # still survives under a suffix rather than being lost.
        final_names_2 = resolve_column_renames(
            ["loanid", "account_no"], {"loanid": "loan_id", "account_no": "loan_id"}
        )
        assert final_names_2["loanid"] == "loan_id"
        assert final_names_2["account_no"] == "loan_id_2"
    finally:
        sys.path.remove(backend_dir)


def test_map_columns_form_computes_fuzzy_scores_only_once():
    """
    Regression guard for a real perf issue caught in code review:
    map_columns_form() (the GET /map-columns route) computed
    schema.map_headers_with_scores() once directly to build `suggested`,
    then called schema.best_raw_for_canonical(raw_headers) to build
    `suggested_inverse` -- which internally called
    map_headers_with_scores() *again* on the same raw_headers, doubling
    the fuzzy-match scoring pass (an O(headers x schema_columns)
    difflib.SequenceMatcher loop) and the DB round trip for the
    fuzzy_match_threshold setting, on every single page load. Verifies
    the route reuses its own already-computed scores via
    best_raw_for_canonical_from_scores() instead, by counting real calls
    to map_headers_with_scores.
    """
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")

    backend_dir = os.path.join(REPO_ROOT, "loans_tool", "backend")
    sys.path.insert(0, backend_dir)
    os.environ.setdefault("FCMR_AADHAAR_HASH_SALT", "test-salt-for-pytest")
    os.environ["LOANS_TRUST_HOST_AUTH"] = "1"
    try:
        import importlib

        import loan_app.main as loan_main
        importlib.reload(loan_main)

        from fastapi.testclient import TestClient
        from fcmr_core.catalog import store as catalog_store
        from fcmr_core.schemas.loader import SchemaMap

        # A header combo distinctive enough that no other test in this
        # suite could have already saved a mapping profile for it -- a
        # saved profile short-circuits map_columns_form before it ever
        # calls map_headers_with_scores, which would make this test pass
        # (0 calls) for the wrong reason instead of exercising the fix.
        csv_bytes = b"loan_id,DrsPOS,PerfCountCheckUniqueColumn\nLN0001,1000,1\n"

        with TestClient(loan_main.app) as client:
            resp = client.post(
                "/dashboard/upload",
                data={"report_type": "ead_files"},
                files=[("files", ("perf.csv", csv_bytes, "text/csv"))],
                follow_redirects=False,
            )
            assert resp.status_code == 303
            upload_id = next(
                u["upload_id"] for u in catalog_store.list_uploads() if u["filename"] == "perf.csv"
            )

            call_count = 0
            original = SchemaMap.map_headers_with_scores

            def counting_wrapper(self, raw_headers):
                nonlocal call_count
                call_count += 1
                return original(self, raw_headers)

            SchemaMap.map_headers_with_scores = counting_wrapper
            try:
                resp = client.get(f"/dashboard/uploads/{upload_id}/map-columns")
                assert resp.status_code == 200
            finally:
                SchemaMap.map_headers_with_scores = original

            assert call_count == 1, f"expected 1 fuzzy-scoring pass, got {call_count}"
    finally:
        os.environ.pop("LOANS_TRUST_HOST_AUTH", None)
        sys.path.remove(backend_dir)
        for mod in list(sys.modules):
            if mod == "loan_app" or mod.startswith("loan_app.") or mod == "app" or mod.startswith("app."):
                del sys.modules[mod]


# ── ead_consolidator.py: colliding rename no longer crashes ─────────────────
def test_ead_consolidator_rejects_a_rename_that_would_duplicate_a_column():
    """
    End-to-end regression guard for the reported crash: consolidating a
    file that has both an exact-match column and a near-duplicate
    ("_Hist") column must not raise polars' DuplicateError, whichever
    mapping ends up selected. Covers both directions:
    1. The (now fixed) auto-suggested mapping never picks the wrong one.
    2. Even a mapping hand-picked into the colliding shape doesn't crash
       or silently lose data: resolve_column_renames() gives the explicit
       rename its requested name (it's the one actual intent, however
       mistaken) and pushes the untouched native column's data aside
       under a numeric suffix rather than overwriting or dropping it --
       an earlier version of this fix got this backwards, silently
       keeping the untouched column and discarding the deliberate
       mapping, which is why this direction is asserted explicitly.
    """
    sys.path.insert(0, REPO_ROOT)
    backend_dir = os.path.join(REPO_ROOT, "loans_tool", "backend")
    sys.path.insert(0, backend_dir)
    os.environ.setdefault("FCMR_AADHAAR_HASH_SALT", "test-salt-for-pytest")
    try:
        import polars as pl

        from fcmr_core.catalog import store as catalog_store

        catalog_store.init_catalog()

        import ead_consolidator as ec

        df = pl.DataFrame(
            {
                "loan_id": ["L1"],
                "zero_90_days_interest": [111.0],
                "zero_90_days_interest_Hist": [999.0],
            }
        )

        suggested = ec._suggested_mapping(df.columns)
        user_mapping = {raw: canonical for canonical, raw in suggested.items()}
        assert user_mapping["zero_90_days_interest"] == "zero_90_days_interest"

        consolidated = ec._consolidate([df], ["f.csv"], user_mapping)
        assert consolidated["zero_90_days_interest"].to_list() == [111.0]

        # Direction 2: force the exact collision shape by hand. The
        # explicit rename wins the clean name; the untouched native
        # column's data survives under a disambiguated name instead of
        # being silently discarded.
        explicit_mapping = {"zero_90_days_interest_Hist": "zero_90_days_interest"}
        consolidated_explicit = ec._consolidate([df], ["f.csv"], explicit_mapping)
        assert consolidated_explicit["zero_90_days_interest"].to_list() == [999.0]
        assert consolidated_explicit["zero_90_days_interest_2"].to_list() == [111.0]
    finally:
        sys.path.remove(backend_dir)
        sys.path.remove(REPO_ROOT)
        for mod in list(sys.modules):
            if mod == "ead_consolidator":
                del sys.modules[mod]


# ── fcmr_core/ingestion/pipeline.py: same guard in the real ingest path ─────
def test_ingest_csv_rejects_a_rename_that_would_duplicate_a_column():
    """Same collision, through the real ingest_csv() path the main Loan
    Analytics app uses -- confirms the explicit mapping's data lands under
    the canonical name (not DuckDB's silent "_1"-suffix disambiguation,
    which would put the *native* column there instead and make the
    explicitly-mapped column's data disappear from every canonical-field
    lookup with no error at all), and that the untouched native column's
    data still survives, just under a distinguishable name."""
    backend_dir = os.path.join(REPO_ROOT, "loans_tool", "backend")
    sys.path.insert(0, backend_dir)
    os.environ.setdefault("FCMR_AADHAAR_HASH_SALT", "test-salt-for-pytest")
    try:
        import tempfile
        from pathlib import Path

        import polars as pl

        from fcmr_core.ingestion.pipeline import ingest_csv

        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "f.csv"
            csv_path.write_text(
                "loan_id,zero_90_days_interest,zero_90_days_interest_Hist\nL1,111,999\n",
                encoding="utf-8",
            )

            # An explicit mapping into the colliding shape: renames the
            # _Hist column onto the name the native column already has.
            explicit_mapping = {"zero_90_days_interest_Hist": "zero_90_days_interest"}
            result = ingest_csv(csv_path, "ead_files", user_mapping=explicit_mapping)

            df = pl.read_parquet(result.parquet_path)
            assert df.columns.count("zero_90_days_interest") == 1
            assert df["zero_90_days_interest"].to_list() == [999]
            assert df["zero_90_days_interest_2"].to_list() == [111]
    finally:
        sys.path.remove(backend_dir)


def test_map_columns_page_suggests_exact_match_not_near_duplicate():
    """
    Real functional check of the GET /map-columns screen for a file with
    the colliding-headers shape: the rendered <select> for canonical field
    `zero_90_days_interest` must default to the exact-match raw header
    (`selected` on its own <option>), not the "_Hist" near-duplicate --
    confirming the fix reaches the actual UI a user would confirm, not
    just the underlying schema helper.
    """
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")

    backend_dir = os.path.join(REPO_ROOT, "loans_tool", "backend")
    sys.path.insert(0, backend_dir)
    os.environ.setdefault("FCMR_AADHAAR_HASH_SALT", "test-salt-for-pytest")
    os.environ["LOANS_TRUST_HOST_AUTH"] = "1"
    try:
        import importlib

        import loan_app.main as loan_main
        importlib.reload(loan_main)

        from fastapi.testclient import TestClient
        from fcmr_core.catalog import store as catalog_store

        csv_bytes = b"loan_id,zero_90_days_interest,zero_90_days_interest_Hist\nL1,111,999\n"

        with TestClient(loan_main.app) as client:
            resp = client.post(
                "/dashboard/upload",
                data={"report_type": "ead_files"},
                files=[("files", ("collision.csv", csv_bytes, "text/csv"))],
                follow_redirects=False,
            )
            assert resp.status_code == 303

            upload_id = next(
                u["upload_id"] for u in catalog_store.list_uploads() if u["filename"] == "collision.csv"
            )
            resp = client.get(f"/dashboard/uploads/{upload_id}/map-columns")
            assert resp.status_code == 200
            # The exact-match option for the zero_90_days_interest row is
            # the one marked selected -- not the "_Hist" variant.
            row_start = resp.text.index('data-canonical="zero_90_days_interest"')
            row_html = resp.text[row_start : row_start + 800]
            assert '<option value="zero_90_days_interest" data-header="zero_90_days_interest" selected>' in row_html
    finally:
        os.environ.pop("LOANS_TRUST_HOST_AUTH", None)
        sys.path.remove(backend_dir)
        for mod in list(sys.modules):
            if mod == "loan_app" or mod.startswith("loan_app.") or mod == "app" or mod.startswith("app."):
                del sys.modules[mod]


def test_build_consolidated_df_reuses_one_connection_not_one_per_file():
    """
    Regression guard for a real report: EAD Consolidation's "Download
    Parquet" button showed "Generating Parquet…" for a long time on a
    batch of a dozen-plus large files, then flipped back to "ready"
    (the button's own 15s safety-net timeout firing) well before the
    actual download had happened -- looking like a silent failure. Root
    cause: build_consolidated_df() called get_upload_df() once per ready
    upload, each opening its own fresh duckdb.connect() (real per-call
    overhead, including apply_duckdb_limits' several SET statements),
    instead of sharing one connection across the whole batch. Verifies by
    counting real store.open_connection() calls while consolidating 3
    ready uploads -- should be exactly 1, not 3 -- and that the
    consolidated result is still correct.
    """
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")

    backend_dir = os.path.join(REPO_ROOT, "loans_tool", "backend")
    sys.path.insert(0, backend_dir)
    os.environ.setdefault("FCMR_AADHAAR_HASH_SALT", "test-salt-for-pytest")
    os.environ["LOANS_TRUST_HOST_AUTH"] = "1"
    try:
        import importlib

        import loan_app.main as loan_main
        importlib.reload(loan_main)

        from fastapi.testclient import TestClient
        from fcmr_core.catalog import store as catalog_store

        with TestClient(loan_main.app) as client:
            # A dedicated engagement, not the shared "default" every other
            # test also uploads into -- build_consolidated_df pulls in
            # *every* ready ead_files upload for the engagement it's given,
            # so reusing "default" would count other tests' leftover
            # uploads too. Created after entering the TestClient context so
            # the app's startup has already run init_catalog().
            engagement_id = catalog_store.create_engagement("conn-reuse-test")
            client.post(f"/set-active/{engagement_id}")

            for i in range(3):
                csv_bytes = f"loan_id,DrsPOS\nLN000{i},{1000 * (i + 1)}\n".encode()
                resp = client.post(
                    "/dashboard/upload",
                    data={"report_type": "ead_files"},
                    files=[("files", (f"conn_reuse_{i}.csv", csv_bytes, "text/csv"))],
                    follow_redirects=False,
                )
                assert resp.status_code == 303
                upload_id = next(
                    u["upload_id"]
                    for u in catalog_store.list_uploads()
                    if u["filename"] == f"conn_reuse_{i}.csv"
                )
                resp = client.post(
                    f"/dashboard/uploads/{upload_id}/map-columns",
                    data={"map_loan_id": "loan_id", "map_outstanding_principal": "DrsPOS"},
                    follow_redirects=False,
                )
                assert resp.status_code == 303

            call_count = 0
            real_open_connection = catalog_store.open_connection

            def counting_open_connection():
                nonlocal call_count
                call_count += 1
                return real_open_connection()

            catalog_store.open_connection = counting_open_connection
            try:
                df = catalog_store.build_consolidated_df(engagement_id, "ead_files")
            finally:
                catalog_store.open_connection = real_open_connection

            assert call_count == 1, f"expected 1 shared connection, got {call_count}"
            assert sorted(df["loan_id"].to_list()) == ["LN0000", "LN0001", "LN0002"]
    finally:
        os.environ.pop("LOANS_TRUST_HOST_AUTH", None)
        sys.path.remove(backend_dir)
        for mod in list(sys.modules):
            if mod == "loan_app" or mod.startswith("loan_app.") or mod == "app" or mod.startswith("app."):
                del sys.modules[mod]


# ── fcmr_core/reporting: two bugs found while building EAD Analytics ───────
def test_aggregate_status_counts_reads_real_counts_not_always_zero():
    """Regression guard for a real bug found while building EAD Analytics'
    result page: aggregate_status_counts() read polars'
    `value_counts().to_dicts()` rows via row["counts"] (plural), but that
    method actually names the column "count" (singular) in the installed
    polars version -- the resulting KeyError was silently swallowed by a
    bare `except Exception`, so this function ALWAYS returned
    {"OK": 0, "WARN": 0, "ERROR": 0} regardless of the real data. This
    affected every analytics run's stat cards and % columns, Customer
    Master's included, not just the new EAD screen -- aggregate_
    exception_codes (a sibling function reading a different column) was
    unaffected and kept working, which is what made the bug easy to miss.
    """
    backend_dir = os.path.join(REPO_ROOT, "loans_tool", "backend")
    sys.path.insert(0, backend_dir)
    try:
        import tempfile
        from pathlib import Path

        from fcmr_core.reporting.aggregation import aggregate_status_counts

        with tempfile.TemporaryDirectory() as tmp:
            wide_path = Path(tmp) / "wide.csv"
            wide_path.write_text("overall_status\nWARN\nWARN\nOK\nERROR\n")
            counts = aggregate_status_counts(wide_path)
            assert counts == {"OK": 1, "WARN": 2, "ERROR": 1}
    finally:
        sys.path.remove(backend_dir)


def test_bar_chart_long_exception_codes_are_not_clipped_off_the_edge():
    """Regression guard for a real bug found while building EAD Analytics:
    build_bar_chart() right-aligns each label at a FIXED 200px left margin
    with no font-family set (so the SVG falls back to the browser's default
    serif font). Customer Master's exception codes are all short enough to
    fit, but EAD's longer, more descriptive codes (e.g.
    "MATURITY_BEFORE_DISBURSAL_OR_SANCTION", 38 chars) overflowed past
    x=0 and were clipped clean off the left edge of the SVG viewBox --
    silently invisible, not just visually truncated. The margin now sizes
    itself to the longest label being rendered."""
    backend_dir = os.path.join(REPO_ROOT, "loans_tool", "backend")
    sys.path.insert(0, backend_dir)
    try:
        from fcmr_core.reporting.charts import build_bar_chart

        long_code = "MATURITY_BEFORE_DISBURSAL_OR_SANCTION"
        svg = build_bar_chart({long_code: 5, "NEGATIVE_EAD": 1}, width=700, height=400)
        assert long_code in svg, "long label was truncated/clipped instead of sized for"
        assert 'font-family' in svg  # pins a real font so the width estimate holds
    finally:
        sys.path.remove(backend_dir)


# ── EAD Analytics: the new "Analytics" screen (EAD-only rules, v1) ─────────
def test_ead_row_rules_flag_expected_exceptions():
    """Unit-level guard for each of the 11 EAD-only row rules (1, 2, 4, 5, 6,
    8, 11, 12, 13, 14, 15): constructs one small DataFrame with a
    deliberately-triggering case for every rule and checks each fires with
    the right exception code -- and that a clean row stays OK."""
    pytest.importorskip("polars")
    backend_dir = os.path.join(REPO_ROOT, "loans_tool", "backend")
    sys.path.insert(0, backend_dir)
    try:
        import polars as pl

        from fcmr_core.rules.ead_rules import EAD_ROW_RULES, run_ead_row_rules

        assert len(EAD_ROW_RULES) == 11

        df = pl.DataFrame(
            {
                "loan_id": ["CLEAN", "L1", "L2", "L3", "L4", "L5"],
                "disbursement_date": ["01-01-2023", "01-04-2023", "01-05-2023", "10-02-2023", "01-06-2023", "01-01-2020"],
                "sanction_date": ["01-01-2023", "01-01-2023", "01-01-2023", "01-01-2023", "01-01-2023", "01-01-2019"],
                "sanction_amount": [100.0, 100.0, 100.0, 100.0, 50.0, 100.0],
                "disbursed_amount": [90.0, 90.0, 90.0, 90.0, 60.0, 90.0],
                "future_pos": [10.0, 95.0, 10.0, 10.0, 10.0, 10.0],
                "ead": [100.0, 100.0, -5.0, 100.0, 100.0, 100.0],
                "npa_flag_date": [None, None, "01-01-2024", None, None, "01-06-2020"],
                "business_date": ["31-03-2024"] * 6,
                "maturity_date": ["01-01-2030", "01-04-2028", "01-05-2025", "01-01-2019", "01-06-2028", "01-01-2021"],
                "original_tenure": [84, 60, 24, 60, -5, 12],
                "product_helper": ["TW"] * 6,
                "ucid": ["U0", "U1", "U1", "U2", "U3", "U4"],
                "customer_id": ["C0", "C1", "C1", "C2", "C3", "C4"],
            }
        )
        annotated = run_ead_row_rules(df)
        codes = {row["loan_id"]: row for row in annotated.to_dicts()}

        assert codes["CLEAN"]["_exc_quick_mortality_code"] == ""
        assert codes["CLEAN"]["_exc_sanction_disbursal_delay_code"] == ""
        assert codes["CLEAN"]["_exc_disbursal_before_sanction_code"] == ""
        assert codes["CLEAN"]["_exc_outstanding_exceeds_disbursed_code"] == ""
        assert codes["CLEAN"]["_exc_disbursed_exceeds_sanctioned_code"] == ""
        assert codes["CLEAN"]["_exc_negative_ead_code"] == ""
        assert codes["CLEAN"]["_exc_tenure_mismatch_code"] == ""
        assert codes["CLEAN"]["_exc_maturity_before_disbursal_or_sanction_code"] == ""
        assert codes["CLEAN"]["_exc_matured_still_on_book_code"] == ""
        assert codes["CLEAN"]["_exc_invalid_tenure_code"] == ""
        assert codes["CLEAN"]["_exc_new_disbursal_to_npa_customer_code"] == ""

        assert codes["L1"]["_exc_sanction_disbursal_delay_code"] == "SANCTION_DISBURSAL_DELAY"
        assert codes["L1"]["_exc_outstanding_exceeds_disbursed_code"] == "OUTSTANDING_EXCEEDS_DISBURSED"
        assert codes["L2"]["_exc_quick_mortality_code"] == "QUICK_MORTALITY"
        assert codes["L2"]["_exc_negative_ead_code"] == "NEGATIVE_EAD"
        assert codes["L3"]["_exc_maturity_before_disbursal_or_sanction_code"] == "MATURITY_BEFORE_DISBURSAL_OR_SANCTION"
        assert codes["L3"]["_exc_matured_still_on_book_code"] == "MATURED_STILL_ON_BOOK"
        assert codes["L4"]["_exc_invalid_tenure_code"] == "INVALID_TENURE"
        assert codes["L5"]["_exc_quick_mortality_code"] == "QUICK_MORTALITY"

        # Disbursal-before-sanction and disbursed-exceeds-sanctioned, tested
        # separately since the shared frame above doesn't trigger them.
        df2 = pl.DataFrame(
            {
                "loan_id": ["D1", "D2", "T1"],
                "disbursement_date": ["01-01-2023", "01-04-2023", "01-01-2020"],
                "sanction_date": ["15-01-2023", "01-01-2023", "01-01-2019"],
                "sanction_amount": [100.0, 50.0, 100.0],
                "disbursed_amount": [90.0, 90.0, 90.0],
                "future_pos": [10.0, 10.0, 10.0],
                "ead": [100.0, 100.0, 100.0],
                "npa_flag_date": [None, None, None],
                "business_date": ["31-03-2024"] * 3,
                "maturity_date": ["01-01-2030"] * 3,
                # T1: disbursed 01-01-2020, maturity 01-01-2030 = exactly 120
                # months, but Original Tenure says 60 -> tenure mismatch.
                "original_tenure": [84, 60, 60],
                "product_helper": ["TW"] * 3,
                "ucid": ["U5", "U6", "U7"],
                "customer_id": ["C5", "C6", "C7"],
            }
        )
        annotated2 = run_ead_row_rules(df2)
        codes2 = {row["loan_id"]: row for row in annotated2.to_dicts()}
        assert codes2["D1"]["_exc_disbursal_before_sanction_code"] == "DISBURSAL_BEFORE_SANCTION"
        assert codes2["D2"]["_exc_disbursed_exceeds_sanctioned_code"] == "DISBURSED_EXCEEDS_SANCTIONED"
        assert codes2["T1"]["_exc_tenure_mismatch_code"] == "TENURE_MISMATCH"

        # Rule 15: a later loan for the same customer, disbursed after
        # another of that customer's loans went NPA.
        df3 = pl.DataFrame(
            {
                "loan_id": ["OLD", "NEW"],
                "disbursement_date": ["01-01-2020", "01-06-2024"],
                "sanction_date": ["01-01-2019", "01-01-2024"],
                "sanction_amount": [100.0, 100.0],
                "disbursed_amount": [90.0, 90.0],
                "future_pos": [10.0, 10.0],
                "ead": [100.0, 100.0],
                "npa_flag_date": ["01-01-2022", None],
                "business_date": ["31-03-2024"] * 2,
                "maturity_date": ["01-01-2030"] * 2,
                "original_tenure": [120, 120],
                "product_helper": ["TW"] * 2,
                "ucid": ["SAME", "SAME"],
                "customer_id": ["SAMEC", "SAMEC"],
            }
        )
        annotated3 = run_ead_row_rules(df3)
        codes3 = {row["loan_id"]: row for row in annotated3.to_dicts()}
        assert codes3["NEW"]["_exc_new_disbursal_to_npa_customer_code"] == "NEW_DISBURSAL_TO_NPA_CUSTOMER"
        assert codes3["OLD"]["_exc_new_disbursal_to_npa_customer_code"] == ""
    finally:
        sys.path.remove(backend_dir)


def test_ead_rules_handle_already_parsed_date_columns_not_just_raw_strings():
    """Regression guard for a real bug found while wiring this feature up:
    DuckDB-backed ingestion (fcmr_core.catalog.store.get_upload_df /
    build_consolidated_df) auto-parses a recognized "DD-MM-YYYY" column
    into a genuine pl.Date column at ingest time -- it does NOT stay a raw
    string. The rules' date-parsing helper originally always cast to Utf8
    first and re-parsed as "%d-%m-%Y", which for an already-Date column
    reformats it to ISO text ("2023-04-01") and then fails to re-parse
    against the DD-MM-YYYY pattern, silently nulling out every date and
    making every date-based rule a no-op. Confirmed against the real app
    via TestClient before this fix: none of quick_mortality,
    sanction_disbursal_delay, tenure_mismatch, maturity_before_disbursal_
    or_sanction, or matured_still_on_book ever fired. This test builds the
    DataFrame with genuine pl.Date columns (as ingestion actually produces)
    rather than strings, so it fails the way the real bug did if the
    date-parsing helper regresses back to assuming raw text.
    """
    pytest.importorskip("polars")
    backend_dir = os.path.join(REPO_ROOT, "loans_tool", "backend")
    sys.path.insert(0, backend_dir)
    try:
        import datetime

        import polars as pl

        from fcmr_core.rules.ead_rules import run_ead_row_rules

        df = pl.DataFrame(
            {
                "loan_id": ["L1"],
                "disbursement_date": [datetime.date(2023, 5, 1)],
                "sanction_date": [datetime.date(2023, 1, 1)],
                "npa_flag_date": [datetime.date(2024, 1, 1)],
                "business_date": [datetime.date(2024, 3, 31)],
                "maturity_date": [datetime.date(2019, 1, 1)],
                "original_tenure": [60],
                "sanction_amount": [100.0],
                "disbursed_amount": [90.0],
                "future_pos": [10.0],
                "ead": [100.0],
                "product_helper": ["TW"],
                "ucid": ["U1"],
                "customer_id": ["C1"],
            },
            schema_overrides={
                "disbursement_date": pl.Date,
                "sanction_date": pl.Date,
                "npa_flag_date": pl.Date,
                "business_date": pl.Date,
                "maturity_date": pl.Date,
            },
        )
        annotated = run_ead_row_rules(df)
        row = annotated.to_dicts()[0]
        # 2024-01-01 - 2023-05-01 = 245 days < 365.
        assert row["_exc_quick_mortality_code"] == "QUICK_MORTALITY"
        # 2023-05-01 - 2023-01-01 = 120 days > 30 (default threshold).
        assert row["_exc_sanction_disbursal_delay_code"] == "SANCTION_DISBURSAL_DELAY"
        # Maturity (2019) is before both disbursal and sanction (2023).
        assert row["_exc_maturity_before_disbursal_or_sanction_code"] == "MATURITY_BEFORE_DISBURSAL_OR_SANCTION"
        # Matured in 2019, business date 2024-03-31, still 10.0 outstanding.
        assert row["_exc_matured_still_on_book_code"] == "MATURED_STILL_ON_BOOK"
    finally:
        sys.path.remove(backend_dir)


def test_ead_reports_compute_expected_summaries():
    """Unit-level guard for the 4 EAD summary reports (7, 9, 10, 18)."""
    pytest.importorskip("polars")
    backend_dir = os.path.join(REPO_ROOT, "loans_tool", "backend")
    sys.path.insert(0, backend_dir)
    try:
        import polars as pl

        from fcmr_core.rules.ead_reports import (
            month_wise_disbursal_summary,
            npa_flag_date_change_report,
            product_ead_reconciliation_summary,
            system_product_minmax_summary,
        )

        df = pl.DataFrame(
            {
                "loan_id": ["L1", "L2"],
                "product_helper": ["TW", "TW"],
                "stage": ["Stage 1", "Stage 3"],
                "ead": [100.0, 50.0],
                "gross_book_value": [10.0, 5.0],
                "zero_90_days_interest": [1.0, 0.5],
                "zero_90_int_final": [0.2, 0.3],
                "system": ["SysA", "SysA"],
                "scheme_name": ["ProdA", "ProdA"],
                "sanction_amount": [100.0, 200.0],
                "financial_irr": [12.0, 14.0],
                "original_tenure": [24, 36],
                "disbursement_date": ["01-04-2023", "01-05-2023"],
                "disbursed_amount": [90.0, 190.0],
                "state": ["MH", "GJ"],
            }
        )

        recon = product_ead_reconciliation_summary(df)
        row = recon.filter(pl.col("product_helper") == "TW").to_dicts()[0]
        assert row["lans"] == 2
        assert row["ead_total"] == 150.0
        assert row["stage1_ead"] == 100.0
        assert row["stage3_ead"] == 50.0
        assert row["check1_diff"] == 0.0  # Stage1(100) + Stage3(50) == EAD(150)

        minmax = system_product_minmax_summary(df).to_dicts()[0]
        assert minmax["min_sanction_amount"] == 100.0
        assert minmax["max_sanction_amount"] == 200.0
        assert minmax["min_original_tenure"] == 24
        assert minmax["max_original_tenure"] == 36

        month_wise = month_wise_disbursal_summary(df, 2023, include_state=False)
        row = month_wise.to_dicts()[0]
        assert row["Apr_amount"] == 90.0
        assert row["Apr_count"] == 1
        assert row["May_amount"] == 190.0

        # Rule 18 needs multiple source files -- single-file input has none.
        single_file_df = df.with_columns(pl.lit("only.csv").alias("_source_file"))
        assert npa_flag_date_change_report(single_file_df).height == 0

        multi_file_df = pl.DataFrame(
            {
                "loan_id": ["L1", "L1"],
                "business_date": ["31-01-2024", "29-02-2024"],
                "npa_flag_date": ["15-01-2024", "20-02-2024"],
                "_source_file": ["jan.csv", "feb.csv"],
            }
        )
        changes = npa_flag_date_change_report(multi_file_df).to_dicts()
        assert len(changes) == 1
        assert changes[0]["from_file"] == "jan.csv"
        assert changes[0]["to_file"] == "feb.csv"
    finally:
        sys.path.remove(backend_dir)


def test_ead_analytics_screen_runs_real_rules_and_reports_end_to_end():
    """Functional test of the new EAD Analytics screen through the real app:
    upload + map a real EAD file (going through DuckDB-backed ingestion, so
    this also guards the date-dtype bug above at the route level), run the
    row-rule checks, download the results, and run every summary report.
    """
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    pytest.importorskip("polars")

    backend_dir = os.path.join(REPO_ROOT, "loans_tool", "backend")
    sys.path.insert(0, backend_dir)
    os.environ.setdefault("FCMR_AADHAAR_HASH_SALT", "test-salt-for-pytest")
    os.environ["LOANS_TRUST_HOST_AUTH"] = "1"
    try:
        import importlib
        import re

        import loan_app.main as loan_main

        importlib.reload(loan_main)

        from fastapi.testclient import TestClient
        from fcmr_core.catalog import store as catalog_store

        csv_bytes = (
            b"loan_id,disbursement_date,sanction_date,sanction_amount,disbursed_amount,future_pos,ead,"
            b"npa_flag_date,business_date,maturity_date,original_tenure,system,scheme_name,financial_irr,"
            b"stage,gross_book_value,zero_90_days_interest,zero_90_int_final,state\n"
            b"L1,01-04-2023,01-01-2023,100.0,90.0,95.0,100.0,,31-03-2024,01-04-2028,60,"
            b"OneLMS_TW,ProdA,12.0,Stage 1,10.0,1.0,0.5,MH\n"
            b"L2,01-05-2023,01-01-2023,100.0,90.0,10.0,-5.0,01-01-2024,31-03-2024,01-05-2025,24,"
            b"SCF,ProdB,13.0,Stage 2,20.0,2.0,0.5,GJ\n"
        )
        fields = [
            "loan_id", "disbursement_date", "sanction_date", "sanction_amount", "disbursed_amount",
            "future_pos", "ead", "npa_flag_date", "business_date", "maturity_date", "original_tenure",
            "system", "scheme_name", "financial_irr", "stage", "gross_book_value",
            "zero_90_days_interest", "zero_90_int_final", "state",
        ]

        with TestClient(loan_main.app) as client:
            resp = client.post(
                "/dashboard/upload",
                data={"report_type": "ead_files"},
                files=[("files", ("ead_analytics.csv", csv_bytes, "text/csv"))],
                follow_redirects=False,
            )
            assert resp.status_code == 303
            upload_id = next(
                u["upload_id"] for u in catalog_store.list_uploads() if u["filename"] == "ead_analytics.csv"
            )
            resp = client.post(
                f"/dashboard/uploads/{upload_id}/map-columns",
                data={f"map_{f}": f for f in fields},
                follow_redirects=False,
            )
            assert resp.status_code == 303

            # Selection screen reflects the now-ready upload.
            resp = client.get("/dashboard/analytics/ead")
            assert resp.status_code == 200
            assert "Exception Checks" in resp.text
            assert "Sanction-to-Disbursal Delay" in resp.text

            # Run every row rule, with a per-Product-Helper threshold override.
            from fcmr_core.rules.ead_rules import EAD_ROW_RULES as _ALL_EAD_ROW_RULES

            resp = client.post(
                "/dashboard/analytics/ead/run",
                data={
                    "rules": [m.rule_id for m in _ALL_EAD_ROW_RULES],
                    "default_days": "30",
                    "type_names": ["TW"],
                    "type_days": ["10"],
                },
            )
            assert resp.status_code == 200
            assert "Download Wide CSV" in resp.text

            # The per-Type override was persisted for next time.
            overrides, default_days = catalog_store.get_ead_sanction_disbursal_thresholds()
            assert overrides["TW"] == 10
            assert default_days == 30

            run_id = re.search(r"/dashboard/analytics/ead/run/([\w-]+)/download/wide", resp.text).group(1)
            wide = client.get(f"/dashboard/analytics/ead/run/{run_id}/download/wide")
            assert wide.status_code == 200
            assert "QUICK_MORTALITY" in wide.text  # L2 went NPA 245 days after disbursal (< 365)
            assert "NEGATIVE_EAD" in wide.text  # L2's EAD is -5.0
            assert "OUTSTANDING_EXCEEDS_DISBURSED" in wide.text  # L1's future_pos (95) > disbursed (90)

            long_csv = client.get(f"/dashboard/analytics/ead/run/{run_id}/download/long")
            assert long_csv.status_code == 200

            # Revisiting the same run_id later re-renders without recomputing.
            resp = client.get(f"/dashboard/analytics/ead/run/{run_id}")
            assert resp.status_code == 200

            # Summary reports, each via its real route + CSV download.
            resp = client.post("/dashboard/analytics/ead/summary/product-recon", data={})
            assert resp.status_code == 200 and "Product-wise EAD Reconciliation" in resp.text
            dl = client.get("/dashboard/analytics/ead/summary/product-recon/download")
            assert dl.status_code == 200 and "check1_diff" in dl.text

            resp = client.post("/dashboard/analytics/ead/summary/system-product-minmax", data={})
            assert resp.status_code == 200
            dl = client.get("/dashboard/analytics/ead/summary/system-product-minmax/download")
            assert "min_sanction_amount" in dl.text

            resp = client.post(
                "/dashboard/analytics/ead/summary/month-wise-disbursal",
                data={"fy_start_year": "2023", "include_state": "true"},
            )
            assert resp.status_code == 200
            dl = client.get(
                "/dashboard/analytics/ead/summary/month-wise-disbursal/download",
                params={"fy_start_year": 2023, "include_state": True},
            )
            assert "Apr_amount" in dl.text

            resp = client.post("/dashboard/analytics/ead/summary/npa-flag-changes", data={})
            assert resp.status_code == 200
    finally:
        os.environ.pop("LOANS_TRUST_HOST_AUTH", None)
        sys.path.remove(backend_dir)
        for mod in list(sys.modules):
            if mod == "loan_app" or mod.startswith("loan_app.") or mod == "app" or mod.startswith("app."):
                del sys.modules[mod]


def test_ead_analytics_detects_npa_flag_change_across_two_separate_uploads():
    """Functional test of rule 18 via the real app: two separately uploaded
    EAD files for the same loan, with a Business Date each and a changed
    (non-null -> different non-null) NPA Flag Date between them."""
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")

    backend_dir = os.path.join(REPO_ROOT, "loans_tool", "backend")
    sys.path.insert(0, backend_dir)
    os.environ.setdefault("FCMR_AADHAAR_HASH_SALT", "test-salt-for-pytest")
    os.environ["LOANS_TRUST_HOST_AUTH"] = "1"
    try:
        import importlib

        import loan_app.main as loan_main

        importlib.reload(loan_main)

        from fastapi.testclient import TestClient
        from fcmr_core.catalog import store as catalog_store

        jan_csv = b"loan_id,business_date,npa_flag_date\nL1,31-01-2024,15-01-2024\n"
        feb_csv = b"loan_id,business_date,npa_flag_date\nL1,29-02-2024,20-02-2024\n"

        with TestClient(loan_main.app) as client:
            for name, data in [("jan.csv", jan_csv), ("feb.csv", feb_csv)]:
                client.post(
                    "/dashboard/upload",
                    data={"report_type": "ead_files"},
                    files=[("files", (name, data, "text/csv"))],
                    follow_redirects=False,
                )
                upload_id = next(u["upload_id"] for u in catalog_store.list_uploads() if u["filename"] == name)
                resp = client.post(
                    f"/dashboard/uploads/{upload_id}/map-columns",
                    data={
                        "map_loan_id": "loan_id",
                        "map_business_date": "business_date",
                        "map_npa_flag_date": "npa_flag_date",
                    },
                    follow_redirects=False,
                )
                assert resp.status_code == 303

            dl = client.get("/dashboard/analytics/ead/summary/npa-flag-changes/download")
            assert dl.status_code == 200
            lines = dl.text.strip().splitlines()
            assert len(lines) == 2  # header + one changed row
            assert "jan.csv" in lines[1] and "feb.csv" in lines[1]
            assert "2024-01-15" in lines[1] and "2024-02-20" in lines[1]
    finally:
        os.environ.pop("LOANS_TRUST_HOST_AUTH", None)
        sys.path.remove(backend_dir)
        for mod in list(sys.modules):
            if mod == "loan_app" or mod.startswith("loan_app.") or mod == "app" or mod.startswith("app."):
                del sys.modules[mod]
