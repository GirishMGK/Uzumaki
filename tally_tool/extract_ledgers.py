"""
Tally Ledger Extractor
=======================
Turns a TallyPrime "JSON (Data Interchange)" export (Masters + Vouchers) into
one flat, columnar table of every ledger's transactions -- no more opening
each ledger one by one and exporting it individually.

USAGE
-----
    python extract_ledgers.py --input "Transactions.json" --output "ledgers_output.xlsx"

    Optional flags:
      --format xlsx|csv|both     (default: xlsx)
      --include-cancelled        include cancelled/optional vouchers (default: excluded,
                                  same as what Tally shows you by default)
      --from-date YYYY-MM-DD     only show rows on/after this date (running balance is
                                  still computed over the FULL period, so it stays correct)
      --to-date   YYYY-MM-DD     only show rows on/before this date
      --ledger "Name1,Name2"     only extract these ledgers (comma separated, exact names)

HOW TO PRODUCE THE INPUT FILE FROM TALLY
-----------------------------------------
Gateway of Tally -> Display -> Day Book (or any report covering the full period)
  -> Alt+F2 to set the date range to the full year
  -> Alt+E (Export) -> Format: JSON (Data Interchange) -> Yes to "Export All"
This produces one JSON file containing every Ledger master and every Voucher
with its full ledger-entry detail -- exactly what this script expects.

WHAT THIS SCRIPT DOES
----------------------
1. Detects the file's encoding (TallyPrime exports UTF-16) and, if needed,
   streams a one-time UTF-8 copy alongside it (cached, so re-runs are fast).
2. Streams the JSON with ijson (does NOT load the whole file into memory --
   important since these exports can be hundreds of MB).
3. Collects every Ledger master (name, group, opening balance) and every
   Voucher's ledger entries (date, voucher type/no., debit, credit, narration...).
4. Builds one flat table: one row per ledger entry, with a running balance
   computed per ledger (Opening Balance + cumulative Debit - Credit), sorted
   by ledger name then date.
5. Writes an Excel workbook (or CSV) with:
     - "Transactions" sheet: the flat table
     - "Ledger Summary" sheet: opening / total debit / total credit / closing
       balance per ledger, for a quick tie-out against your trial balance.
6. Prints a control total (sum of all debits vs all credits) as a sanity check
   -- for a clean double-entry export these should match to the paisa.

SIGN CONVENTION (as stored by Tally's own export)
---------------------------------------------------
- Ledger opening balance: positive = Debit balance, negative = Credit balance.
- Each ledger entry's Debit/Credit is taken from the SIGN of its "amount"
  field (negative = Debit, positive = Credit) -- NOT from the "isdeemedpositive"
  flag. The flag looks like it should mean the same thing, but on this data it
  was found to be unreliable for some statutory/duty ledger entries (e.g. TDS
  lines on vouchers migrated from an older Tally version). The amount's sign
  was verified against multiple real vouchers (Receipt, Bank Payment with TDS)
  and is the only rule under which every voucher's ledger entries actually sum
  to zero -- if you ever port this script to a fresh (non-migrated) export,
  it's worth re-checking the control total in case that export's data doesn't
  have this quirk.
- Closing/Running balance = Opening Balance + (Debit - Credit), shown as a
  positive number "Dr" or a negative number "Cr" in the Balance columns.
"""

from __future__ import annotations

import argparse
import codecs
import datetime
import os
import sys
import time

import ijson
import pandas as pd


# --------------------------------------------------------------------------
# Step 1: make sure we have a UTF-8 copy of the file (ijson's fast C backend
# only understands UTF-8; TallyPrime exports UTF-16-LE with a BOM).
# --------------------------------------------------------------------------
def ensure_utf8(path: str) -> str:
    with open(path, "rb") as f:
        head = f.read(4)

    bom_len = 0
    if head.startswith(codecs.BOM_UTF8):
        return path  # already UTF-8
    if head.startswith(codecs.BOM_UTF16_LE):
        src_encoding = "utf-16-le"
        bom_len = 2
    elif head.startswith(codecs.BOM_UTF16_BE):
        src_encoding = "utf-16-be"
        bom_len = 2
    elif head[:2] == b"{\x00":
        src_encoding = "utf-16-le"  # no BOM, but starts with '{' as UTF-16LE
    elif head[:2] == b"\x00{":
        src_encoding = "utf-16-be"  # no BOM, but starts with '{' as UTF-16BE
    else:
        # No BOM detected -- assume it's already UTF-8/ASCII.
        return path

    cache_path = path + ".utf8.json"
    if os.path.exists(cache_path) and os.path.getmtime(cache_path) >= os.path.getmtime(path):
        print(f"  Using cached UTF-8 copy: {cache_path}")
        return cache_path

    print(f"  Converting {src_encoding} -> UTF-8 (one-time; cached for next run)...")
    t0 = time.time()
    decoder = codecs.getincrementaldecoder(src_encoding)()
    chunk_size = 4 * 1024 * 1024
    with open(path, "rb") as fin, open(cache_path, "w", encoding="utf-8", newline="") as fout:
        fin.seek(bom_len)  # skip the BOM bytes -- the explicit -le/-be codec doesn't strip it
        while True:
            chunk = fin.read(chunk_size)
            if not chunk:
                tail = decoder.decode(b"", final=True)
                if tail:
                    fout.write(tail)
                break
            text = decoder.decode(chunk)
            if text:
                fout.write(text)
    print(f"  Done in {time.time() - t0:.1f}s -> {cache_path}")
    return cache_path


# --------------------------------------------------------------------------
# Small parsing helpers
# --------------------------------------------------------------------------
def clean_num(val) -> float:
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip()
    if not s:
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def parse_tally_date(s):
    if not s:
        return None
    s = str(s).strip()
    if len(s) == 8 and s.isdigit():
        try:
            return datetime.date(int(s[0:4]), int(s[4:6]), int(s[6:8]))
        except ValueError:
            return None
    return None


def clean_str(val) -> str:
    if val is None:
        return ""
    return str(val).strip()


# --------------------------------------------------------------------------
# Step 2 + 3: stream the JSON, collect Ledger masters and Voucher entries
# --------------------------------------------------------------------------
def extract(utf8_path: str):
    ledger_master: dict[str, dict] = {}
    rows: list[dict] = []

    voucher_count = 0
    entry_count = 0
    seq = 0
    t0 = time.time()

    with open(utf8_path, "rb") as f:
        for item in ijson.items(f, "tallymessage.item"):
            meta = item.get("metadata") or {}
            mtype = meta.get("type")

            if mtype == "Ledger":
                name = clean_str(meta.get("name"))
                if not name:
                    continue
                ledger_master[name] = {
                    "group": clean_str(item.get("parent")),
                    "opening_balance": clean_num(item.get("openingbalance")),
                }

            elif mtype == "Voucher":
                voucher_count += 1
                date = parse_tally_date(item.get("date"))
                vch_type = clean_str(item.get("vouchertypename") or meta.get("vchtype"))
                vch_no = clean_str(item.get("vouchernumber"))
                reference = clean_str(item.get("reference"))
                party = clean_str(item.get("partyledgername"))
                narration = clean_str(item.get("narration"))
                guid = clean_str(meta.get("remoteid") or item.get("guid"))
                master_id = clean_str(item.get("masterid"))
                is_cancelled = bool(item.get("iscancelled"))
                is_optional = bool(item.get("isoptional"))

                entries = list(item.get("allledgerentries") or []) + list(item.get("ledgerentries") or [])
                for e in entries:
                    lname = clean_str(e.get("ledgername"))
                    if not lname:
                        continue
                    # NOTE: we deliberately use the SIGNED amount (negative = Debit,
                    # positive = Credit) rather than the "isdeemedpositive" flag.
                    # They usually agree, but on this data isdeemedpositive was found
                    # to be unreliable for some statutory/duty ledger entries (e.g.
                    # TDS lines on migrated vouchers) -- verified by tracing a real
                    # voucher that only balanced to zero using the amount's sign.
                    signed_amt = clean_num(e.get("amount"))
                    bills = e.get("billallocations") or []
                    bill_ref = "; ".join(clean_str(b.get("name")) for b in bills if b.get("name"))

                    seq += 1
                    entry_count += 1
                    rows.append(
                        {
                            "Ledger Name": lname,
                            "Date": date,
                            "Voucher Type": vch_type,
                            "Voucher No": vch_no,
                            "Reference": reference,
                            "Party Ledger": party,
                            "Narration": narration,
                            "Debit": -signed_amt if signed_amt < 0 else 0.0,
                            "Credit": signed_amt if signed_amt > 0 else 0.0,
                            "Bill Reference": bill_ref,
                            "Cancelled": is_cancelled,
                            "Optional": is_optional,
                            "Voucher GUID": guid,
                            "Master ID": master_id,
                            "_seq": seq,
                        }
                    )

                if voucher_count % 2000 == 0:
                    elapsed = time.time() - t0
                    print(f"  ...{voucher_count:,} vouchers / {entry_count:,} ledger entries "
                          f"processed ({elapsed:.0f}s elapsed)")

    elapsed = time.time() - t0
    print(f"  Finished streaming: {len(ledger_master):,} ledger masters, "
          f"{voucher_count:,} vouchers, {entry_count:,} ledger-entry rows ({elapsed:.0f}s)")
    return ledger_master, rows


# --------------------------------------------------------------------------
# Step 4: build the flat table + running balances
# --------------------------------------------------------------------------
def build_tables(ledger_master: dict, rows: list, include_cancelled: bool,
                  from_date, to_date, ledger_filter):
    df = pd.DataFrame(rows)
    if df.empty:
        raise SystemExit("No voucher ledger-entries were found in this file -- "
                          "check that you exported the Day Book (not just Masters).")

    if not include_cancelled:
        df = df[~df["Cancelled"] & ~df["Optional"]].copy()

    if ledger_filter:
        wanted = {n.strip() for n in ledger_filter.split(",") if n.strip()}
        df = df[df["Ledger Name"].isin(wanted)].copy()

    df["Ledger Group"] = df["Ledger Name"].map(
        lambda n: ledger_master.get(n, {}).get("group", "(Not in Ledger Masters)")
    )
    df["Opening Balance"] = df["Ledger Name"].map(
        lambda n: ledger_master.get(n, {}).get("opening_balance", 0.0)
    )

    # Sort for correct running-balance order: by ledger, then date, then
    # original file order (stable tie-break for same-day vouchers).
    df = df.sort_values(["Ledger Name", "Date", "_seq"], na_position="first").reset_index(drop=True)

    df["Net"] = df["Debit"] - df["Credit"]
    df["Running Balance"] = df["Opening Balance"] + df.groupby("Ledger Name")["Net"].cumsum()
    df["Running Balance (Dr/Cr)"] = df["Running Balance"].apply(
        lambda v: f"{abs(v):,.2f} {'Dr' if v >= 0 else 'Cr'}"
    )

    # Ledger Summary (built from the full ledger master list so dormant
    # ledgers with zero transactions still appear, opening == closing).
    agg = df.groupby("Ledger Name", as_index=False).agg(
        Total_Debit=("Debit", "sum"),
        Total_Credit=("Credit", "sum"),
        Txn_Count=("Debit", "size"),
    )
    master_names = set(ledger_master.keys())
    if ledger_filter:
        wanted = {n.strip() for n in ledger_filter.split(",") if n.strip()}
        master_names &= wanted
    master_df = pd.DataFrame(
        [
            {"Ledger Name": k, "Ledger Group": v["group"], "Opening Balance": v["opening_balance"]}
            for k, v in ledger_master.items()
            if k in master_names
        ]
    )
    summary = master_df.merge(agg, on="Ledger Name", how="outer")
    summary["Ledger Group"] = summary["Ledger Group"].fillna("(Not in Ledger Masters)")
    for col in ["Opening Balance", "Total_Debit", "Total_Credit", "Txn_Count"]:
        summary[col] = summary[col].fillna(0)
    summary["Closing Balance"] = summary["Opening Balance"] + summary["Total_Debit"] - summary["Total_Credit"]
    summary["Closing (Dr/Cr)"] = summary["Closing Balance"].apply(
        lambda v: f"{abs(v):,.2f} {'Dr' if v >= 0 else 'Cr'}"
    )
    summary = summary.rename(columns={"Total_Debit": "Total Debit", "Total_Credit": "Total Credit",
                                       "Txn_Count": "Transaction Count"})
    summary = summary.sort_values("Ledger Name").reset_index(drop=True)

    # Now apply the display date window (running balance above was computed
    # over the FULL period, so it stays correct even when we slice the view).
    if from_date:
        df = df[(df["Date"].isna()) | (df["Date"] >= from_date)]
    if to_date:
        df = df[(df["Date"].isna()) | (df["Date"] <= to_date)]

    df = df.drop(columns=["_seq", "Net"], errors="ignore")
    if not include_cancelled:
        # These vouchers were already filtered out above, so the columns are
        # redundant (always False) -- drop them to keep the sheet clean.
        df = df.drop(columns=["Cancelled", "Optional"], errors="ignore")

    ordered_cols = [
        "Ledger Name", "Ledger Group", "Date", "Voucher Type", "Voucher No",
        "Reference", "Party Ledger", "Narration", "Debit", "Credit",
        "Opening Balance", "Running Balance", "Running Balance (Dr/Cr)",
        "Bill Reference", "Voucher GUID", "Master ID",
    ]
    if include_cancelled:
        ordered_cols += ["Cancelled", "Optional"]
    ordered_cols = [c for c in ordered_cols if c in df.columns]
    df = df[ordered_cols]

    return df, summary


# --------------------------------------------------------------------------
# Step 5 + 6: write output + control total
# --------------------------------------------------------------------------
def write_output(df: pd.DataFrame, summary: pd.DataFrame, output_path: str, fmt: str):
    base, ext = os.path.splitext(output_path)

    if fmt in ("csv", "both"):
        txn_csv = f"{base}_transactions.csv"
        sum_csv = f"{base}_summary.csv"
        df.to_csv(txn_csv, index=False)
        summary.to_csv(sum_csv, index=False)
        print(f"  Wrote {txn_csv}")
        print(f"  Wrote {sum_csv}")

    if fmt in ("xlsx", "both"):
        xlsx_path = output_path if ext.lower() == ".xlsx" else f"{base}.xlsx"
        with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
            df.to_excel(writer, sheet_name="Transactions", index=False)
            summary.to_excel(writer, sheet_name="Ledger Summary", index=False)

            for sheet_name, frame in (("Transactions", df), ("Ledger Summary", summary)):
                ws = writer.sheets[sheet_name]
                ws.freeze_panes = "A2"
                ws.auto_filter.ref = ws.dimensions
                for col_idx, col_name in enumerate(frame.columns, start=1):
                    try:
                        max_len = max(
                            [len(str(col_name))]
                            + [len(str(v)) for v in frame[col_name].head(200).tolist()]
                        )
                    except Exception:
                        max_len = 12
                    ws.column_dimensions[ws.cell(row=1, column=col_idx).column_letter].width = min(max_len + 2, 45)
        print(f"  Wrote {xlsx_path}")


def print_control_total(df: pd.DataFrame):
    total_dr = df["Debit"].sum()
    total_cr = df["Credit"].sum()
    diff = total_dr - total_cr
    print()
    print("Control total (should match to the paisa for a clean double-entry export):")
    print(f"  Total Debit  : {total_dr:,.2f}")
    print(f"  Total Credit : {total_cr:,.2f}")
    print(f"  Difference   : {diff:,.2f}" + ("  <-- OK" if abs(diff) < 0.01 else "  <-- CHECK: does not balance"))


# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Extract all Tally ledgers into one columnar table.")
    ap.add_argument("--input", required=True, help="Path to Tally's JSON (Data Interchange) export")
    ap.add_argument("--output", required=True, help="Output file path (e.g. ledgers_output.xlsx)")
    ap.add_argument("--format", choices=["xlsx", "csv", "both"], default="xlsx")
    ap.add_argument("--include-cancelled", action="store_true",
                     help="Include cancelled/optional vouchers (default: excluded)")
    ap.add_argument("--from-date", default=None, help="YYYY-MM-DD, filter the displayed rows")
    ap.add_argument("--to-date", default=None, help="YYYY-MM-DD, filter the displayed rows")
    ap.add_argument("--ledger", default=None, help='Comma-separated exact ledger names to extract only those')
    args = ap.parse_args()

    from_date = datetime.date.fromisoformat(args.from_date) if args.from_date else None
    to_date = datetime.date.fromisoformat(args.to_date) if args.to_date else None

    print(f"Input : {args.input}")
    print(f"Output: {args.output}\n")

    print("Step 1/4: checking encoding...")
    utf8_path = ensure_utf8(args.input)

    print("Step 2/4: streaming the JSON export...")
    ledger_master, rows = extract(utf8_path)

    print("Step 3/4: building the ledger tables and running balances...")
    df, summary = build_tables(
        ledger_master, rows,
        include_cancelled=args.include_cancelled,
        from_date=from_date, to_date=to_date,
        ledger_filter=args.ledger,
    )
    print(f"  {len(df):,} transaction rows across {df['Ledger Name'].nunique():,} ledgers")

    print("Step 4/4: writing output...")
    write_output(df, summary, args.output, args.format)

    print_control_total(df)


if __name__ == "__main__":
    main()
