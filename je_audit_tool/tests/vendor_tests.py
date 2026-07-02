"""
Vendor Master Data Exception Tests.

Tests covered (requires vendor master upload):
  1.  Duplicate Vendors (same GSTIN or same PAN → different vendor codes)
  2.  New Vendor Paid Within N Days of Creation (from vendor master CREATED DATE)
  3.  MSME Delayed Payment (>45 days — MSME Act)
  4.  MSME Exposure Summary
  5.  Inactive / Blocked Vendor Transactions
  6.  Suspicious Vendor Name Patterns
  7.  High-Value Entry with No Vendor Code
  8.  Sequential Vendor Codes (numeric runs ≥ 3)

Without vendor master: returns one informational entry per test group.
"""
from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import re
import pandas as pd
import numpy as np
from utils.column_mapper import get_safe_series, get_safe_numeric, safe_str
from utils.doc_type_classifier import get_types_for_category
from utils.vendor_master import (
    find_duplicate_vendors, get_msme_vendors,
    get_inactive_vendors, get_vendor_created_dates,
)

_SCORES   = {"Low": 1, "Medium": 3, "High": 5, "Critical": 8}
_CATEGORY = "Vendor & Master Data Risks"

# Enhanced suspicious name patterns (Indian audit context)
_SUSPICIOUS_PATTERNS = [
    # Obvious placeholders
    (r"^(test|dummy|temp|sample|placeholder)$",          "Placeholder / Test Name"),
    (r"^xxx+$",                                           "Placeholder (XXX…)"),
    (r"^(n/?a|nil|none|null|not available|na)$",         "Blank-masking Name (N/A / Nil / None)"),
    # Very short / numeric
    (r"^[a-z0-9]{1,3}$",                                 "Very Short Name (≤3 chars)"),
    (r"^\d{1,5}$",                                        "Purely Numeric Name"),
    # Keyboard mash
    (r"^(qwerty|asdf|zxcv|1234|abcd|abcde|aaaa+|1111+|xxxx+)", "Keyboard Pattern / Repeated Chars"),
    # Generic company terms with no real identity
    (r"^(supplier|vendor|party|customer|company|firm|enterprise|trader|misc|miscellaneous|general)$",
                                                          "Generic Placeholder Company Name"),
    # Bare legal suffix with nothing before it (e.g., "pvt ltd", "llp", "ltd")
    (r"^(pvt\.?\s*ltd\.?|private limited|limited|llp|lpl|partnership)$",
                                                          "Legal Suffix Without Entity Name"),
]


def _exc(idx_list, exc_type, risk_level, detail="") -> list:
    return [
        {
            "original_index":    i,
            "Exception Type":    exc_type,
            "Exception Category":_CATEGORY,
            "Risk Level":        risk_level,
            "Risk Score":        _SCORES[risk_level],
            "Detail":            detail,
        }
        for i in idx_list
    ]


def _info_entry(message: str) -> dict:
    """Return a single informational exception entry (no JE row associated)."""
    return {
        "original_index":    -1,
        "Exception Type":    "Vendor Analytics Skipped",
        "Exception Category":_CATEGORY,
        "Risk Level":        "Low",
        "Risk Score":        0,
        "Detail":            message,
    }


def run_vendor_tests(df: pd.DataFrame, col_map: dict, params: dict) -> list:
    """Run all vendor master data tests. Returns flat list of exception dicts."""
    exceptions: list = []

    vm_df   = params.get("vendor_master_df")
    dt_map  = params.get("doc_type_map", {})

    # ── JE series ─────────────────────────────────────────────────────────────
    vc           = safe_str(get_safe_series(df, "vendor_code",   col_map))
    vn           = safe_str(get_safe_series(df, "vendor_name",   col_map))
    doc_type_s   = safe_str(get_safe_series(df, "doc_type",      col_map))
    doc_number_s = safe_str(get_safe_series(df, "doc_number",    col_map))
    posting_date = pd.to_datetime(get_safe_series(df, "posting_date", col_map), errors="coerce")
    clearing_doc = safe_str(get_safe_series(df, "clearing_doc",  col_map))
    abs_amt      = get_safe_numeric(df, "amount", col_map).abs()

    materiality     = float(params.get("overall_materiality", 1_000_000))
    new_vendor_days = int(params.get("new_vendor_days", 30))

    inv_types = get_types_for_category(dt_map, "Vendor Invoice") or ["kr", "re", "mr", "ka"]
    pay_types = get_types_for_category(dt_map, "Payment")        or ["kz", "zp", "za", "sk", "sz"]

    # ── Gate: vendor master required for main analytics ───────────────────────
    if vm_df is None or vm_df.empty:
        exceptions.append(_info_entry(
            "Vendor master not uploaded — duplicate vendor (GSTIN/PAN), MSME delayed payment, "
            "vendor creation date analysis, and inactive vendor checks are all skipped. "
            "Upload vendor master on the Upload Data page to enable these tests."
        ))
    else:
        # ── 1. Duplicate Vendors (GSTIN / PAN) ───────────────────────────────
        dup_vendor_map = find_duplicate_vendors(vm_df)
        if dup_vendor_map:
            je_codes = set(vc.str.upper().unique())
            for code, others in dup_vendor_map.items():
                if code not in je_codes:
                    continue
                idx = df[vc.str.upper() == code].index.tolist()
                label = ", ".join(others[:4]) + ("…" if len(others) > 4 else "")
                exceptions.extend(_exc(
                    idx, "Duplicate Vendor (Same GSTIN / PAN)", "High",
                    f"Vendor {code} shares GSTIN or PAN with: {label} — "
                    f"possible duplicate master record or same entity registered under multiple codes",
                ))

        # ── 2. New Vendor Paid Within {new_vendor_days} Days of Creation ──────
        created_dates = get_vendor_created_dates(vm_df)
        if created_dates and pay_types:
            payment_rows = df[doc_type_s.isin(pay_types)]
            for idx in payment_rows.index:
                code     = vc.at[idx].upper()
                pay_date = posting_date.at[idx]
                created  = created_dates.get(code)
                if not created or pd.isna(pay_date) or not code:
                    continue
                days = (pay_date - created).days
                if 0 <= days <= new_vendor_days:
                    exceptions.append({
                        "original_index":    idx,
                        "Exception Type":    "New Vendor Paid Quickly",
                        "Exception Category":_CATEGORY,
                        "Risk Level":        "High",
                        "Risk Score":        _SCORES["High"],
                        "Detail":            (
                            f"Vendor {code} was created {days} days before this payment "
                            f"(created: {created.date()}, payment: {pay_date.date()}) "
                            f"— fictitious vendor risk"
                        ),
                    })

        # ── 3. MSME Delayed Payment (>45 days — MSME Act) ────────────────────
        msme_vendors = get_msme_vendors(vm_df)
        if msme_vendors:
            # Build doc_number → earliest posting_date lookup for payment matching
            doc_date_lookup: dict = {}
            for i in df.index:
                dn  = doc_number_s.at[i]
                pdt = posting_date.at[i]
                if dn and dn not in ("", "nan") and pd.notna(pdt) and dn not in doc_date_lookup:
                    doc_date_lookup[dn] = pdt

            msme_inv = df[vc.str.upper().isin(msme_vendors) & doc_type_s.isin(inv_types)]
            for idx in msme_inv.index:
                inv_date = posting_date.at[idx]
                clr      = clearing_doc.at[idx]
                if pd.isna(inv_date) or not clr or clr in ("", "nan"):
                    continue
                pay_date = doc_date_lookup.get(clr)
                if pay_date is None or pd.isna(pay_date):
                    continue
                days = (pay_date - inv_date).days
                if days > 45:
                    exceptions.append({
                        "original_index":    idx,
                        "Exception Type":    "MSME Payment Delay (>45 Days)",
                        "Exception Category":_CATEGORY,
                        "Risk Level":        "High",
                        "Risk Score":        _SCORES["High"],
                        "Detail":            (
                            f"Vendor {vc.at[idx]} is MSME registered. "
                            f"Payment (doc: {clr}) was {days} days after invoice — "
                            f"exceeds the 45-day statutory limit under the MSME Act"
                        ),
                    })

        # ── 4. MSME Exposure — high-value without MSME flag (informational) ───
        if msme_vendors:
            msme_amt  = abs_amt[vc.str.upper().isin(msme_vendors)].sum()
            total_amt = abs_amt.sum()
            pct       = (msme_amt / total_amt * 100) if total_amt > 0 else 0
            msme_txn  = vc.str.upper().isin(msme_vendors).sum()
            exceptions.append({
                "original_index":    -1,
                "Exception Type":    "MSME Exposure Summary",
                "Exception Category":_CATEGORY,
                "Risk Level":        "Low",
                "Risk Score":        0,
                "Detail":            (
                    f"MSME vendor transactions: {msme_txn:,} entries, "
                    f"₹{msme_amt:,.0f} ({pct:.1f}% of total GL amount). "
                    f"Verify timely payment compliance under MSME Act."
                ),
            })

        # ── 5. Inactive / Blocked Vendor Transactions ─────────────────────────
        inactive = get_inactive_vendors(vm_df)
        if inactive:
            idx = df[vc.str.upper().isin(inactive)].index.tolist()
            if idx:
                exceptions.extend(_exc(
                    idx, "Transaction with Inactive / Blocked Vendor", "Critical",
                    "Vendor is marked inactive or blocked in master data — "
                    "payment / transaction should not have been processed",
                ))

    # ── 6. Suspicious Vendor Name Patterns (no master required) ──────────────
    vn_lc = vn.str.strip().str.lower()
    for pattern, label in _SUSPICIOUS_PATTERNS:
        mask = vn_lc.str.match(pattern, na=False) & (vn_lc.str.len() > 0)
        idx  = df[mask].index.tolist()
        if idx:
            exceptions.extend(_exc(
                idx, f"Suspicious Vendor Name: {label}", "High",
                f"Vendor name matches suspicious pattern — {label}",
            ))

    # ── 7. High-Value Entry with No Vendor Code (no master required) ─────────
    no_vend   = (vc.str.len() == 0) | vc.isin(["", "nan", "0", "none"])
    hv_thresh = materiality * 0.05
    idx = df[no_vend & (abs_amt >= hv_thresh)].index.tolist()
    if idx:
        exceptions.extend(_exc(
            idx, "High-Value Entry with No Vendor Code", "Medium",
            f"Transaction ≥ ₹{hv_thresh:,.0f} has no vendor code — incomplete master data",
        ))

    # ── 8. Sequential Vendor Codes (no master required) ──────────────────────
    num_vc = vc[vc.str.match(r"^\d+$", na=False)]
    if num_vc.nunique() >= 3:
        # Keep the original (possibly zero-padded) string forms alongside the
        # int value used for the sequence check — reformatting via str(int)
        # loses leading zeros (e.g. SAP-style "0001000234"), so a match
        # against those original strings would silently find nothing.
        int_to_orig: dict = {}
        for orig in num_vc.unique():
            int_to_orig.setdefault(int(orig), set()).add(orig)
        nums = sorted(int_to_orig)
        seq_nums: set = set()
        run = 1
        for i in range(1, len(nums)):
            if nums[i] - nums[i - 1] == 1:
                run += 1
                if run >= 3:
                    seq_nums.update([nums[i - 2], nums[i - 1], nums[i]])
            else:
                run = 1
        if seq_nums:
            seq_vendors = {orig for n in seq_nums for orig in int_to_orig[n]}
            exceptions.extend(_exc(
                df[vc.isin(seq_vendors)].index.tolist(),
                "Sequential Vendor Codes", "High",
                "Vendor codes form a sequential numeric run ≥3 — "
                "possible batch-created fictitious vendors",
            ))

    return exceptions
