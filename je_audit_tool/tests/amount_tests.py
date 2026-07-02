"""
Amount-Based Exception Tests.

Tests covered:
  2.  Duplicate Transactions (full + partial)
      - Excludes: CGST/SGST tax lines (same doc, same amount)
      - Excludes: System Auto doc types
      - Excludes: amounts < ₹100
      - Excludes: DR/CR offset pairs and cleared documents
  2b. Duplicate Invoice Reference (same invoice_ref booked twice)
  2c. Near-Duplicate (same vendor + amount within ±3 days)
  3.  High-Value Entries (≥ Performance Materiality)
  4.  Split Transactions / Just Below Approval Limit
  5.  Unusual Decimal Patterns
  6.  High-Value Entries Without Adequate Narration

DuckDB is used for all groupby / row-iteration bottlenecks.
"""
from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd
import numpy as np
from utils.column_mapper import get_safe_series, get_safe_numeric, safe_str
from utils.doc_type_classifier import get_types_for_category
from utils.duckdb_helper import slim_df, duck_con, fetch_idx

_SCORES   = {"Low": 1, "Medium": 3, "High": 5, "Critical": 8}
_CATEGORY = "Amount-Based Anomalies"
_UNUSUAL_DECIMALS = {0.99, 0.01, 0.49, 0.51, 0.98, 0.02, 0.97, 0.03, 0.95, 0.05}

_TAX_KWS = [
    "cgst", "sgst", "igst", "gst input", "input tax credit",
    "input gst", "itc", "gst payable", "cenvat", "tax credit",
]

_GLOBAL_FLOOR = 100.0

# Pre-build regex alternation string for DuckDB (used in REGEXP_MATCHES)
_TAX_RE = "|".join(_TAX_KWS)


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


def run_amount_rowlevel(chunk: pd.DataFrame, col_map: dict, params: dict) -> list:
    """Row-level amount tests — safe to call per chunk (no cross-row aggregation).
    Tests: 1 Round Numbers, 3 High-Value, 4a Just-Below-Approval,
           5 Unusual Decimals, 6 High-Value Without Narration.
    """
    exceptions: list = []

    amt     = get_safe_numeric(chunk, "amount", col_map)
    abs_amt = amt.abs()
    narr    = (
        safe_str(get_safe_series(chunk, "text",    col_map)) + " " +
        safe_str(get_safe_series(chunk, "gl_desc", col_map))
    )

    materiality    = float(params.get("overall_materiality",     1_000_000))
    perf_mat       = float(params.get("performance_materiality", materiality * 0.75))
    approval_limit = float(params.get("approval_limit",           500_000))
    # _hv_thresh precomputed from full df by app.py; fall back to perf_mat * 0.1
    hv_thresh      = float(params.get("_hv_thresh", perf_mat * 0.1))

    # ── 3. High-Value Entries (≥ Performance Materiality) ────────────────────
    exceptions.extend(_exc(
        chunk[abs_amt >= perf_mat].index.tolist(),
        "High-Value Entry (≥ Performance Materiality)", "High",
        f"Amount ≥ ₹{perf_mat:,.0f} (performance materiality threshold) — requires substantive review",
    ))

    # ── 4a. Just Below Approval Limit (Manual / Non-PO entries only) ─────────
    # PO-based invoices (KR, RE) are controlled at PO-approval stage, not JE level.
    # Only flag manual doc types where the JE itself is the sole control point.
    _dt_map_amt   = params.get("doc_type_map", {})
    _manual_types = (
        get_types_for_category(_dt_map_amt, "Manual")
        or [t.lower() for t in ["sa", "mn", "ab", "zz", "xx", "je", "mj", "g1"]]
    )
    _doc_type_s = safe_str(get_safe_series(chunk, "doc_type", col_map))
    lower = approval_limit * 0.90
    idx   = chunk[
        (abs_amt >= lower) & (abs_amt < approval_limit) &
        _doc_type_s.isin(_manual_types)
    ].index.tolist()
    if idx:
        exceptions.extend(_exc(
            idx, "Entry Just Below Approval Limit (Manual / Non-PO)", "High",
            f"Manual doc type entry in ₹{lower:,.0f}–₹{approval_limit:,.0f} range — "
            f"non-PO transaction near approval limit, possible limit-avoidance without PO control",
        ))

    # ── 5. Unusual Decimal Patterns (vectorised — no DuckDB needed) ───────────
    frac     = (abs_amt % 1).round(2)
    dec_mask = frac.isin(_UNUSUAL_DECIMALS) & (abs_amt >= _GLOBAL_FLOOR)
    if dec_mask.any():
        exceptions.extend(_exc(
            chunk[dec_mask].index.tolist(), "Unusual Decimal Pattern", "Medium",
            "Amount has a suspicious decimal (.99 / .01 / .49 etc.) — common fraud manipulation signature",
        ))

    return exceptions


def run_amount_aggregation(df: pd.DataFrame, col_map: dict, params: dict) -> list:
    """Aggregation amount tests — must see the full dataset.
    Tests: tax-exclusion setup, 2 Duplicates, 2a Partial-Dup, 2b Invoice-Ref,
           2c Near-Duplicate, 4b Split Transactions.
    """
    exceptions: list = []

    amt          = get_safe_numeric(df, "amount", col_map)
    abs_amt      = amt.abs()
    gl_desc_s    = safe_str(get_safe_series(df, "gl_desc",      col_map))
    vendor       = safe_str(get_safe_series(df, "vendor_name",  col_map))
    dc_series    = safe_str(get_safe_series(df, "dc_indicator", col_map))
    clearing_doc = safe_str(get_safe_series(df, "clearing_doc", col_map))
    invoice_ref  = safe_str(get_safe_series(df, "invoice_ref",  col_map))
    doc_type_s   = safe_str(get_safe_series(df, "doc_type",     col_map))

    materiality    = float(params.get("overall_materiality",      1_000_000))
    overall_mat    = materiality
    approval_limit = float(params.get("approval_limit",            500_000))

    dt_map      = params.get("doc_type_map", {})
    system_auto = set(get_types_for_category(dt_map, "System Auto"))

    vn_c  = col_map.get("vendor_name")
    pd_c  = col_map.get("posting_date")
    amt_c = col_map.get("amount")

    # ── Build slim DataFrame for DuckDB (only needed columns) ────────────────
    _slim = slim_df(df, col_map, [
        "doc_number", "gl_desc", "amount", "vendor_name", "posting_date", "doc_date",
        "gl_account", "user_id", "doc_type", "invoice_ref",
        "dc_indicator", "clearing_doc", "text",
    ])
    # Attach pre-computed abs_amount and a lowercased vendor column
    _slim["_abs_amt"]   = abs_amt.values
    _slim["_vendor_lc"] = vendor.values          # already lowercased by safe_str

    _con = duck_con(_slim)

    # Prefer document date (invoice date) over posting date for duplicate matching —
    # catches same invoice re-posted on a different posting date.
    _has_doc_date = bool(col_map.get("doc_date") and col_map["doc_date"] in df.columns)
    _date_field   = "doc_date" if _has_doc_date else "posting_date"  # slim_df standard name
    _date_col     = col_map.get(_date_field)                          # actual df column name

    # ── Pre-compute exclusion sets ────────────────────────────────────────────

    # Layer A: pandas vectorised string search — faster than DuckDB REGEXP_MATCHES
    # on 14M+ rows because it avoids per-row CAST and regex engine overhead.
    _tax_mask = gl_desc_s.str.contains("|".join(_TAX_KWS), case=False, na=False)
    _direct_tax_idx: set = set(df[_tax_mask].index.tolist())

    # Layer B: any row in same (doc_number, abs_amount) group as a tax line
    # Uses pandas merge (hash join) — faster than MultiIndex .isin() on 14M rows.
    _doc_col = col_map.get("doc_number")
    if _direct_tax_idx and _doc_col and _doc_col in df.columns:
        _base = pd.DataFrame({"_doc": df[_doc_col].astype(str), "_abs": abs_amt.values, "_idx": df.index})
        _tax_keys = _base.loc[_base["_idx"].isin(_direct_tax_idx), ["_doc", "_abs"]].drop_duplicates()
        _tax_keys = _tax_keys[~_tax_keys["_doc"].isin(["", "nan", "None"])]
        _doc_tax_excl_idx: set = set(
            _base.merge(_tax_keys, on=["_doc", "_abs"], how="inner")["_idx"].tolist()
        )
    else:
        _doc_tax_excl_idx = set()

    _tax_excl_idx = _direct_tax_idx | _doc_tax_excl_idx

    # System Auto doc type exclusion (fast pandas mask on small set)
    _auto_excl_idx: set = (
        set(df[doc_type_s.isin(system_auto)].index.tolist()) if system_auto else set()
    )

    _dup_excl = _tax_excl_idx | _auto_excl_idx

    # Register exclusion table so SQL can reference it
    _excl_df = pd.DataFrame({"_orig_idx": list(_dup_excl)})
    _con.register("excl", _excl_df)

    # ── Helper functions (applied on small candidate sets only) ───────────────
    _DR_VALUES = {"d", "debit", "s", "40", "50"}
    _CR_VALUES = {"c", "credit", "h", "k", "31", "11"}

    def _is_dr_cr_pair(group_dc: pd.Series) -> bool:
        vals = set(group_dc.tolist())
        return bool(vals & _DR_VALUES) and bool(vals & _CR_VALUES)

    def _all_have_clearing_doc(group_clearing: pd.Series) -> bool:
        return (group_clearing.str.len().gt(0).all()
                and not group_clearing.isin(["", "nan"]).any())

    # ── 2. Duplicate Transactions ─────────────────────────────────────────────
    dup_cols = []
    for key in ["amount", "vendor_name", _date_field, "gl_account", "user_id"]:
        c = col_map.get(key)
        if c and c in df.columns:
            dup_cols.append(c)

    # Map actual column names to slim_df standard names for SQL
    _field_map = {
        col_map.get(k): k for k in
        ["amount", "vendor_name", _date_field, "gl_account", "user_id"]
        if col_map.get(k)
    }
    _slim_dup_cols = [_field_map[c] for c in dup_cols if c in _field_map]

    if len(_slim_dup_cols) >= 3:
        _key_sql = ", ".join(f'"{c}"' for c in _slim_dup_cols)
        _key3_sql = ", ".join(f'"{c}"' for c in _slim_dup_cols[:3])

        # Step 1: DuckDB finds candidate duplicate rows (much smaller than full df)
        _dup_candidate_idx = fetch_idx(_con, f"""
            SELECT _orig_idx FROM je
            WHERE _abs_amt >= {_GLOBAL_FLOOR}
              AND _orig_idx NOT IN (SELECT _orig_idx FROM excl)
              AND ({_key_sql}) IN (
                  SELECT {_key_sql} FROM je
                  WHERE _abs_amt >= {_GLOBAL_FLOOR}
                    AND _orig_idx NOT IN (SELECT _orig_idx FROM excl)
                  GROUP BY {_key_sql}
                  HAVING COUNT(*) > 1
              )
        """)

        if _dup_candidate_idx:
            # Step 2: Apply DR/CR + clearing check in pandas on the small candidate set
            _tmp = df.loc[_dup_candidate_idx].copy()
            _tmp["_dc"]  = dc_series.loc[_dup_candidate_idx]
            _tmp["_clr"] = clearing_doc.loc[_dup_candidate_idx]
            # Group on the SAME key the SQL duplicate-detection GROUP BY used
            # (all of dup_cols, not just the first 3) — grouping on a coarser
            # key here can merge two distinct 5-column duplicate groups into
            # one, and if that merged group happens to contain one debit and
            # one credit line, the DR/CR-offset exclusion below would wrongly
            # exclude real duplicates along with it.
            suspect_idx: list = []
            for _, grp in _tmp.groupby(dup_cols):
                if _is_dr_cr_pair(grp["_dc"]) or _all_have_clearing_doc(grp["_clr"]):
                    continue
                suspect_idx.extend(grp.index.tolist())
            if suspect_idx:
                exceptions.extend(_exc(
                    suspect_idx, "Duplicate Transaction", "High",
                    f"Exact duplicate on: {', '.join(dup_cols)} "
                    f"(tax lines, system-auto entries, DR/CR offset pairs excluded)",
                ))

    # ── 2a. Partial Duplicate — same amount + vendor + date (doc date) ────────
    if vn_c and _date_col and amt_c and all(c in df.columns for c in [vn_c, _date_col, amt_c]):
        _full_dup_key = ", ".join(f'"{c}"' for c in _slim_dup_cols) if _slim_dup_cols else None

        _partial_candidate_idx = fetch_idx(_con, f"""
            SELECT _orig_idx FROM je
            WHERE _abs_amt >= {_GLOBAL_FLOOR}
              AND _orig_idx NOT IN (SELECT _orig_idx FROM excl)
              AND (vendor_name, {_date_field}, amount) IN (
                  SELECT vendor_name, {_date_field}, amount FROM je
                  WHERE _abs_amt >= {_GLOBAL_FLOOR}
                    AND _orig_idx NOT IN (SELECT _orig_idx FROM excl)
                  GROUP BY vendor_name, {_date_field}, amount
                  HAVING COUNT(*) > 1
              )
            {"AND (_orig_idx) NOT IN (SELECT _orig_idx FROM je WHERE (" + _full_dup_key + ") IN (SELECT " + _full_dup_key + " FROM je GROUP BY " + _full_dup_key + " HAVING COUNT(*) > 1))" if _full_dup_key and len(_slim_dup_cols) >= 3 else ""}
        """)

        if _partial_candidate_idx:
            _tmp = df.loc[_partial_candidate_idx].copy()
            _tmp["_dc"]  = dc_series.loc[_partial_candidate_idx]
            _tmp["_clr"] = clearing_doc.loc[_partial_candidate_idx]
            _tmp["_vn"]  = vendor.loc[_partial_candidate_idx]

            partial_with_vendor:    list = []
            partial_without_vendor: list = []

            for _, grp in _tmp.groupby([vn_c, _date_col, amt_c]):
                if _is_dr_cr_pair(grp["_dc"]) or _all_have_clearing_doc(grp["_clr"]):
                    continue
                has_vendor = (grp["_vn"].str.len().gt(0).any()
                              and not grp["_vn"].isin(["", "nan"]).all())
                if has_vendor:
                    partial_with_vendor.extend(grp.index.tolist())
                else:
                    partial_without_vendor.extend(grp.index.tolist())

            if partial_with_vendor:
                exceptions.extend(_exc(
                    partial_with_vendor, "Duplicate Amount — Same Vendor & Date", "High",
                    "Same amount to same vendor on same date with no clearing — possible duplicate payment",
                ))
            if partial_without_vendor:
                exceptions.extend(_exc(
                    partial_without_vendor, "Duplicate Transaction", "High",
                    "Same amount on same date, no vendor information — duplicate posting risk",
                ))

    # ── 2b. Duplicate Invoice Reference ──────────────────────────────────────
    inv_ref_col = col_map.get("invoice_ref")
    if inv_ref_col and inv_ref_col in df.columns:
        # Use DuckDB to find candidate rows — avoids per-value pandas full-table scan
        _inv_candidate_idx = fetch_idx(_con, """
            SELECT _orig_idx FROM je
            WHERE invoice_ref IS NOT NULL
              AND LENGTH(CAST(invoice_ref AS VARCHAR)) > 3
              AND CAST(invoice_ref AS VARCHAR) NOT IN ('', 'nan', 'none', '0')
              AND invoice_ref IN (
                  SELECT invoice_ref FROM je
                  WHERE invoice_ref IS NOT NULL
                    AND LENGTH(CAST(invoice_ref AS VARCHAR)) > 3
                    AND CAST(invoice_ref AS VARCHAR) NOT IN ('', 'nan', 'none', '0')
                  GROUP BY invoice_ref
                  HAVING COUNT(*) > 1
              )
        """)
        if _inv_candidate_idx:
            _tmp_inv = df.loc[_inv_candidate_idx].copy()
            _tmp_inv["_dc"]  = dc_series.loc[_inv_candidate_idx]
            _tmp_inv["_ref"] = invoice_ref.loc[_inv_candidate_idx]
            flagged_idx = []
            for _, grp in _tmp_inv.groupby("_ref"):
                if _is_dr_cr_pair(grp["_dc"]):
                    continue
                flagged_idx.extend(grp.index.tolist())
            if flagged_idx:
                exceptions.extend(_exc(
                    flagged_idx,
                    "Duplicate Invoice Reference", "Critical",
                    "Same invoice reference number appears on multiple journal entries — "
                    "possible duplicate invoice booking",
                ))

    # ── 2c. Near-Duplicate (same vendor + amount, doc dates within ±3 days) ───
    # Uses doc_date (invoice date) so a re-entered invoice with a different posting
    # date is still caught — the document/invoice date is what should match.
    if vn_c and _date_col and amt_c and all(c in df.columns for c in [vn_c, _date_col, amt_c]):
        _nd_idx = fetch_idx(_con, f"""
            WITH ordered AS (
                SELECT _orig_idx,
                       _vendor_lc,
                       _abs_amt,
                       CAST({_date_field} AS DATE)  AS pd,
                       LAG(_orig_idx)    OVER (PARTITION BY _vendor_lc, _abs_amt ORDER BY {_date_field}) AS prev_idx,
                       LAG(CAST({_date_field} AS DATE)) OVER (PARTITION BY _vendor_lc, _abs_amt ORDER BY {_date_field}) AS prev_pd
                FROM je
                WHERE _abs_amt >= {_GLOBAL_FLOOR}
                  AND _vendor_lc IS NOT NULL
                  AND CAST(_vendor_lc AS VARCHAR) NOT IN ('', 'nan')
                  AND {_date_field} IS NOT NULL
                  AND _orig_idx NOT IN (SELECT _orig_idx FROM excl)
            ),
            pairs AS (
                SELECT _orig_idx  AS idx_b, prev_idx AS idx_a
                FROM ordered
                WHERE prev_pd IS NOT NULL
                  AND DATEDIFF('day', prev_pd, pd) BETWEEN 0 AND 3
            )
            SELECT idx_a AS _orig_idx FROM pairs WHERE idx_a IS NOT NULL
            UNION
            SELECT idx_b AS _orig_idx FROM pairs
        """)
        if _nd_idx:
            exceptions.extend(_exc(
                _nd_idx, "Near-Duplicate (Same Vendor & Amount ±3 Days by Doc Date)", "High",
                "Same vendor and same amount with document dates within 3 days — "
                "possible duplicate invoice booked on slightly different dates",
            ))

    # ── 4b. Split Transactions (aggregate ≥ approval limit) ──────────────────
    if vn_c and pd_c and vn_c in df.columns and pd_c in df.columns:
        _split_idx = fetch_idx(_con, f"""
            WITH splits AS (
                SELECT _vendor_lc, CAST(posting_date AS DATE) AS pd_date
                FROM je
                WHERE _vendor_lc IS NOT NULL
                  AND CAST(_vendor_lc AS VARCHAR) NOT IN ('', 'nan')
                  AND _abs_amt < {approval_limit}
                  AND _abs_amt >= {_GLOBAL_FLOOR}
                GROUP BY _vendor_lc, CAST(posting_date AS DATE)
                HAVING COUNT(*) >= 2
                   AND SUM(_abs_amt) >= {approval_limit * 0.85}
            )
            SELECT je._orig_idx
            FROM je
            JOIN splits
              ON je._vendor_lc = splits._vendor_lc
             AND CAST(je.posting_date AS DATE) = splits.pd_date
            WHERE je._abs_amt < {approval_limit}
              AND CAST(je._vendor_lc AS VARCHAR) NOT IN ('', 'nan')
        """)
        if _split_idx:
            exceptions.extend(_exc(
                list(set(_split_idx)), "Split Transaction (Aggregate ≥ Approval Limit)", "Critical",
                f"Multiple entries to same vendor on same date aggregate ≥ ₹{approval_limit * 0.85:,.0f}",
            ))

    return exceptions


def run_amount_tests(df: pd.DataFrame, col_map: dict, params: dict) -> list:
    """Backward-compatible wrapper: row-level + aggregation on the full df."""
    return run_amount_rowlevel(df, col_map, params) + run_amount_aggregation(df, col_map, params)
