"""
Column Mapper — maps client dataset columns to standard audit field names.
All field keys are standardised; labels match the prompt specification.
"""
from __future__ import annotations
import re
import pandas as pd
from typing import Dict, Optional, List

# ── Standard audit field registry ────────────────────────────────────────────
REQUIRED_FIELDS: Dict[str, str] = {
    "gl_account":         "G/L Account",
    "gl_desc":            "G/L Acct Long Text",
    "assignment":         "Assignment",
    "reference":          "Reference",
    "doc_number":         "Document Number",
    "invoice_ref":        "Invoice Reference",
    "purchase_doc":       "Purchasing Document",
    "doc_type":           "Document Type",
    "doc_date":           "Document Date",
    "posting_date":       "Posting Date",
    "entry_date":         "Entry Date",
    "vendor_code":        "Vendor Code",
    "vendor_name":        "Vendor Name",
    "posting_key":        "Posting Key",
    "amount":             "Amount in Local Currency",
    "clearing_doc":       "Clearing Document",
    "text":               "Text",
    "offset_account":     "Offsetting account",
    "cost_center":        "Cost Center",
    "profit_center":      "Profit Center",
    "asset":              "Asset",
    "order":              "Order",
    "wht_amount":         "Withholding Tax Amt",
    "collective_inv":     "Collective Invoice",
    "dc_indicator":       "Debit/Credit ind",
    "sales_doc":          "Sales document",
    "billing_doc":        "Billing Document",
    "wbs":                "WBS Element",
    "customer":           "Customer",
    "reversal_indicator": "Reversal Indicator",
    "user_id":            "User ID",
    "approval_id":        "Approval ID",
    "grouping":           "Grouping",
    "sub_grouping":       "Sub Grouping",
}

# Common aliases per field key for auto-detection
_ALIASES: Dict[str, List[str]] = {
    "doc_number":         ["docnumber", "document no", "doc no", "voucher no", "journal no", "jv no", "entry no"],
    "posting_date":       ["postingdate", "post date", "period date", "posting dt", "value date"],
    "doc_date":           ["docdate", "document date", "invoice date", "date"],
    "entry_date":         ["entrydate", "created on", "system date", "entry dt"],
    "amount":             ["amount", "local amount", "lc amount", "txn amount", "value", "net amount", "dr/cr amount"],
    "user_id":            ["userid", "posted by", "created by", "user", "username", "entered by", "preparer"],
    "approval_id":        ["approvalid", "approver", "approved by", "checker", "authorised by", "auth by"],
    "vendor_name":        ["vendor", "vendor name", "supplier name", "party name", "creditor name"],
    "vendor_code":        ["vendorcode", "vendor id", "supplier code", "party code", "creditor code"],
    "gl_account":         ["gl", "account", "gl code", "account no", "ledger", "gl number"],
    "gl_desc":            ["gl description", "account name", "ledger name", "account description"],
    "text":               ["narration", "description", "remarks", "particulars", "note", "narr", "details"],
    "reversal_indicator": ["reversal", "reversed", "rev indicator", "rev flag"],
    "dc_indicator":       ["dr/cr", "debit credit", "dr cr", "d/c", "indicator", "s/h", "normal/reverse"],
    "customer":           ["customer", "debtor", "customer code", "customer id"],
    "cost_center":        ["costcenter", "cost ctr", "cc", "cost centre"],
    "profit_center":      ["profitcenter", "profit ctr", "pc", "profit centre"],
    "doc_type":           ["doctype", "document type", "type", "trans type", "voucher type"],
    "posting_key":        ["postingkey", "pk", "post key"],
    "reference":          ["ref", "ref no", "reference no", "ref number"],
    "invoice_ref":        ["invoice", "invoice no", "inv ref", "invoice reference"],
    "clearing_doc":       ["clearing", "clearing doc", "clear doc", "set off"],
    "wbs":                ["wbs", "wbs element", "project", "project code"],
    "purchase_doc":       ["po", "po number", "purchase order", "purchase doc"],
    "grouping":           ["grouping", "group", "account group", "ledger group", "fs item group"],
    "sub_grouping":       ["sub grouping", "subgrouping", "sub group", "sub-group", "account sub group"],
}


def auto_map_columns(df_columns: List[str]) -> Dict[str, Optional[str]]:
    """
    Auto-detect matches between dataset columns and standard audit fields.
    Priority: exact label match → partial label match → alias match.
    Returns {field_key: matched_column_name or None}.
    """
    mapping: Dict[str, Optional[str]] = {}
    col_lower: Dict[str, str] = {c.lower().strip(): c for c in df_columns}

    for key, label in REQUIRED_FIELDS.items():
        matched: Optional[str] = None
        label_l = label.lower().strip()

        # 1. Exact match on label
        if label_l in col_lower:
            matched = col_lower[label_l]

        # 2. Partial match — label contained in column or vice-versa
        if not matched:
            for col_l, col in col_lower.items():
                if label_l in col_l or col_l in label_l:
                    matched = col
                    break

        # 3. Alias match
        if not matched:
            for alias in _ALIASES.get(key, []):
                if alias in col_lower:
                    matched = col_lower[alias]
                    break

        mapping[key] = matched

    return mapping


def get_safe_series(df: pd.DataFrame, key: str, col_map: Dict) -> pd.Series:
    """Return the mapped column series, or a series of empty strings if unmapped."""
    col = col_map.get(key)
    if col and col in df.columns:
        return df[col]
    return pd.Series([""] * len(df), index=df.index, dtype=object)


_CURRENCY_JUNK_RE = re.compile(r"[₹$,\s]")
_PAREN_NEG_RE = re.compile(r"^\((.*)\)$")


def _is_text_dtype(s: pd.Series) -> bool:
    """True for text columns regardless of pandas version. Pandas 3.x infers
    a dedicated string dtype for text by default instead of the legacy
    'object' dtype, so a bare `dtype == object` check silently stops
    matching any string column on newer pandas."""
    return s.dtype == object or pd.api.types.is_string_dtype(s)


def get_safe_numeric(df: pd.DataFrame, key: str, col_map: Dict) -> pd.Series:
    """Return numeric series for mapped column, or zeros if unmapped/invalid.

    Strips common currency symbols / thousands separators and accounting-
    style parentheses first — otherwise amounts like "₹1,23,456.00" or
    "(1,234.56)" (very common in Tally/Excel GL exports) silently coerce to
    0 via errors="coerce", which every amount-based test relies on and would
    otherwise cause large-scale false negatives with no warning.
    """
    s = get_safe_series(df, key, col_map)
    if _is_text_dtype(s):
        cleaned = s.astype(str).str.strip()
        is_paren_neg = cleaned.str.match(_PAREN_NEG_RE)
        cleaned = cleaned.str.replace(_PAREN_NEG_RE, r"\1", regex=True)
        cleaned = cleaned.str.replace(_CURRENCY_JUNK_RE, "", regex=True)
        nums = pd.to_numeric(cleaned, errors="coerce")
        nums = nums.where(~is_paren_neg, -nums)
        return nums.fillna(0)
    return pd.to_numeric(s, errors="coerce").fillna(0)


def safe_str(series: pd.Series) -> pd.Series:
    """Normalise a series to lowercase stripped strings."""
    return series.fillna("").astype(str).str.strip().str.lower()
