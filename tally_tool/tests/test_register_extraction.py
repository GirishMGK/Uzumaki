"""File-based Sales/Purchase Register extraction (reports/sales_purchase_register.py)
-- the new pass over allinventoryentries/ALLINVENTORYENTRIES.LIST that
extract_ledgers.extract()/extract_xml() already stream past but never read.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from reports.sales_purchase_register import extract_register_from_export


def _sample_json_export() -> dict:
    return {
        "tallymessage": [
            {
                "metadata": {"type": "Voucher"},
                "date": "20240110",
                "vouchertypename": "Sales",
                "vouchernumber": "S/1",
                "partyledgername": "XYZ Traders",
                "reference": "INV-1",
                "narration": "",
                "guid": "sale-guid-1",
                "masterid": "10",
                "iscancelled": False,
                "isoptional": False,
                "allledgerentries": [
                    {"ledgername": "XYZ Traders", "amount": -1180},
                    {"ledgername": "Sales Account", "amount": 1000},
                    {"ledgername": "Output CGST", "amount": 90},
                    {"ledgername": "Output SGST", "amount": 90},
                ],
                "allinventoryentries": [
                    {"stockitemname": "Widget", "actualqty": "10 Nos", "rate": "100/Nos", "amount": 1000},
                ],
            },
            {
                "metadata": {"type": "Voucher"},
                "date": "20240112",
                "vouchertypename": "Purchase",
                "vouchernumber": "P/1",
                "partyledgername": "ABC Suppliers",
                "reference": "",
                "narration": "",
                "guid": "purch-guid-1",
                "masterid": "11",
                "iscancelled": False,
                "isoptional": False,
                "allledgerentries": [
                    {"ledgername": "ABC Suppliers", "amount": 590},
                    {"ledgername": "Purchase Account", "amount": -500},
                    {"ledgername": "Input CGST", "amount": -45},
                    {"ledgername": "Input SGST", "amount": -45},
                ],
                "allinventoryentries": [
                    {"stockitemname": "Gadget", "billedqty": "5 Nos", "rate": "100/Nos", "amount": -500},
                ],
            },
            {
                "metadata": {"type": "Voucher"},
                "date": "20240113",
                "vouchertypename": "Sales",
                "vouchernumber": "S/2-CANCELLED",
                "partyledgername": "XYZ Traders",
                "reference": "",
                "narration": "",
                "guid": "sale-guid-2",
                "masterid": "12",
                "iscancelled": True,
                "isoptional": False,
                "allledgerentries": [
                    {"ledgername": "XYZ Traders", "amount": -100},
                    {"ledgername": "Sales Account", "amount": 100},
                ],
                "allinventoryentries": [
                    {"stockitemname": "Widget", "actualqty": "1 Nos", "rate": "100/Nos", "amount": 100},
                ],
            },
            {
                "metadata": {"type": "Voucher"},
                "date": "20240114",
                "vouchertypename": "Sales",
                "vouchernumber": "S/3-SERVICE",
                "partyledgername": "Service Client",
                "reference": "",
                "narration": "",
                "guid": "sale-guid-3",
                "masterid": "13",
                "iscancelled": False,
                "isoptional": False,
                "allledgerentries": [
                    {"ledgername": "Service Client", "amount": -500},
                    {"ledgername": "Service Income", "amount": 500},
                ],
                # No allinventoryentries at all -- a service invoice.
            },
        ]
    }


def test_extract_sales_register(tmp_path):
    path = tmp_path / "export.json"
    path.write_text(json.dumps(_sample_json_export()), encoding="utf-8")

    rows = extract_register_from_export(str(path), {"Sales"})
    # The cancelled voucher is excluded by default; the service invoice has
    # no stock items but still gets one row.
    item_rows = [r for r in rows if r["Stock Item"] == "Widget"]
    assert len(item_rows) == 1
    row = item_rows[0]
    assert row["Item Amount"] == 1000.0
    assert row["Voucher Total"] == 1180.0  # GST-inclusive invoice total
    assert row["Voucher No"] == "S/1"

    service_rows = [r for r in rows if r["Voucher No"] == "S/3-SERVICE"]
    assert len(service_rows) == 1
    assert service_rows[0]["Stock Item"] == ""
    assert service_rows[0]["Item Amount"] == 500.0


def test_extract_purchase_register(tmp_path):
    path = tmp_path / "export.json"
    path.write_text(json.dumps(_sample_json_export()), encoding="utf-8")

    rows = extract_register_from_export(str(path), {"Purchase"})
    assert len(rows) == 1
    row = rows[0]
    assert row["Stock Item"] == "Gadget"
    assert row["Item Amount"] == 500.0
    assert row["Voucher Total"] == 590.0


def test_extract_register_includes_cancelled_when_requested(tmp_path):
    path = tmp_path / "export.json"
    path.write_text(json.dumps(_sample_json_export()), encoding="utf-8")

    rows_default = extract_register_from_export(str(path), {"Sales"}, include_cancelled=False)
    assert not any(r["Voucher No"] == "S/2-CANCELLED" for r in rows_default)

    rows_all = extract_register_from_export(str(path), {"Sales"}, include_cancelled=True)
    assert any(r["Voucher No"] == "S/2-CANCELLED" for r in rows_all)


_XML_EXPORT = """<ENVELOPE>
<TALLYMESSAGE>
<VOUCHER VCHTYPE="Sales">
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
<ALLINVENTORYENTRIES.LIST><STOCKITEMNAME>Widget</STOCKITEMNAME><ACTUALQTY>10 Nos</ACTUALQTY><RATE>100/Nos</RATE><AMOUNT>1000</AMOUNT></ALLINVENTORYENTRIES.LIST>
</VOUCHER>
</TALLYMESSAGE>
</ENVELOPE>
"""


def test_extract_register_from_xml_export(tmp_path):
    path = tmp_path / "export.xml"
    path.write_text(_XML_EXPORT, encoding="utf-8")

    rows = extract_register_from_export(str(path), {"Sales"})
    assert len(rows) == 1
    assert rows[0]["Stock Item"] == "Widget"
    assert rows[0]["Item Amount"] == 1000.0
    assert rows[0]["Voucher Total"] == 1180.0
