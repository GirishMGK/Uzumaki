"""
Vendor Master Utilities
=======================
Load, normalise, and query the uploaded vendor master file.
Expected columns (flexible matching):
  VENDOR CODE, VENDOR NAME, GST Number, PAN-NO, CREATED DATE,
  MSME, MSME NO, STATUS, ADDRESS, REGION, ACCOUNT GROUP NAME
"""
from __future__ import annotations
import pandas as pd

# ── Column name aliases (flexible matching) ──────────────────────────────────
_COL_ALIASES: dict[str, list[str]] = {
    "vendor_code":     ["vendor code", "vendor_code", "lifnr", "creditor",
                        "supplier code", "vendor no", "vend. code"],
    "vendor_name":     ["vendor name", "vendor_name", "name", "name1",
                        "supplier name", "party name"],
    "gstin":           ["gst number", "gstin", "gst no", "gst_no", "gst reg",
                        "gstnumber", "gst registration", "gstin no"],
    "pan":             ["pan-no", "pan no", "pan_no", "pan number", "panno",
                        "pan", "permanent account number"],
    "created_date":    ["created date", "creation date", "erdat", "create date",
                        "created_date", "vendor creation date"],
    "msme":            ["msme", "msme status", "msme_status", "udyam status",
                        "msme registered", "msme flag"],
    "msme_no":         ["msme no", "msme_no", "msme number", "udyam no",
                        "udyam registration no"],
    "msme_valid_from": ["msme valid from", "msme_valid_from", "udyam valid from",
                        "msme validity"],
    "status":          ["status", "vendor status", "blocked", "deletion flag",
                        "active", "vendor active status"],
    "account_group":   ["account group name", "vendor account group",
                        "ktokk", "account group"],
    "region":          ["region", "state", "region code"],
    "reconciliation":  ["reconciliation account", "recon account"],
    "currency":        ["curr", "currency", "local currency"],
}

# MSME flag values that indicate the vendor IS registered as MSME
_MSME_YES = {"Y", "YES", "1", "TRUE", "REGISTERED", "MSME", "ACTIVE", "X"}

# Status values that indicate the vendor is inactive / blocked
_INACTIVE = {"INACTIVE", "BLOCKED", "X", "DELETED", "B", "LOCKED",
             "DELETION", "BLOCK", "D"}


def _find_col(df_cols: list[str], key: str) -> str | None:
    """Return the first column in df_cols that matches aliases for key (case-insensitive)."""
    aliases = _COL_ALIASES.get(key, [key])
    lc = {c.strip().lower(): c for c in df_cols}
    for alias in aliases:
        if alias in lc:
            return lc[alias]
    return None


def load_vendor_master(file) -> pd.DataFrame:
    """Read CSV / Excel vendor master file into a raw DataFrame."""
    name = getattr(file, "name", "")
    if name.lower().endswith((".xlsx", ".xls")):
        return pd.read_excel(file, dtype=str)
    for enc in ("utf-8", "cp1252", "latin-1"):
        try:
            file.seek(0)
            return pd.read_csv(file, dtype=str, encoding=enc)
        except UnicodeDecodeError:
            continue
    file.seek(0)
    return pd.read_csv(file, dtype=str, encoding="utf-8", errors="replace")


def get_auto_column_mapping(vm_df: pd.DataFrame) -> dict[str, str]:
    """
    Return {field_key: original_column_name} for every field that was
    auto-detected from vm_df's column headers.  Used to pre-fill the
    mapping panel so users can see what was detected and override only
    what's wrong.
    """
    result: dict[str, str] = {}
    for std_key in _COL_ALIASES:
        src = _find_col(vm_df.columns.tolist(), std_key)
        if src:
            result[std_key] = src
    return result


def normalise_vendor_master(vm_df: pd.DataFrame) -> pd.DataFrame:
    """
    Return a normalised copy with standard column names.
    Unmapped columns are kept as-is.
    """
    vm = vm_df.copy()

    # Build rename map
    rename_map: dict[str, str] = {}
    for std_key in _COL_ALIASES:
        src = _find_col(vm.columns.tolist(), std_key)
        if src and src not in rename_map.values():
            rename_map[src] = std_key

    vm = vm.rename(columns=rename_map)

    # Normalise key string fields
    for col in ("vendor_code", "gstin", "pan"):
        if col in vm.columns:
            vm[col] = vm[col].fillna("").str.strip().str.upper()

    if "vendor_name" in vm.columns:
        vm["vendor_name"] = vm["vendor_name"].fillna("").str.strip()

    if "created_date" in vm.columns:
        vm["created_date"] = pd.to_datetime(vm["created_date"], errors="coerce")

    if "msme" in vm.columns:
        vm["msme"] = vm["msme"].fillna("").str.strip().str.upper()

    if "status" in vm.columns:
        vm["status"] = vm["status"].fillna("").str.strip().str.upper()

    return vm


def find_duplicate_vendors(vm_df: pd.DataFrame) -> dict[str, list[str]]:
    """
    Return {vendor_code: [other_vendor_codes_sharing_same_GSTIN_or_PAN]}.

    - Same GSTIN (≥15 chars) across different vendor codes → definite duplicate registration
    - Same PAN (10 chars) across different vendor codes → same legal entity,
      possibly with multiple state-wise GST registrations
    Blank / invalid values are ignored.
    """
    dups: dict[str, set] = {}

    # GSTIN-based
    if "gstin" in vm_df.columns and "vendor_code" in vm_df.columns:
        valid = vm_df[(vm_df["gstin"].str.len() >= 15) & (vm_df["vendor_code"].str.len() > 0)]
        for gstin, grp in valid.groupby("gstin"):
            codes = grp["vendor_code"].unique().tolist()
            if len(codes) > 1:
                for c in codes:
                    dups.setdefault(c, set()).update(x for x in codes if x != c)

    # PAN-based
    if "pan" in vm_df.columns and "vendor_code" in vm_df.columns:
        valid = vm_df[(vm_df["pan"].str.len() == 10) & (vm_df["vendor_code"].str.len() > 0)]
        for pan, grp in valid.groupby("pan"):
            codes = grp["vendor_code"].unique().tolist()
            if len(codes) > 1:
                for c in codes:
                    dups.setdefault(c, set()).update(x for x in codes if x != c)

    return {k: list(v) for k, v in dups.items()}


def get_msme_vendors(vm_df: pd.DataFrame) -> set[str]:
    """Return set of vendor codes where MSME flag indicates registration."""
    if "msme" not in vm_df.columns or "vendor_code" not in vm_df.columns:
        return set()
    mask = vm_df["msme"].isin(_MSME_YES) & (vm_df["vendor_code"].str.len() > 0)
    return set(vm_df.loc[mask, "vendor_code"].tolist())


def get_inactive_vendors(vm_df: pd.DataFrame) -> set[str]:
    """Return set of vendor codes that are inactive / blocked / deleted."""
    if "status" not in vm_df.columns or "vendor_code" not in vm_df.columns:
        return set()
    mask = vm_df["status"].isin(_INACTIVE) & (vm_df["vendor_code"].str.len() > 0)
    return set(vm_df.loc[mask, "vendor_code"].tolist())


def get_vendor_created_dates(vm_df: pd.DataFrame) -> dict[str, pd.Timestamp]:
    """Return {vendor_code: created_date (Timestamp)} for vendors with a valid creation date."""
    if "created_date" not in vm_df.columns or "vendor_code" not in vm_df.columns:
        return {}
    valid = vm_df[vm_df["created_date"].notna() & (vm_df["vendor_code"].str.len() > 0)]
    return dict(zip(valid["vendor_code"], valid["created_date"]))


def vendor_master_summary(vm_df: pd.DataFrame) -> dict:
    """Return basic stats about the uploaded vendor master."""
    total = len(vm_df)
    msme_count  = vm_df["msme"].isin(_MSME_YES).sum()       if "msme"   in vm_df.columns else 0
    inact_count = vm_df["status"].isin(_INACTIVE).sum()     if "status" in vm_df.columns else 0
    gstin_count = (vm_df["gstin"].str.len() >= 15).sum()    if "gstin"  in vm_df.columns else 0
    pan_count   = (vm_df["pan"].str.len()   == 10).sum()    if "pan"    in vm_df.columns else 0
    return {
        "total":       total,
        "msme":        int(msme_count),
        "inactive":    int(inact_count),
        "with_gstin":  int(gstin_count),
        "with_pan":    int(pan_count),
    }
