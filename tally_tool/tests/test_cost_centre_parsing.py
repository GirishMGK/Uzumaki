"""Cost Centre column: file-based (JSON + XML) and live-XML paths.

No live Tally instance is available in this environment, so the live path
is tested by monkeypatching requests.post to return a fixture response
matching the documented COSTCENTREALLOCATIONS.LIST shape -- same technique
any requests-based code is unit-tested with.
"""
from __future__ import annotations

import datetime
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import extract_ledgers
import tally_connector


def _sample_json_export() -> dict:
    return {
        "tallymessage": [
            {
                "metadata": {"type": "Ledger", "name": "Salary Expense"},
                "parent": "Indirect Expenses",
                "openingbalance": 0,
            },
            {
                "metadata": {"type": "Voucher"},
                "date": "20240115",
                "vouchertypename": "Payment",
                "vouchernumber": "PAY/1",
                "partyledgername": "",
                "narration": "Salary paid",
                "guid": "guid-1",
                "masterid": "1",
                "allledgerentries": [
                    {
                        "ledgername": "Salary Expense",
                        "amount": -10000,
                        "costcentreallocations": [{"name": "Sales Dept", "amount": -6000}, {"name": "Admin Dept", "amount": -4000}],
                    },
                    {"ledgername": "Cash", "amount": 10000},
                ],
            },
        ]
    }


def test_json_export_captures_cost_centre(tmp_path):
    path = tmp_path / "export.json"
    path.write_text(json.dumps(_sample_json_export()), encoding="utf-8")

    ledger_master, rows = extract_ledgers.extract(str(path))

    salary_row = next(r for r in rows if r["Ledger Name"] == "Salary Expense")
    assert salary_row["Cost Centre"] == "Sales Dept; Admin Dept"

    cash_row = next(r for r in rows if r["Ledger Name"] == "Cash")
    assert cash_row["Cost Centre"] == "", "a ledger entry with no cost centre allocations should be blank, not crash"


def test_json_export_blank_cost_centre_when_not_enabled(tmp_path):
    """Graceful degrade: a company that doesn't use cost centres at all --
    the field is simply absent from every entry, same as Bill Reference."""
    export = _sample_json_export()
    for entry in export["tallymessage"][1]["allledgerentries"]:
        entry.pop("costcentreallocations", None)
    path = tmp_path / "export.json"
    path.write_text(json.dumps(export), encoding="utf-8")

    _ledger_master, rows = extract_ledgers.extract(str(path))
    assert all(r["Cost Centre"] == "" for r in rows)


_XML_EXPORT = """<ENVELOPE>
<TALLYMESSAGE>
<LEDGER NAME="Salary Expense"><PARENT>Indirect Expenses</PARENT><OPENINGBALANCE>0</OPENINGBALANCE></LEDGER>
</TALLYMESSAGE>
<TALLYMESSAGE>
<VOUCHER VCHTYPE="Payment">
<DATE>20240115</DATE>
<VOUCHERTYPENAME>Payment</VOUCHERTYPENAME>
<VOUCHERNUMBER>PAY/1</VOUCHERNUMBER>
<PARTYLEDGERNAME></PARTYLEDGERNAME>
<NARRATION>Salary paid</NARRATION>
<GUID>guid-1</GUID>
<MASTERID>1</MASTERID>
<ALLLEDGERENTRIES.LIST>
<LEDGERNAME>Salary Expense</LEDGERNAME>
<AMOUNT>-10000</AMOUNT>
<COSTCENTREALLOCATIONS.LIST><NAME>Sales Dept</NAME><AMOUNT>-6000</AMOUNT></COSTCENTREALLOCATIONS.LIST>
<COSTCENTREALLOCATIONS.LIST><NAME>Admin Dept</NAME><AMOUNT>-4000</AMOUNT></COSTCENTREALLOCATIONS.LIST>
</ALLLEDGERENTRIES.LIST>
<ALLLEDGERENTRIES.LIST>
<LEDGERNAME>Cash</LEDGERNAME>
<AMOUNT>10000</AMOUNT>
</ALLLEDGERENTRIES.LIST>
</VOUCHER>
</TALLYMESSAGE>
</ENVELOPE>
"""


def test_xml_export_captures_cost_centre(tmp_path):
    path = tmp_path / "export.xml"
    path.write_text(_XML_EXPORT, encoding="utf-8")

    _ledger_master, rows = extract_ledgers.extract_xml(str(path))

    salary_row = next(r for r in rows if r["Ledger Name"] == "Salary Expense")
    assert salary_row["Cost Centre"] == "Sales Dept; Admin Dept"
    cash_row = next(r for r in rows if r["Ledger Name"] == "Cash")
    assert cash_row["Cost Centre"] == ""


def test_build_tables_includes_cost_centre_column(tmp_path):
    path = tmp_path / "export.json"
    path.write_text(json.dumps(_sample_json_export()), encoding="utf-8")
    ledger_master, rows = extract_ledgers.extract(str(path))

    df, _summary = extract_ledgers.build_tables(
        ledger_master, rows, include_cancelled=False, from_date=None, to_date=None, ledger_filter=None
    )
    assert "Cost Centre" in df.columns


_LIVE_VOUCHER_XML = """<ENVELOPE><BODY><DATA><COLLECTION>
<VOUCHER>
<DATE>20240115</DATE>
<VOUCHERTYPENAME>Payment</VOUCHERTYPENAME>
<VOUCHERNUMBER>PAY/1</VOUCHERNUMBER>
<PARTYLEDGERNAME></PARTYLEDGERNAME>
<ALLLEDGERENTRIES.LIST>
<LEDGERNAME>Salary Expense</LEDGERNAME>
<AMOUNT>-10000</AMOUNT>
<COSTCENTREALLOCATIONS.LIST><NAME>Sales Dept</NAME><AMOUNT>-10000</AMOUNT></COSTCENTREALLOCATIONS.LIST>
</ALLLEDGERENTRIES.LIST>
<ALLLEDGERENTRIES.LIST>
<LEDGERNAME>Cash</LEDGERNAME>
<AMOUNT>10000</AMOUNT>
</ALLLEDGERENTRIES.LIST>
</VOUCHER>
</COLLECTION></DATA></BODY></ENVELOPE>
"""


class _FakeResponse:
    def __init__(self, text: str):
        self.text = text
        self.status_code = 200
        self.headers = {}


def test_fetch_vouchers_captures_cost_centre(monkeypatch):
    def fake_post(url, data=None, headers=None, timeout=None):
        return _FakeResponse(_LIVE_VOUCHER_XML)

    monkeypatch.setattr(tally_connector.requests, "post", fake_post)

    rows = tally_connector.fetch_vouchers(
        "localhost", 9000, "Test Co", datetime.date(2024, 1, 1), datetime.date(2024, 1, 31)
    )
    salary_row = next(r for r in rows if r["Ledger Name"] == "Salary Expense")
    assert salary_row["Cost Centre"] == "Sales Dept"
    cash_row = next(r for r in rows if r["Ledger Name"] == "Cash")
    assert cash_row["Cost Centre"] == ""
