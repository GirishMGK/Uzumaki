"""
Tally live-connect
===================
Pulls ledgers/vouchers directly from a running TallyPrime instance over its
built-in XML/HTTP interface, instead of requiring a manual "Alt+E -> Export
-> JSON" step first. Produces the exact same (ledger_master, rows) shape that
extract_ledgers.extract() builds from a JSON export, so build_tables(),
write_output(), and print_control_total() are reused completely unchanged --
this module only replaces the "where the data comes from" step.

PREREQUISITE (on the machine running Tally, done once per session)
--------------------------------------------------------------------
1. Restore/open the backup as a company inside TallyPrime itself -- nothing
   outside Tally can read its proprietary backup format directly.
2. F12 (Configure) -> Advanced Configuration -> set "Enable ODBC/XML Server"
   (or "Client/Server Configuration" on older TallyPrime) to Yes, and note
   the port (default 9000). Tally must stay open with the company loaded for
   the duration of the pull.

HOW THIS WORKS
--------------
Tally's XML server accepts an ENVELOPE-wrapped request over a plain HTTP POST
to http://<host>:<port>. Two request shapes are used here, both using Tally's
documented "custom COLLECTION + FETCH" mechanism (the same technique Tally's
own ODBC driver uses under the hood) rather than exporting a built-in report
like "Day Book", since a custom collection's field list is explicit and
stable across Tally versions/configurations, where a report's XML shape can
vary with report configuration:

  1. `list_companies()`     -- "List of Companies" collection: which company
                                names Tally currently has open, so the UI can
                                offer a picker instead of asking the user to
                                type the exact name.
  2. `fetch_ledger_master()` -- a Ledger collection (NAME, PARENT,
                                 OPENINGBALANCE) -- equivalent to the
                                 "Ledger" masters in the JSON export.
  3. `fetch_vouchers()`      -- a Voucher collection over the requested date
                                 range, fetching the same fields
                                 extract_ledgers.extract() reads from the
                                 JSON export (date, voucher type/no,
                                 reference, party, narration, per-entry
                                 ledger name + amount + isdeemedpositive,
                                 cancelled/optional flags, guid, master id).

NOT YET VERIFIED AGAINST A REAL TALLY INSTANCE
------------------------------------------------
This sandbox has no Tally install to test against, so the request/response
shapes below follow Tally's documented XML/collection schema but have not
been exercised against a live server. In particular:
  - Some Tally versions/voucher types nest ledger entries under
    ALLLEDGERENTRIES.LIST, others under LEDGERENTRIES.LIST -- both are
    checked here, mirroring the same dual-check extract_ledgers.py already
    does for the JSON export.
  - Field availability inside a custom FETCH list can vary by Tally release;
    if a field comes back empty, check TallyPrime's release notes / the
    exact FETCH list Tally accepted (it silently drops unknown fields rather
    than erroring).
Expect to iterate once run against a real, running TallyPrime.
"""

from __future__ import annotations

import datetime
import xml.etree.ElementTree as ET

import requests

DEFAULT_PORT = 9000
_TIMEOUT = 15  # seconds -- a local XML request should be fast; don't hang the UI


class TallyConnectionError(RuntimeError):
    """Raised for anything that stops us reaching/using the Tally XML server."""


def _post(host: str, port: int, xml_request: str) -> ET.Element:
    url = f"http://{host}:{port}"
    try:
        resp = requests.post(
            url,
            data=xml_request.encode("utf-8"),
            headers={"Content-Type": "text/xml"},
            timeout=_TIMEOUT,
        )
    except requests.exceptions.ConnectionError as exc:
        raise TallyConnectionError(
            f"Could not reach Tally at {url}. Make sure TallyPrime is open with the "
            "company loaded, and ODBC/XML Server is enabled (F12 -> Advanced "
            "Configuration -> Enable ODBC/XML Server)."
        ) from exc
    except requests.exceptions.Timeout as exc:
        raise TallyConnectionError(f"Tally at {url} did not respond in time.") from exc

    if resp.status_code != 200:
        raise TallyConnectionError(f"Tally returned HTTP {resp.status_code}.")

    text = resp.text.strip()
    if not text:
        raise TallyConnectionError("Tally returned an empty response.")

    try:
        return ET.fromstring(text)
    except ET.ParseError as exc:
        raise TallyConnectionError(
            "Tally's response wasn't valid XML -- it may have returned an error "
            f"page instead. First 300 chars: {text[:300]!r}"
        ) from exc


def test_connection(host: str, port: int = DEFAULT_PORT) -> tuple[bool, str]:
    """Quick reachability + company-list check for a 'Test Connection' button."""
    try:
        companies = list_companies(host, port)
    except TallyConnectionError as exc:
        return False, str(exc)
    if not companies:
        return False, "Connected to Tally, but no company appears to be open."
    return True, f"Connected. Open compan{'y' if len(companies) == 1 else 'ies'}: {', '.join(companies)}"


def list_companies(host: str, port: int = DEFAULT_PORT) -> list[str]:
    request_xml = """<ENVELOPE>
  <HEADER>
    <VERSION>1</VERSION>
    <TALLYREQUEST>EXPORT</TALLYREQUEST>
    <TYPE>COLLECTION</TYPE>
    <ID>List of Companies</ID>
  </HEADER>
  <BODY>
    <DESC>
      <TDL>
        <TDLMESSAGE>
          <COLLECTION NAME="List of Companies" ISMODIFY="No">
            <TYPE>Company</TYPE>
            <FETCH>NAME</FETCH>
          </COLLECTION>
        </TDLMESSAGE>
      </TDL>
    </DESC>
  </BODY>
</ENVELOPE>"""
    root = _post(host, port, request_xml)
    names = []
    for company in root.iter("COMPANY"):
        name_el = company.find("NAME")
        name = (name_el.text or "").strip() if name_el is not None else ""
        if not name:
            # some Tally versions put the name in the NAME attribute instead
            name = (company.get("NAME") or "").strip()
        if name:
            names.append(name)
    return names


def _static_vars(company: str | None, from_date: datetime.date | None, to_date: datetime.date | None) -> str:
    parts = []
    if company:
        parts.append(f"<SVCURRENTCOMPANY>{_xml_escape(company)}</SVCURRENTCOMPANY>")
    if from_date:
        parts.append(f"<SVFROMDATE>{from_date.strftime('%Y%m%d')}</SVFROMDATE>")
    if to_date:
        parts.append(f"<SVTODATE>{to_date.strftime('%Y%m%d')}</SVTODATE>")
    return "\n      ".join(parts)


def _xml_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace('"', "&quot;").replace("'", "&apos;")
    )


def fetch_ledger_master(host: str, port: int, company: str | None = None) -> dict[str, dict]:
    """Mirrors the {name: {group, opening_balance}} shape extract() builds
    from the JSON export's "Ledger" master records."""
    request_xml = f"""<ENVELOPE>
  <HEADER>
    <VERSION>1</VERSION>
    <TALLYREQUEST>EXPORT</TALLYREQUEST>
    <TYPE>COLLECTION</TYPE>
    <ID>Ledger Collection</ID>
  </HEADER>
  <BODY>
    <DESC>
      <STATICVARIABLES>
        {_static_vars(company, None, None)}
      </STATICVARIABLES>
      <TDL>
        <TDLMESSAGE>
          <COLLECTION NAME="Ledger Collection" ISMODIFY="No">
            <TYPE>Ledger</TYPE>
            <FETCH>NAME,PARENT,OPENINGBALANCE</FETCH>
          </COLLECTION>
        </TDLMESSAGE>
      </TDL>
    </DESC>
  </BODY>
</ENVELOPE>"""
    root = _post(host, port, request_xml)
    ledger_master: dict[str, dict] = {}
    for ledger in root.iter("LEDGER"):
        name_el = ledger.find("NAME")
        name = ((name_el.text or "").strip() if name_el is not None else "") or (ledger.get("NAME") or "").strip()
        if not name:
            continue
        parent_el = ledger.find("PARENT")
        ob_el = ledger.find("OPENINGBALANCE")
        ledger_master[name] = {
            "group": (parent_el.text or "").strip() if parent_el is not None else "",
            "opening_balance": _to_float(ob_el.text if ob_el is not None else None),
        }
    return ledger_master


def _to_float(val) -> float:
    if val is None:
        return 0.0
    s = str(val).strip()
    if not s:
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def fetch_vouchers(
    host: str,
    port: int,
    company: str | None,
    from_date: datetime.date | None,
    to_date: datetime.date | None,
) -> list[dict]:
    """Returns rows in the exact shape extract_ledgers.extract() produces,
    ready to hand straight to build_tables()."""
    if from_date is None or to_date is None:
        # Confirmed against a real Tally instance: a Voucher Collection
        # request with no SVFROMDATE/SVTODATE doesn't error -- Tally silently
        # returns its <CMPINFO> object-count diagnostic instead of voucher
        # data (a few hundred bytes, LEDGER>0</LEDGER> etc.), which then
        # fails to parse as the expected shape. Fail loudly here instead of
        # letting that confusing response reach the caller.
        raise TallyConnectionError(
            "Both From date and To date are required for a live pull -- Tally's XML "
            "server returns a diagnostic response instead of voucher data when no date "
            "range is given, not an error you can otherwise detect."
        )
    # NOTE: the sub-list field name itself (ALLLEDGERENTRIES.LIST /
    # LEDGERENTRIES.LIST) must be named in FETCH for Tally to include the
    # nested ledger-entry data at all -- confirmed against a real Tally
    # instance that a Voucher Collection FETCH without it returns the
    # voucher "shell" (date/party/narration) with no ledger entries.
    fetch_fields = (
        "DATE,VOUCHERTYPENAME,VOUCHERNUMBER,PARTYLEDGERNAME,NARRATION,REFERENCE,"
        "GUID,MASTERID,ISCANCELLED,ISOPTIONAL,ALLLEDGERENTRIES.LIST,LEDGERENTRIES.LIST"
    )
    request_xml = f"""<ENVELOPE>
  <HEADER>
    <VERSION>1</VERSION>
    <TALLYREQUEST>EXPORT</TALLYREQUEST>
    <TYPE>COLLECTION</TYPE>
    <ID>Voucher Collection</ID>
  </HEADER>
  <BODY>
    <DESC>
      <STATICVARIABLES>
        {_static_vars(company, from_date, to_date)}
      </STATICVARIABLES>
      <TDL>
        <TDLMESSAGE>
          <COLLECTION NAME="Voucher Collection" ISMODIFY="No">
            <TYPE>Voucher</TYPE>
            <FETCH>{fetch_fields}</FETCH>
          </COLLECTION>
        </TDLMESSAGE>
      </TDL>
    </DESC>
  </BODY>
</ENVELOPE>"""
    root = _post(host, port, request_xml)

    rows: list[dict] = []
    seq = 0
    for voucher in root.iter("VOUCHER"):
        date = _parse_date(_text(voucher, "DATE"))
        vch_type = _text(voucher, "VOUCHERTYPENAME")
        vch_no = _text(voucher, "VOUCHERNUMBER")
        reference = _text(voucher, "REFERENCE")
        party = _text(voucher, "PARTYLEDGERNAME")
        narration = _text(voucher, "NARRATION")
        guid = _text(voucher, "GUID")
        master_id = _text(voucher, "MASTERID")
        is_cancelled = _text(voucher, "ISCANCELLED").lower() == "yes"
        is_optional = _text(voucher, "ISOPTIONAL").lower() == "yes"

        entry_lists = voucher.findall("ALLLEDGERENTRIES.LIST") + voucher.findall("LEDGERENTRIES.LIST")
        for entry in entry_lists:
            lname = _text(entry, "LEDGERNAME")
            if not lname:
                continue
            amount = _to_float(_text(entry, "AMOUNT"))
            bill_names = [
                (b.findtext("NAME") or "").strip()
                for b in entry.findall("BILLALLOCATIONS.LIST")
                if (b.findtext("NAME") or "").strip()
            ]

            seq += 1
            rows.append(
                {
                    "Ledger Name": lname,
                    "Date": date,
                    "Voucher Type": vch_type,
                    "Voucher No": vch_no,
                    "Reference": reference,
                    "Party Ledger": party,
                    "Narration": narration,
                    # Same convention as the JSON-export path: signed amount
                    # drives Debit/Credit, not ISDEEMEDPOSITIVE.
                    "Debit": -amount if amount < 0 else 0.0,
                    "Credit": amount if amount > 0 else 0.0,
                    "Bill Reference": "; ".join(bill_names),
                    "Cancelled": is_cancelled,
                    "Optional": is_optional,
                    "Voucher GUID": guid,
                    "Master ID": master_id,
                    "_seq": seq,
                }
            )
    return rows


def _text(el: ET.Element, tag: str) -> str:
    child = el.find(tag)
    return (child.text or "").strip() if child is not None and child.text else ""


def _parse_date(s: str):
    if not s:
        return None
    s = s.strip()
    if len(s) == 8 and s.isdigit():
        try:
            return datetime.date(int(s[0:4]), int(s[4:6]), int(s[6:8]))
        except ValueError:
            return None
    return None


def pull_from_tally(
    host: str,
    port: int,
    company: str | None,
    from_date: datetime.date | None,
    to_date: datetime.date | None,
) -> tuple[dict, list[dict]]:
    """One-shot helper: (ledger_master, rows), ready for build_tables()."""
    ledger_master = fetch_ledger_master(host, port, company)
    rows = fetch_vouchers(host, port, company, from_date, to_date)
    return ledger_master, rows
