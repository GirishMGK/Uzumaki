"""File-based Stock Item master extraction (reports/inventory_stock.py's
extract_stock_items_from_export()) -- best-effort, since it's unconfirmed
whether Tally's JSON/XML Data Interchange export includes Stock Item
masters at all."""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from reports.inventory_stock import extract_stock_items_from_export


def test_extract_stock_items_from_json_export(tmp_path):
    export = {
        "tallymessage": [
            {
                "metadata": {"type": "Stock Item", "name": "Widget"},
                "parent": "Finished Goods",
                "baseunits": "Nos",
                "openingbalance": 100,
                "openingvalue": 10000,
                "closingbalance": "80 Nos",
                "closingvalue": 8000,
            },
            {
                "metadata": {"type": "Ledger", "name": "Cash"},
                "parent": "Cash-in-Hand",
                "openingbalance": 0,
            },
        ]
    }
    path = tmp_path / "export.json"
    path.write_text(json.dumps(export), encoding="utf-8")

    items = extract_stock_items_from_export(str(path))
    assert len(items) == 1
    item = items[0]
    assert item["Stock Item"] == "Widget"
    assert item["Stock Group"] == "Finished Goods"
    assert item["Opening Qty"] == 100.0
    assert item["Tally Closing Qty"] == 80.0


def test_extract_stock_items_returns_empty_when_absent(tmp_path):
    """Only Ledger/Voucher masters in the export -- no Stock Item masters
    at all. Must come back empty, not raise."""
    export = {
        "tallymessage": [
            {"metadata": {"type": "Ledger", "name": "Cash"}, "parent": "Cash-in-Hand", "openingbalance": 0},
        ]
    }
    path = tmp_path / "export.json"
    path.write_text(json.dumps(export), encoding="utf-8")

    assert extract_stock_items_from_export(str(path)) == []


_XML_EXPORT = """<ENVELOPE>
<TALLYMESSAGE>
<STOCKITEM NAME="Widget">
<PARENT>Finished Goods</PARENT>
<BASEUNITS>Nos</BASEUNITS>
<OPENINGBALANCE>100</OPENINGBALANCE>
<OPENINGVALUE>10000</OPENINGVALUE>
<CLOSINGBALANCE>80</CLOSINGBALANCE>
<CLOSINGVALUE>8000</CLOSINGVALUE>
</STOCKITEM>
</TALLYMESSAGE>
</ENVELOPE>
"""


def test_extract_stock_items_from_xml_export(tmp_path):
    path = tmp_path / "export.xml"
    path.write_text(_XML_EXPORT, encoding="utf-8")

    items = extract_stock_items_from_export(str(path))
    assert len(items) == 1
    assert items[0]["Stock Item"] == "Widget"
    assert items[0]["Opening Qty"] == 100.0
    assert items[0]["Tally Closing Qty"] == 80.0
