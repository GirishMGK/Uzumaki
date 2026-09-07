"""Phase 3 live-XML additions to tally_connector.py: fetch_stock_items()
and fetch_voucher_register()'s optional GST breakup / HSN capture.

No live Tally instance is available in this environment -- same technique
as test_cost_centre_parsing.py: monkeypatch requests.post to return a fixture
response matching the documented collection/FETCH shape.
"""
from __future__ import annotations

import datetime
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import tally_connector

_LEDGER_MASTER = {
    "Output CGST": {"group": "Duties & Taxes"},
    "Output SGST": {"group": "Duties & Taxes"},
}


class _FakeResponse:
    def __init__(self, text: str):
        self.text = text
        self.status_code = 200
        self.headers = {}


_STOCK_ITEM_XML = """<ENVELOPE><BODY><DATA><COLLECTION>
<STOCKITEM NAME="Widget">
<PARENT>Finished Goods</PARENT>
<BASEUNITS>Nos</BASEUNITS>
<OPENINGBALANCE>100</OPENINGBALANCE>
<OPENINGVALUE>10000</OPENINGVALUE>
<CLOSINGBALANCE>80 Nos</CLOSINGBALANCE>
<CLOSINGVALUE>8000</CLOSINGVALUE>
</STOCKITEM>
</COLLECTION></DATA></BODY></ENVELOPE>
"""


def test_fetch_stock_items(monkeypatch):
    def fake_post(url, data=None, headers=None, timeout=None):
        return _FakeResponse(_STOCK_ITEM_XML)

    monkeypatch.setattr(tally_connector.requests, "post", fake_post)

    items = tally_connector.fetch_stock_items("localhost", 9000, "Test Co")
    assert len(items) == 1
    item = items[0]
    assert item["Stock Item"] == "Widget"
    assert item["Stock Group"] == "Finished Goods"
    assert item["Unit"] == "Nos"
    assert item["Opening Qty"] == 100.0
    assert item["Opening Value"] == 10000.0
    assert item["Tally Closing Qty"] == 80.0  # "80 Nos" -- unit suffix stripped
    assert item["Tally Closing Value"] == 8000.0


_VOUCHER_REGISTER_XML = """<ENVELOPE><BODY><DATA><COLLECTION>
<VOUCHER>
<DATE>20240110</DATE>
<VOUCHERTYPENAME>Sales</VOUCHERTYPENAME>
<VOUCHERNUMBER>S/1</VOUCHERNUMBER>
<PARTYLEDGERNAME>XYZ Traders</PARTYLEDGERNAME>
<GUID>sale-guid-1</GUID>
<MASTERID>10</MASTERID>
<ALLLEDGERENTRIES.LIST><LEDGERNAME>XYZ Traders</LEDGERNAME><AMOUNT>-1180</AMOUNT></ALLLEDGERENTRIES.LIST>
<ALLLEDGERENTRIES.LIST><LEDGERNAME>Sales Account</LEDGERNAME><AMOUNT>1000</AMOUNT></ALLLEDGERENTRIES.LIST>
<ALLLEDGERENTRIES.LIST><LEDGERNAME>Output CGST</LEDGERNAME><AMOUNT>90</AMOUNT></ALLLEDGERENTRIES.LIST>
<ALLLEDGERENTRIES.LIST><LEDGERNAME>Output SGST</LEDGERNAME><AMOUNT>90</AMOUNT></ALLLEDGERENTRIES.LIST>
<ALLINVENTORYENTRIES.LIST><STOCKITEMNAME>Widget</STOCKITEMNAME><GSTHSNNAME>8471</GSTHSNNAME><ACTUALQTY>10 Nos</ACTUALQTY><RATE>100/Nos</RATE><AMOUNT>1000</AMOUNT></ALLINVENTORYENTRIES.LIST>
</VOUCHER>
</COLLECTION></DATA></BODY></ENVELOPE>
"""


def test_fetch_voucher_register_hsn_and_gst_breakup(monkeypatch):
    def fake_post(url, data=None, headers=None, timeout=None):
        return _FakeResponse(_VOUCHER_REGISTER_XML)

    monkeypatch.setattr(tally_connector.requests, "post", fake_post)

    rows = tally_connector.fetch_voucher_register(
        "localhost", 9000, "Test Co", {"Sales"},
        datetime.date(2024, 1, 1), datetime.date(2024, 1, 31),
        ledger_master=_LEDGER_MASTER,
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["HSN Code"] == "8471"
    assert row["CGST"] == 90.0
    assert row["SGST"] == 90.0
    assert row["IGST"] == 0.0


def test_fetch_voucher_register_gst_breakup_zero_without_ledger_master(monkeypatch):
    def fake_post(url, data=None, headers=None, timeout=None):
        return _FakeResponse(_VOUCHER_REGISTER_XML)

    monkeypatch.setattr(tally_connector.requests, "post", fake_post)

    rows = tally_connector.fetch_voucher_register(
        "localhost", 9000, "Test Co", {"Sales"},
        datetime.date(2024, 1, 1), datetime.date(2024, 1, 31),
    )
    assert rows[0]["CGST"] == 0.0
    assert rows[0]["SGST"] == 0.0
