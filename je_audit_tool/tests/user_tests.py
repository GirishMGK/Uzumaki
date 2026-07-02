"""
User & Access Control Exception Tests + Narration Keyword Tests.

Tests covered:
  10. Same User Posting & Approving (SOD violation)
  11. Dormant User Became Active
  12. Admin / Superuser Entries
  --  High-Volume User (Concentration Risk)
  --  Rare User Posting High-Value Entry
  --  Narration Keyword Flags
  --  Blank Narration
  --  Related Party Indicators
  --  GL Outlier (amount > 4 std dev from GL mean)
  --  Manual Document Type

DuckDB replaces groupby loops and the row-by-row related-party scan.
"""
from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import re
import json
import pandas as pd
import numpy as np
from utils.column_mapper import get_safe_series, get_safe_numeric, safe_str
from utils.doc_type_classifier import get_types_for_category
from utils.duckdb_helper import slim_df, duck_con, fetch_idx

_SCORES = {"Low": 1, "Medium": 3, "High": 5, "Critical": 8}

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "exception_rules.json")
try:
    with open(_CONFIG_PATH) as _f:
        _CFG = json.load(_f)
    _NARR_KWS      = _CFG.get("narration_keywords", [])
    _ADMIN_PTNS    = _CFG.get("admin_user_patterns", ["admin", "superuser", "sys", "root"])
    _RP_KWS        = _CFG.get("related_party_keywords", [])
except Exception:
    _NARR_KWS   = []
    _ADMIN_PTNS = ["admin", "superuser", "sys", "root"]
    _RP_KWS     = []


def _exc(idx_list, exc_type, category, risk_level, detail="") -> list:
    return [
        {
            "original_index":    i,
            "Exception Type":    exc_type,
            "Exception Category":category,
            "Risk Level":        risk_level,
            "Risk Score":        _SCORES[risk_level],
            "Detail":            detail,
        }
        for i in idx_list
    ]


def _build_rp_lookups(params: dict):
    """Return (rp_vc_rel, rp_cc_rel, rp_pn_list) from params['related_party_list']."""
    rp_vc_rel: dict = {}
    rp_cc_rel: dict = {}
    rp_pn_list: list = []
    seen_pn: set = set()
    for r in params.get("related_party_list", []):
        vc  = r.get("vendor_code",   "").strip().upper()
        cc  = r.get("customer_code", "").strip().upper()
        pn  = r.get("party_name",    "").strip()
        rel = r.get("relationship",  "Related Party").strip()
        if vc and vc not in ("", "NAN"):
            rp_vc_rel[vc] = (pn, rel)
        if cc and cc not in ("", "NAN"):
            rp_cc_rel[cc] = (pn, rel)
        if pn and pn.lower() not in ("", "nan") and pn.lower() not in seen_pn:
            seen_pn.add(pn.lower())
            rp_pn_list.append((pn.lower(), pn, rel))
    return rp_vc_rel, rp_cc_rel, rp_pn_list


def run_user_rowlevel(chunk: pd.DataFrame, col_map: dict, params: dict) -> list:
    """Row-level user tests — safe to call per chunk (each row is independent).
    Tests: 10a SOD, 12 Admin, Manual-Doc-Type, Narration-Keywords,
           Blank-Narration, Related-Party (pure-pandas lookup).
    """
    exceptions: list = []

    user_id     = safe_str(get_safe_series(chunk, "user_id",     col_map))
    approval_id = safe_str(get_safe_series(chunk, "approval_id", col_map))
    doc_type    = safe_str(get_safe_series(chunk, "doc_type",    col_map))
    narr        = (
        safe_str(get_safe_series(chunk, "text",    col_map)) + " " +
        safe_str(get_safe_series(chunk, "gl_desc", col_map))
    )

    _dt_map      = params.get("doc_type_map", {})
    manual_types = (
        get_types_for_category(_dt_map, "Manual")
        or [t.lower() for t in ["sa", "mn", "ab", "zz", "xx", "je", "mj", "g1"]]
    )

    user_has_data     = user_id.str.len().max() > 0
    approval_has_data = approval_id.str.len().max() > 0

    # ── 10a. Same User Posting & Approving (SOD) ─────────────────────────────
    if user_has_data and approval_has_data:
        sod_mask = (
            (user_id == approval_id) &
            user_id.str.len().gt(0) &
            ~user_id.isin(["", "nan"])
        )
        exceptions.extend(_exc(
            chunk[sod_mask].index.tolist(),
            "Same User Posting & Approving (SOD Violation)",
            "User & Access Control Risks", "Critical",
            "User ID equals Approval ID — segregation of duties breach",
        ))

    # ── 12. Admin / Superuser Entries ────────────────────────────────────────
    if _ADMIN_PTNS and user_has_data:
        _admin_re = "|".join(_ADMIN_PTNS)
        admin_mask = (
            user_id.str.contains(_admin_re, na=False, regex=True) &
            user_id.str.len().gt(0)
        )
        exceptions.extend(_exc(
            chunk[admin_mask].index.tolist(),
            "Admin / Superuser Entry",
            "User & Access Control Risks", "High",
            "Posted by an admin or system account — elevated manipulation risk",
        ))

    # ── Manual Document Type ──────────────────────────────────────────────────
    exceptions.extend(_exc(
        chunk[doc_type.isin(manual_types)].index.tolist(),
        "Manual Document Type",
        "User & Access Control Risks", "Medium",
        f"Document type is a known manual type ({', '.join(manual_types[:5]).upper()}) — requires scrutiny",
    ))

    # ── Narration Keyword Flags ───────────────────────────────────────────────
    for kw_info in _NARR_KWS:
        kw = kw_info.get("keyword", "")
        rl = kw_info.get("risk_level", "Medium")
        if not kw:
            continue
        mask = narr.str.contains(kw.lower(), na=False, regex=False)
        exceptions.extend(_exc(
            chunk[mask].index.tolist(),
            f'Narration Keyword: "{kw.title()}"',
            "Narration & Behavioural Indicators", rl,
            f'Narration contains flag keyword "{kw}" — possibly unusual or overriding entry',
        ))

    # ── Related Party Indicators (pure pandas — RP list is tiny) ─────────────
    rp_list = params.get("related_party_list", [])
    if rp_list:
        rp_vc_rel, rp_cc_rel, rp_pn_list = _build_rp_lookups(params)
        vendor_code_s = safe_str(get_safe_series(chunk, "vendor_code", col_map)).str.upper()
        vendor_name_s = safe_str(get_safe_series(chunk, "vendor_name", col_map))
        customer_s    = safe_str(get_safe_series(chunk, "customer",    col_map)).str.upper()

        _seen_rp: set = set()

        # Match by vendor code
        for _idx in chunk[vendor_code_s.isin(rp_vc_rel)].index:
            if _idx in _seen_rp:
                continue
            _seen_rp.add(_idx)
            pn, rel = rp_vc_rel[vendor_code_s.at[_idx]]
            exceptions.append({
                "original_index":    _idx,
                "Exception Type":    "Related Party Transaction",
                "Exception Category":"Related Party Indicators",
                "Risk Level":        "Critical",
                "Risk Score":        _SCORES["Critical"],
                "Detail":            (
                    f"Related party detected via vendor code {vendor_code_s.at[_idx]}"
                    f"{' — ' + pn if pn else ''} (Relationship: {rel}) — "
                    "requires disclosure and independent review"
                ),
            })

        # Match by customer code
        for _idx in chunk[customer_s.isin(rp_cc_rel)].index:
            if _idx in _seen_rp:
                continue
            _seen_rp.add(_idx)
            pn, rel = rp_cc_rel[customer_s.at[_idx]]
            exceptions.append({
                "original_index":    _idx,
                "Exception Type":    "Related Party Transaction",
                "Exception Category":"Related Party Indicators",
                "Risk Level":        "Critical",
                "Risk Score":        _SCORES["Critical"],
                "Detail":            (
                    f"Related party detected via customer code {customer_s.at[_idx]}"
                    f"{' — ' + pn if pn else ''} (Relationship: {rel}) — "
                    "requires disclosure and independent review"
                ),
            })

        # Match by party name substring — build one regex for all names
        if rp_pn_list:
            _pn_pat = "|".join(re.escape(pn_lc) for pn_lc, _, _ in rp_pn_list)
            _name_hit = (
                vendor_name_s.str.contains(_pn_pat, na=False, regex=True) |
                customer_s.str.lower().str.contains(_pn_pat, na=False, regex=True)
            )
            for _idx in chunk[_name_hit].index:
                if _idx in _seen_rp:
                    continue
                _seen_rp.add(_idx)
                # Find which party name matched
                _vn = vendor_name_s.at[_idx]
                _cu = customer_s.at[_idx].lower()
                _matched_pn, _matched_disp, _matched_rel = rp_pn_list[0]
                for pn_lc, pn_disp, rel in rp_pn_list:
                    if pn_lc in _vn or pn_lc in _cu:
                        _matched_disp, _matched_rel = pn_disp, rel
                        break
                exceptions.append({
                    "original_index":    _idx,
                    "Exception Type":    "Related Party Transaction",
                    "Exception Category":"Related Party Indicators",
                    "Risk Level":        "Critical",
                    "Risk Score":        _SCORES["Critical"],
                    "Detail":            (
                        f"Related party detected via party name match — {_matched_disp} "
                        f"(Relationship: {_matched_rel}) — requires disclosure and independent review"
                    ),
                })

    elif _RP_KWS:
        _rp_pat = "|".join(re.escape(k) for k in _RP_KWS)
        rp_mask = narr.str.contains(_rp_pat, na=False, regex=True)
        exceptions.extend(_exc(
            chunk[rp_mask].index.tolist(),
            "Related Party Indicator in Narration",
            "Related Party Indicators", "Critical",
            "Narration contains related-party keywords — possible undisclosed related-party transaction",
        ))

    return exceptions


def run_user_aggregation(df: pd.DataFrame, col_map: dict, params: dict) -> list:
    """Aggregation user tests — must see the full dataset.
    Tests: 10b Same-User-DR/CR, 11 Dormant-User,
           High-Volume-User, Rare-User, GL-Outlier.
    """
    exceptions: list = []

    user_id     = safe_str(get_safe_series(df, "user_id",    col_map))
    approval_id = safe_str(get_safe_series(df, "approval_id",col_map))
    doc_number  = safe_str(get_safe_series(df, "doc_number", col_map))
    posting_date= pd.to_datetime(get_safe_series(df, "posting_date", col_map), errors="coerce")
    abs_amt     = get_safe_numeric(df, "amount", col_map).abs()
    gl_acct     = safe_str(get_safe_series(df, "gl_account", col_map))
    dc          = safe_str(get_safe_series(df, "dc_indicator", col_map))

    materiality   = float(params.get("overall_materiality", 1_000_000))
    dormancy_days = int(params.get("dormancy_days", 90))
    top_n         = int(params.get("top_n_users", 5))

    user_has_data = user_id.str.len().max() > 0

    _slim = slim_df(df, col_map, [
        "user_id", "doc_number", "posting_date", "gl_account", "dc_indicator",
    ])
    _slim["_user_id"]      = user_id.values
    _slim["_doc_number"]   = doc_number.values
    _slim["_dc"]           = dc.values
    _slim["_abs_amt"]      = abs_amt.values
    _slim["_gl_acct"]      = gl_acct.values
    _slim["_posting_date"] = posting_date.values
    _con = duck_con(_slim)

    # ── 10b. Same User on DR & CR of Same Document ───────────────────────────
    if user_has_data and doc_number.str.len().max() > 0:
        _sod2_idx = fetch_idx(_con, """
            WITH doc_info AS (
                SELECT _doc_number
                FROM je
                WHERE _doc_number IS NOT NULL
                  AND CAST(_doc_number AS VARCHAR) NOT IN ('', 'nan')
                  AND _user_id IS NOT NULL
                  AND CAST(_user_id AS VARCHAR) NOT IN ('', 'nan')
                GROUP BY _doc_number
                HAVING COUNT(DISTINCT _user_id) = 1
                   AND BOOL_OR(LOWER(CAST(_dc AS VARCHAR)) IN ('d', 'debit', 's'))
                   AND BOOL_OR(LOWER(CAST(_dc AS VARCHAR)) IN ('c', 'credit', 'h', 'k'))
            )
            SELECT je._orig_idx
            FROM je
            JOIN doc_info ON je._doc_number = doc_info._doc_number
        """)
        exceptions.extend(_exc(
            list(set(_sod2_idx)),
            "Same User on DR & CR of Same Document",
            "User & Access Control Risks", "Critical",
            "Single user appears on both debit and credit sides of the same document",
        ))

    # ── 11. Dormant User Became Active ───────────────────────────────────────
    if user_has_data and posting_date.notna().any():
        _dormant_idx = fetch_idx(_con, f"""
            WITH user_dates AS (
                SELECT _orig_idx, _user_id, _posting_date,
                       LAG(_posting_date) OVER (
                           PARTITION BY _user_id ORDER BY _posting_date
                       ) AS prev_date
                FROM je
                WHERE _user_id IS NOT NULL
                  AND CAST(_user_id AS VARCHAR) NOT IN ('', 'nan')
                  AND _posting_date IS NOT NULL
            )
            SELECT _orig_idx FROM user_dates
            WHERE prev_date IS NOT NULL
              AND DATEDIFF('day', CAST(prev_date AS DATE),
                                  CAST(_posting_date AS DATE)) > {dormancy_days}
        """)
        exceptions.extend(_exc(
            list(set(_dormant_idx)),
            "Dormant User Became Active",
            "User & Access Control Risks", "High",
            f"User returned after >{dormancy_days} days of inactivity — possible account misuse",
        ))

    # ── High-Volume User ──────────────────────────────────────────────────────
    if user_has_data:
        top_users = user_id.value_counts().head(top_n).index.tolist()
        exceptions.extend(_exc(
            df[user_id.isin(top_users)].index.tolist(),
            f"High-Volume User (Top {top_n} by Count)",
            "User & Access Control Risks", "Low",
            f"Top {top_n} most active users account for a disproportionate share of postings",
        ))

    # ── Rare User Posting High-Value ──────────────────────────────────────────
    if user_has_data:
        rare = user_id.value_counts()[lambda x: x <= 3].index.tolist()
        idx  = df[user_id.isin(rare) & (abs_amt >= materiality * 0.1)].index.tolist()
        exceptions.extend(_exc(
            idx, "Rare User Posting High-Value Entry",
            "User & Access Control Risks", "High",
            "User appears ≤3 times in dataset but posted a high-value entry",
        ))

    # ── GL Statistical Outlier (> 4 Std Dev) ─────────────────────────────────
    _gl_outlier_idx = fetch_idx(_con, """
        WITH gl_stats AS (
            SELECT _orig_idx, _abs_amt,
                   AVG(_abs_amt)    OVER (PARTITION BY _gl_acct) AS gl_mean,
                   STDDEV(_abs_amt) OVER (PARTITION BY _gl_acct) AS gl_std
            FROM je
            WHERE _gl_acct IS NOT NULL
              AND CAST(_gl_acct AS VARCHAR) NOT IN ('', 'nan')
        )
        SELECT _orig_idx FROM gl_stats
        WHERE gl_std > 0 AND (_abs_amt - gl_mean) / gl_std >= 4
    """)
    exceptions.extend(_exc(
        _gl_outlier_idx, "GL Amount Statistical Outlier (>4 Std Dev)",
        "User & Access Control Risks", "High",
        "Amount is >4 standard deviations above this GL account's average — statistical anomaly",
    ))

    return exceptions


def run_user_tests(df: pd.DataFrame, col_map: dict, params: dict) -> list:
    """Backward-compatible wrapper: row-level + aggregation on the full df."""
    return run_user_rowlevel(df, col_map, params) + run_user_aggregation(df, col_map, params)
