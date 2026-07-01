"""
Timing-Based Exception Tests.

Tests covered:
  7.  Weekend / Holiday Postings
  8.  Late Month-End Adjustments
  8b. Entries Near Year-End Cutoff
  9.  Entries Before System Lock
  10. Burst Posting by Same User
  --  Backdated / Late Postings
  --  Expense Suppression Near Year-End
  13. Reversal Entries
  14. Month-End Reversals
  15. Provision → Reversal (March → April)
  16. Revenue Spike Near Year-End
  17. Manual Entries to Revenue Accounts
  18. AR / Customer Entry Concentration in Q4

DuckDB is used for row-by-row apply() and groupby bottlenecks.
"""
from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import json
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from utils.column_mapper import get_safe_series, get_safe_numeric, safe_str
from utils.doc_type_classifier import get_types_for_category
from utils.duckdb_helper import slim_df, duck_con, fetch_idx

_SCORES = {"Low": 1, "Medium": 3, "High": 5, "Critical": 8}

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "exception_rules.json")
try:
    with open(_CONFIG_PATH) as _f:
        _CFG = json.load(_f)
    _HOLIDAYS = set(pd.to_datetime(_CFG.get("holidays_fy2526", [])).date.tolist())
except Exception:
    _HOLIDAYS = set()

_REVENUE_KW = ["revenue", "sales", "turnover", "income", "sale", "billing"]
_EXPENSE_KW = ["expense", "cost", "charge", "accrual", "provision", "depreciation"]


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


def _last_n_month_days(d, n=5) -> bool:
    try:
        dt = pd.to_datetime(d)
        last_day = (dt.replace(day=1) + pd.offsets.MonthEnd(0)).day
        return dt.day >= (last_day - n + 1)
    except Exception:
        return False


def _build_holidays(params: dict) -> dict:
    """Return {date: name} dict merged from built-in + custom holidays."""
    _custom_hl_raw = params.get("custom_holidays", {})
    _custom: dict = {}
    if isinstance(_custom_hl_raw, dict):
        for _d, _n in _custom_hl_raw.items():
            try:
                _custom[pd.to_datetime(_d).date()] = str(_n).strip() if _n else ""
            except Exception:
                pass
    else:
        for _d in _custom_hl_raw:
            try:
                _custom[pd.to_datetime(_d).date()] = ""
            except Exception:
                pass
    result = {d: "" for d in _HOLIDAYS}
    result.update(_custom)
    return result


def _build_rev_exp_masks(chunk, col_map, params):
    """Return (revenue_mask, expense_mask) boolean Series for a chunk."""
    _REV_GROUPS = ["revenue from operations", "revenue", "other income",
                   "income from operations", "sales"]
    _EXP_GROUPS = ["expenses", "cost of", "depreciation", "provisions",
                   "other expenses", "finance cost"]
    grouping_s  = safe_str(get_safe_series(chunk, "grouping",     col_map))
    sub_grp_s   = safe_str(get_safe_series(chunk, "sub_grouping", col_map))
    gl_desc_s   = safe_str(get_safe_series(chunk, "gl_desc",      col_map))

    # _grouping_mapped precomputed from full df by app.py; fall back to local check
    _grouping_mapped = params.get("_grouping_mapped", grouping_s.str.len().max() > 0)
    if _grouping_mapped:
        _rev_pat = "|".join(_REV_GROUPS)
        _exp_pat = "|".join(_EXP_GROUPS)
        revenue_mask = grouping_s.str.contains(_rev_pat, na=False, regex=True)
        expense_mask = grouping_s.str.contains(_exp_pat, na=False, regex=True)
        if sub_grp_s.str.len().max() > 0:
            revenue_mask = revenue_mask | sub_grp_s.str.contains(_rev_pat, na=False, regex=True)
            expense_mask = expense_mask | sub_grp_s.str.contains(_exp_pat, na=False, regex=True)
    else:
        revenue_mask = gl_desc_s.str.contains("|".join(_REVENUE_KW), na=False, regex=True)
        expense_mask = gl_desc_s.str.contains("|".join(_EXPENSE_KW), na=False, regex=True)
    return revenue_mask, expense_mask


def run_timing_rowlevel(chunk: pd.DataFrame, col_map: dict, params: dict) -> list:
    """Row-level timing tests — safe to call per chunk (each row is independent).
    Tests: 7 Weekend/Holiday, 8 Late-Month-End, 8b Year-End-Cutoff,
           9 System-Lock, Entry-Date-Gap, Backdated, Expense-Suppression,
           Reversal flags (3 types), 16 Revenue-Spike, 17 Manual-to-Revenue.
    """
    exceptions: list = []

    posting_date = pd.to_datetime(get_safe_series(chunk, "posting_date", col_map), errors="coerce")
    doc_date     = pd.to_datetime(get_safe_series(chunk, "doc_date",     col_map), errors="coerce")
    entry_date   = pd.to_datetime(get_safe_series(chunk, "entry_date",   col_map), errors="coerce")
    if entry_date.isna().all():
        entry_date = posting_date.copy()

    narr      = (safe_str(get_safe_series(chunk, "text",               col_map)) + " " +
                 safe_str(get_safe_series(chunk, "gl_desc",            col_map)))
    reversal  = safe_str(get_safe_series(chunk, "reversal_indicator",  col_map))
    dc        = safe_str(get_safe_series(chunk, "dc_indicator",        col_map))
    doc_type  = safe_str(get_safe_series(chunk, "doc_type",            col_map))
    abs_amt   = get_safe_numeric(chunk, "amount", col_map).abs()

    year_end_raw = params.get("year_end_date") or params.get("end_date")
    sys_lock_raw = params.get("system_lock_date")
    spike_days   = int(params.get("revenue_spike_days", 5))
    _dt_map      = params.get("doc_type_map", {})
    manual_types = (
        get_types_for_category(_dt_map, "Manual")
        or [t.lower() for t in ["sa", "mn", "ab", "zz", "xx", "je", "mj", "g1"]]
    )

    try:
        yed = pd.Timestamp(year_end_raw) if year_end_raw else None
    except Exception:
        yed = None

    revenue_mask, expense_mask = _build_rev_exp_masks(chunk, col_map, params)
    _rev_src = "Grouping" if params.get("_grouping_mapped") else "GL Description"

    # ── 7. Weekend / Holiday Postings ────────────────────────────────────────
    _all_holidays = _build_holidays(params)
    _holiday_set  = set(_all_holidays.keys())
    if entry_date.notna().any():
        _entry_d  = entry_date.dt.date          # Series of date objects
        _is_sun   = entry_date.dt.dayofweek == 6
        # Vectorised holiday check via string isin (avoids Python-loop apply)
        _entry_str = _entry_d.astype(str)
        _hol_str   = {str(d) for d in _holiday_set}
        _is_hol    = _entry_str.isin(_hol_str) & entry_date.notna()
        _wh_mask   = entry_date.notna() & (_is_sun | _is_hol)
        for _idx in chunk[_wh_mask].index:
            _d = _entry_d.at[_idx]
            _reason = (
                _all_holidays.get(_d) or "Public Holiday"
                if str(_d) in _hol_str else "Sunday"
            )
            exceptions.append({
                "original_index":     _idx,
                "Exception Type":     "Sunday / Holiday Entry",
                "Exception Category": "Timing-Based Anomalies",
                "Risk Level":         "Medium",
                "Risk Score":         _SCORES["Medium"],
                "Detail":             f"Entry posted on {_reason} — requires business justification",
            })

    # ── 8. Quarter-End / Year-End Adjustments ────────────────────────────────
    # March (year-end) is highest risk. Jun/Sep/Dec quarter-ends are medium.
    # Routine month-ends (Jan, Feb, Apr, May, Jul, Aug, Oct, Nov) are excluded —
    # too much noise, no meaningful audit signal.
    _is_last5   = posting_date.notna() & posting_date.apply(
        lambda d: _last_n_month_days(d, 5) if pd.notna(d) else False
    )
    _post_month = posting_date.dt.month
    _march_mask = _is_last5 & (_post_month == 3)
    _qend_mask  = _is_last5 & (_post_month.isin([6, 9, 12]))
    _late_mask  = _march_mask | _qend_mask  # combined — used below for reversal check

    if _march_mask.any():
        exceptions.extend(_exc(
            chunk[_march_mask].index.tolist(),
            "Year-End Adjustment (March)", "Timing-Based Anomalies", "High",
            "Posted in last 5 days of March (financial year-end) — highest risk for "
            "cutoff manipulation, provision stuffing, and earnings management",
        ))
    if _qend_mask.any():
        exceptions.extend(_exc(
            chunk[_qend_mask].index.tolist(),
            "Quarter-End Adjustment (Jun/Sep/Dec)", "Timing-Based Anomalies", "Medium",
            "Posted in last 5 days of a quarter-end month — quarter-close earnings management risk",
        ))

    # ── 8b. Year-End Cutoff ───────────────────────────────────────────────────
    if yed is not None:
        ye_mask = (posting_date >= yed - timedelta(days=spike_days)) & (posting_date <= yed)
        exceptions.extend(_exc(
            chunk[ye_mask].index.tolist(),
            "Entry Near Year-End Cutoff", "Timing-Based Anomalies", "High",
            f"Posted within {spike_days} days before year-end {year_end_raw}",
        ))

    # ── 9. Entries Near System Lock ───────────────────────────────────────────
    if sys_lock_raw:
        try:
            lock_dt = pd.Timestamp(sys_lock_raw)
            mask = (posting_date >= lock_dt - timedelta(days=3)) & (posting_date <= lock_dt)
            exceptions.extend(_exc(
                chunk[mask].index.tolist(),
                "Entry Near System Lock Date", "Timing-Based Anomalies", "High",
                f"Posted within 3 days of system lock on {sys_lock_raw}",
            ))
        except Exception:
            pass

    # ── Entry Date vs Document Date Gap (per-row) ─────────────────────────────
    if doc_date.notna().any() and entry_date.notna().any():
        _gap = (entry_date - doc_date).dt.days
        # Late entries > 45 days
        _late_rows = chunk[_gap > 45].index
        for _idx in _late_rows:
            exceptions.append({
                "original_index":     _idx,
                "Exception Type":     "Late Entry (Entry Date > 45 Days After Document Date)",
                "Exception Category": "Timing-Based Anomalies",
                "Risk Level":         "Medium",
                "Risk Score":         _SCORES["Medium"],
                "Detail":             f"Entry was recorded {int(_gap.at[_idx])} days after the document date",
            })
        # Backdated entries
        exceptions.extend(_exc(
            chunk[_gap < 0].index.tolist(),
            "Backdated Entry (Entry Date Before Document Date)",
            "Timing-Based Anomalies", "High",
            "Entry date precedes document date — possible backdating",
        ))

    # ── Expense Suppression Near Year-End ─────────────────────────────────────
    if yed is not None:
        mask = (
            (posting_date >= yed - timedelta(days=30)) & (posting_date <= yed) &
            dc.isin(["c", "credit", "h", "k"]) & expense_mask
        )
        exceptions.extend(_exc(
            chunk[mask].index.tolist(),
            "Expense Suppression Near Year-End", "Timing-Based Anomalies", "High",
            "Credit entry to an expense GL in last 30 days of FY — possible cost suppression",
        ))

    # ── Reversal entries (3 types) ────────────────────────────────────────────
    rev_flag = reversal.isin(["x", "1", "true", "yes", "r", "reversed", "y"])
    exceptions.extend(_exc(
        chunk[rev_flag].index.tolist(),
        "Reversal Entry Detected", "Reversal & Provision Patterns", "Medium",
        "Reversal indicator is set — verify economic substance of original entry",
    ))
    exceptions.extend(_exc(
        chunk[rev_flag & _late_mask].index.tolist(),
        "Reversal at Quarter/Year-End", "Reversal & Provision Patterns", "High",
        "Reversal posted in last 5 days of a quarter-end or year-end month (Mar/Jun/Sep/Dec)",
    ))

    # ── 16. Revenue Spike Near Year-End ──────────────────────────────────────
    if yed is not None:
        rev_spike = (
            (posting_date >= yed - timedelta(days=spike_days)) &
            (posting_date <= yed) & revenue_mask
        )
        exceptions.extend(_exc(
            chunk[rev_spike].index.tolist(),
            "Revenue Spike Near Year-End", "Revenue & Cutoff Risks", "High",
            f"Revenue entry (via {_rev_src}) in last {spike_days} days before year-end — cutoff risk",
        ))

    # ── 17. Manual Entries to Revenue Accounts ────────────────────────────────
    exceptions.extend(_exc(
        chunk[doc_type.isin(manual_types) & revenue_mask].index.tolist(),
        "Manual Entry to Revenue Account", "Revenue & Cutoff Risks", "Critical",
        f"Manual journal to a revenue account (via {_rev_src}) — high fraud / window-dressing risk",
    ))

    return exceptions


def run_timing_aggregation(df: pd.DataFrame, col_map: dict, params: dict) -> list:
    """Aggregation timing tests — must see the full dataset.
    Tests: 10 Burst-Posting, Unusual-Time-Gap,
           15 Provision-March→April, 18 AR-Q4-Concentration.
    """
    exceptions: list = []

    posting_date = pd.to_datetime(get_safe_series(df, "posting_date", col_map), errors="coerce")
    entry_date   = pd.to_datetime(get_safe_series(df, "entry_date",   col_map), errors="coerce")
    if entry_date.isna().all():
        entry_date = posting_date.copy()
    narr     = (safe_str(get_safe_series(df, "text",    col_map)) + " " +
                safe_str(get_safe_series(df, "gl_desc", col_map)))
    reversal = safe_str(get_safe_series(df, "reversal_indicator", col_map))
    vendor   = safe_str(get_safe_series(df, "vendor_name",        col_map))
    gl_acct  = safe_str(get_safe_series(df, "gl_account",         col_map))
    customer = safe_str(get_safe_series(df, "customer",           col_map))
    user_id  = safe_str(get_safe_series(df, "user_id",            col_map))

    burst_thresh = int(params.get("burst_posting_threshold", 30))
    spike_days   = int(params.get("revenue_spike_days", 5))
    year_end_raw = params.get("year_end_date") or params.get("end_date")
    try:
        yed = pd.Timestamp(year_end_raw) if year_end_raw else None
    except Exception:
        yed = None

    revenue_mask, _ = _build_rev_exp_masks(df, col_map, params)

    # ── 10. Burst Posting ─────────────────────────────────────────────────────
    if user_id.str.len().max() > 0 and posting_date.notna().any():
        _slim = slim_df(df, col_map, ["posting_date", "user_id"])
        _slim["_user_id"]      = user_id.values
        _slim["_posting_date"] = posting_date.values
        _con = duck_con(_slim)
        _burst_idx = fetch_idx(_con, f"""
            SELECT je._orig_idx
            FROM je
            WHERE _user_id IS NOT NULL
              AND CAST(_user_id AS VARCHAR) NOT IN ('', 'nan')
              AND _posting_date IS NOT NULL
              AND (_user_id, DATE_TRUNC('hour', _posting_date)) IN (
                  SELECT _user_id, DATE_TRUNC('hour', _posting_date)
                  FROM je
                  WHERE _user_id IS NOT NULL
                    AND CAST(_user_id AS VARCHAR) NOT IN ('', 'nan')
                    AND _posting_date IS NOT NULL
                  GROUP BY _user_id, DATE_TRUNC('hour', _posting_date)
                  HAVING COUNT(*) >= {burst_thresh}
              )
        """)
        exceptions.extend(_exc(
            list(set(_burst_idx)),
            "Burst Posting Pattern", "Timing-Based Anomalies", "High",
            f"User posted ≥{burst_thresh} entries in a single hour — automated or unusual activity",
        ))

    # ── Unusual Time Gap Between Entries ─────────────────────────────────────
    if posting_date.notna().sum() > 10:
        sorted_pd = posting_date.dropna().sort_values()
        gaps      = sorted_pd.diff().dt.days.fillna(0)
        gap_thr   = max(gaps.quantile(0.95), 30)
        gap_idx   = gaps[gaps >= gap_thr].index.tolist()
        exceptions.extend(_exc(
            gap_idx, "Unusual Time Gap Between Entries",
            "Timing-Based Anomalies", "Low",
            f"Entry follows a gap of ≥{gap_thr:.0f} days — posting after an unusually long pause",
        ))

    # ── 15. Provision in March → Reversed in April ────────────────────────────
    rev_flag  = reversal.isin(["x", "1", "true", "yes", "r", "reversed", "y"])
    prov_mask = (posting_date.dt.month == 3) & narr.str.contains(r"provision|accrual|prov", na=False)
    apr_rev   = (posting_date.dt.month == 4) & rev_flag
    p_vendors = set(vendor[prov_mask].tolist())
    p_gls     = set(gl_acct[prov_mask].tolist())
    apr_match = df[apr_rev & (vendor.isin(p_vendors) | gl_acct.isin(p_gls))]
    exceptions.extend(_exc(
        df[prov_mask].index.tolist(),
        "Provision Created at Year-End (March)", "Reversal & Provision Patterns", "Medium",
        "Provision/accrual created in March — verify economic substance vs window-dressing",
    ))
    exceptions.extend(_exc(
        apr_match.index.tolist(),
        "March Provision Reversed in April", "Reversal & Provision Patterns", "High",
        "Probable reversal of a March-year-end provision — income smoothing risk",
    ))

    # ── 18. AR / Customer Entry Concentration in Q4 ───────────────────────────
    cust_entries = df[customer.str.len() > 0]
    if len(cust_entries) > 10:
        q4_months = [1, 2, 3] if (yed is None or yed.month == 3) else []
        if q4_months:
            cust_q4 = cust_entries[posting_date[cust_entries.index].dt.month.isin(q4_months)]
            if len(cust_q4) / len(cust_entries) > 0.45:
                exceptions.extend(_exc(
                    cust_q4.index.tolist(),
                    "AR / Customer Entries Concentrated in Q4", "Revenue & Cutoff Risks", "High",
                    ">45% of customer entries fall in Q4 (Jan–Mar) — possible cutoff manipulation",
                ))

    return exceptions


def run_timing_tests(df: pd.DataFrame, col_map: dict, params: dict) -> list:
    """Backward-compatible wrapper: row-level + aggregation on the full df."""
    return run_timing_rowlevel(df, col_map, params) + run_timing_aggregation(df, col_map, params)
