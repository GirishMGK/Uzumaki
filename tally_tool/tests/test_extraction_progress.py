"""progress_callback support in extract()/extract_xml()/extract_any() --
real feedback: extraction of a large Tally export had no sense of progress,
just an indeterminate spinner, for something that can genuinely take a
while. Verifies the callback actually fires with real, monotonically
increasing byte positions and ends at 100% for both the JSON and XML
export formats.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from extract_ledgers import extract, extract_any, extract_xml


def _write_temp(content: str, suffix: str) -> str:
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=suffix, delete=False, encoding="utf-8"
    ) as f:
        f.write(content)
        return f.name


def _json_export(n_vouchers: int) -> dict:
    return {
        "tallymessage": [
            {"metadata": {"type": "Ledger", "name": "Cash"},
             "parent": "Cash-in-Hand", "openingbalance": "0"},
            {"metadata": {"type": "Ledger", "name": "Sales Account"},
             "parent": "Sales Accounts", "openingbalance": "0"},
        ] + [
            {
                "metadata": {"type": "Voucher"}, "date": "20240110",
                "vouchertypename": "Sales", "vouchernumber": f"S/{i}",
                "partyledgername": "Cash", "reference": "", "narration": "",
                "guid": f"g{i}", "masterid": str(i),
                "iscancelled": False, "isoptional": False,
                "allledgerentries": [
                    {"ledgername": "Cash", "amount": -1000},
                    {"ledgername": "Sales Account", "amount": 1000},
                ],
            }
            for i in range(n_vouchers)
        ]
    }


def _xml_export(n_vouchers: int) -> str:
    vouchers = "".join(
        f"""
<VOUCHER VCHTYPE="Sales">
<DATE>20240110</DATE>
<VOUCHERTYPENAME>Sales</VOUCHERTYPENAME>
<VOUCHERNUMBER>S/{i}</VOUCHERNUMBER>
<PARTYLEDGERNAME>Cash</PARTYLEDGERNAME>
<GUID>g{i}</GUID>
<MASTERID>{i}</MASTERID>
<ALLLEDGERENTRIES.LIST>
<LEDGERNAME>Cash</LEDGERNAME>
<AMOUNT>-1000</AMOUNT>
</ALLLEDGERENTRIES.LIST>
<ALLLEDGERENTRIES.LIST>
<LEDGERNAME>Sales Account</LEDGERNAME>
<AMOUNT>1000</AMOUNT>
</ALLLEDGERENTRIES.LIST>
</VOUCHER>
""" for i in range(n_vouchers)
    )
    return (
        '<ENVELOPE><BODY><IMPORTDATA><REQUESTDATA>'
        '<LEDGER NAME="Cash"><PARENT>Cash-in-Hand</PARENT><OPENINGBALANCE>0</OPENINGBALANCE></LEDGER>'
        '<LEDGER NAME="Sales Account"><PARENT>Sales Accounts</PARENT><OPENINGBALANCE>0</OPENINGBALANCE></LEDGER>'
        f'{vouchers}'
        '</REQUESTDATA></IMPORTDATA></BODY></ENVELOPE>'
    )


def _assert_well_formed_progress(calls: list[tuple[int, int]]) -> None:
    assert calls, "progress_callback was never called"
    for i in range(1, len(calls)):
        assert calls[i][0] >= calls[i - 1][0], "bytes_read must never go backwards"
    for bytes_read, total_bytes in calls:
        assert 0 <= bytes_read <= total_bytes
    assert calls[-1][0] == calls[-1][1], "the final call must report 100% (bytes_read == total_bytes)"


def test_extract_json_progress_callback():
    path = _write_temp(json.dumps(_json_export(500)), ".json")
    try:
        calls = []
        ledger_master, rows = extract(path, progress_callback=lambda r, t: calls.append((r, t)))
    finally:
        os.unlink(path)

    assert len(rows) == 1000  # 500 vouchers x 2 ledger entries each
    _assert_well_formed_progress(calls)


def test_extract_xml_progress_callback():
    path = _write_temp(_xml_export(500), ".xml")
    try:
        calls = []
        ledger_master, rows = extract_xml(path, progress_callback=lambda r, t: calls.append((r, t)))
    finally:
        os.unlink(path)

    assert len(ledger_master) == 2
    assert len(rows) == 1000
    _assert_well_formed_progress(calls)


def test_extract_any_dispatches_progress_callback_for_both_formats():
    """extract_any() must actually pass the callback through to whichever
    of extract()/extract_xml() it dispatches to, not silently drop it."""
    json_path = _write_temp(json.dumps(_json_export(10)), ".json")
    xml_path = _write_temp(_xml_export(10), ".xml")
    try:
        json_calls = []
        extract_any(json_path, progress_callback=lambda r, t: json_calls.append((r, t)))
        xml_calls = []
        extract_any(xml_path, progress_callback=lambda r, t: xml_calls.append((r, t)))
    finally:
        os.unlink(json_path)
        os.unlink(xml_path)

    assert json_calls, "extract_any() did not forward progress_callback for a JSON export"
    assert xml_calls, "extract_any() did not forward progress_callback for an XML export"


def test_extract_works_without_a_progress_callback():
    """progress_callback must stay optional -- the CLI entry point and
    anything else calling extract()/extract_any() directly doesn't pass one."""
    path = _write_temp(json.dumps(_json_export(5)), ".json")
    try:
        ledger_master, rows = extract(path)
    finally:
        os.unlink(path)
    assert len(rows) == 10
