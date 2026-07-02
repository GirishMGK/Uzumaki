"""
Document Type Classifier
========================
Manages SAP / ERP document type → category mapping.
Supports client-specific overrides via an uploaded master file.

Categories:
  Manual, Payment, Vendor Invoice, Customer Invoice, Credit Memo,
  Reversal, Asset, Goods Movement, System Auto, Opening Balance
"""
from __future__ import annotations
import pandas as pd

# ─── Default SAP Document Type Classifications ───────────────────────────────
# {TYPE_CODE: (Description, Category)}
DEFAULT_DOC_TYPES: dict[str, tuple[str, str]] = {
    "AA": ("Asset posting",                "Asset"),
    "AB": ("Accounting document",          "Manual"),
    "AF": ("Dep. postings",                "System Auto"),
    "AN": ("Net asset posting",            "Asset"),
    "DA": ("Customer document",            "Customer Invoice"),
    "DG": ("Customer credit memo",         "Credit Memo"),
    "DR": ("Customer invoice",             "Customer Invoice"),
    "DZ": ("Customer payment",             "Payment"),
    "EU": ("Euro rounding difference",     "System Auto"),
    "EX": ("External document",            "Manual"),
    "G1": ("Manual journal entry",         "Manual"),
    "GR": ("Goods receipt",                "Goods Movement"),
    "GS": ("Statistical posting",          "System Auto"),
    "JE": ("Journal entry",                "Manual"),
    "KA": ("Vendor document",              "Vendor Invoice"),
    "KG": ("Vendor credit memo",           "Credit Memo"),
    "KN": ("Net vendor",                   "Vendor Invoice"),
    "KP": ("Account maintenance",          "System Auto"),
    "KR": ("Vendor invoice",               "Vendor Invoice"),
    "KZ": ("Vendor payment",               "Payment"),
    "MJ": ("Manual journal",               "Manual"),
    "MN": ("Manual entry",                 "Manual"),
    "MR": ("Invoice receipt (MM)",         "Vendor Invoice"),
    "MW": ("VAT correction document",      "System Auto"),
    "OB": ("Opening balance",              "Opening Balance"),
    "PC": ("Profit centre document",       "System Auto"),
    "PR": ("Price change document",        "System Auto"),
    "PY": ("Payment",                      "Payment"),
    "RE": ("Invoice receipt (MM-IV)",      "Vendor Invoice"),
    "RV": ("Billing document transfer",    "Customer Invoice"),
    "SA": ("G/L account document",         "Manual"),
    "SK": ("Cash document",                "Payment"),
    "SL": ("Special purpose ledger",       "System Auto"),
    "SR": ("Goods receipt (returns)",      "Goods Movement"),
    "ST": ("Stock transfer",               "Goods Movement"),
    "SU": ("Adjustment document",          "Manual"),
    "SZ": ("Vendor payment (FI)",          "Payment"),
    "WA": ("Goods issue",                  "Goods Movement"),
    "WE": ("Goods receipt",                "Goods Movement"),
    "WI": ("Inventory difference",         "Goods Movement"),
    "WL": ("Goods issue / delivery",       "Goods Movement"),
    "WN": ("Net goods receipt",            "Goods Movement"),
    "XX": ("Manual document",              "Manual"),
    "ZA": ("Payment posting",              "Payment"),
    "ZJ": ("Customer invoice (custom)",    "Customer Invoice"),
    "ZP": ("Customer payment",             "Payment"),
    "ZR": ("Reversal document",            "Reversal"),
    "ZV": ("Payment clearing",             "Payment"),
    "ZZ": ("Parked / unposted JE",         "Manual"),
}

CATEGORY_LABELS: list[str] = [
    "Manual",
    "Payment",
    "Vendor Invoice",
    "Customer Invoice",
    "Credit Memo",
    "Reversal",
    "Asset",
    "Goods Movement",
    "System Auto",
    "Opening Balance",
]

# ─── Public API ───────────────────────────────────────────────────────────────

def build_category_map(custom_df: pd.DataFrame | None = None) -> dict[str, str]:
    """
    Build {doc_type_code_lower: category} map.

    Starts from DEFAULT_DOC_TYPES and overlays any rows in custom_df.
    custom_df must have at minimum columns: 'Type' and 'Category'.
    Returns a dict with lowercase keys, e.g. {"sa": "Manual", "kr": "Vendor Invoice", ...}
    """
    # Start from SAP defaults
    cat_map: dict[str, str] = {
        code.lower(): cat
        for code, (_, cat) in DEFAULT_DOC_TYPES.items()
    }

    if custom_df is not None and not custom_df.empty:
        custom_df = custom_df.copy()
        # Normalise column headers
        custom_df.columns = [str(c).strip().lower() for c in custom_df.columns]

        # Accept common variants of the type-code column name
        type_col = next(
            (c for c in custom_df.columns
             if c in ("type", "doc type", "document type", "doc_type", "doctype")),
            None,
        )
        # Accept common variants of the category column name
        cat_col = next(
            (c for c in custom_df.columns
             if c in ("category", "classification", "type category", "doc category")),
            None,
        )

        if type_col and cat_col:
            for _, row in custom_df.iterrows():
                code = str(row[type_col]).strip().upper()
                cat  = str(row[cat_col]).strip()
                if code and code != "NAN" and cat and cat != "NAN":
                    cat_map[code.lower()] = cat

    return cat_map


def get_types_for_category(category_map: dict, category: str) -> list[str]:
    """Return list of lowercase doc type codes that belong to *category*."""
    return [
        code for code, cat in category_map.items()
        if cat.lower() == category.lower()
    ]


def load_doc_type_master(file) -> pd.DataFrame:
    """Read an uploaded CSV / Excel doc-type master file into a DataFrame."""
    name = getattr(file, "name", "")
    if name.lower().endswith((".xlsx", ".xls")):
        return pd.read_excel(file, dtype=str)
    # CSV — try common encodings
    for enc in ("utf-8", "cp1252", "latin-1"):
        try:
            file.seek(0)
            return pd.read_csv(file, dtype=str, encoding=enc)
        except UnicodeDecodeError:
            continue
    file.seek(0)
    return pd.read_csv(file, dtype=str, encoding="utf-8", errors="replace")


def default_type_df() -> pd.DataFrame:
    """Return the full default SAP classification table as a DataFrame."""
    rows = [
        {"Type": code, "Description": desc, "Category": cat}
        for code, (desc, cat) in sorted(DEFAULT_DOC_TYPES.items())
    ]
    return pd.DataFrame(rows)
