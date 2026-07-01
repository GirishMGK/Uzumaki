"""
Benford's Law Test — first-digit distribution analysis.

Uses a chi-square goodness-of-fit test to detect digit anomalies
in transaction amounts that may indicate manipulation.
"""
from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd
import numpy as np
from utils.column_mapper import get_safe_numeric

_SCORES   = {"Low": 1, "Medium": 3, "High": 5, "Critical": 8}
_CATEGORY = "Benford's Law Deviation"


def _benford_expected() -> dict:
    """Return Benford's Law expected proportion for digits 1–9."""
    return {d: np.log10(1 + 1 / d) for d in range(1, 10)}


def _first_digit(x: float) -> int:
    """Extract the leading significant digit from a positive float."""
    s = str(abs(x)).replace(".", "").lstrip("0")
    return int(s[0]) if s else 0


def run_benford_test(
    df: pd.DataFrame,
    col_map: dict,
    params: dict | None = None,
) -> tuple[list, pd.DataFrame, pd.DataFrame]:
    """
    Run Benford's Law analysis on the Amount column.

    Returns:
        exceptions : list of exception dicts
        digit_df   : per-digit statistics DataFrame
        chi2_df    : chi-square summary DataFrame (1 row per key metric)
    """
    params = params or {}
    empty  = ([], pd.DataFrame(), pd.DataFrame())

    abs_amt  = get_safe_numeric(df, "amount", col_map).abs()
    pos_amt  = abs_amt[abs_amt > 0].dropna()

    if len(pos_amt) < 100:
        return empty

    # ── First digits ──────────────────────────────────────────────────────────
    fd_series = pos_amt.apply(_first_digit)
    fd_series = fd_series[fd_series > 0]
    # Map back to full df index (0 for non-positive rows)
    all_fd = pd.Series(0, index=df.index, dtype=int)
    all_fd.update(fd_series)

    total    = len(fd_series)
    obs_cnt  = fd_series.value_counts().sort_index().reindex(range(1, 10), fill_value=0)
    obs_pct  = obs_cnt / total
    expected = _benford_expected()
    exp_pct  = pd.Series(expected)
    exp_cnt  = (exp_pct * total).round().astype(int).clip(lower=1)

    # ── Chi-square test ───────────────────────────────────────────────────────
    try:
        from scipy import stats as _stats
        chi2_stat, p_value = _stats.chisquare(
            obs_cnt.values,
            f_exp=exp_cnt.values,
        )
    except ImportError:
        # Manual chi-square if scipy not available
        chi2_stat = float(np.sum((obs_cnt.values - exp_cnt.values) ** 2 / exp_cnt.values))
        # Approximate p-value via normal approximation
        p_value = float(np.exp(-chi2_stat / 16))

    # ── Deviation threshold ───────────────────────────────────────────────────
    dev_thr = float(params.get("benford_deviation_threshold", 0.05))
    flagged_digits = [
        d for d in range(1, 10)
        if abs(obs_pct.get(d, 0) - exp_pct.get(d, 0)) > dev_thr
    ]

    # ── Digit statistics table ────────────────────────────────────────────────
    rows = []
    for d in range(1, 10):
        o_cnt = int(obs_cnt.get(d, 0))
        o_pct = float(obs_pct.get(d, 0))
        e_pct = float(exp_pct.get(d, 0))
        dev   = o_pct - e_pct
        denom = (e_pct * (1 - e_pct) / total) ** 0.5
        z     = (dev / denom) if denom > 0 else 0
        rows.append({
            "First Digit":         d,
            "Observed Count":      o_cnt,
            "Observed %":          round(o_pct * 100, 2),
            "Expected % (Benford)":round(e_pct * 100, 2),
            "Deviation %":         round(dev   * 100, 2),
            "Z-Score":             round(z, 2),
            "Flagged":             "YES" if d in flagged_digits else "No",
        })
    digit_df = pd.DataFrame(rows)

    # ── Chi-square summary ────────────────────────────────────────────────────
    chi2_df = pd.DataFrame({
        "Metric": [
            "Chi-Square Statistic",
            "P-Value",
            "Sample Size",
            "Deviation Threshold Used",
            "Flagged Digits",
            "Overall Assessment",
        ],
        "Value": [
            round(chi2_stat, 4),
            round(p_value, 6),
            total,
            f"{dev_thr * 100:.1f}%",
            ", ".join(str(d) for d in flagged_digits) if flagged_digits else "None",
            "SIGNIFICANT DEVIATION — recommend investigation" if p_value < 0.05 else "Within normal range",
        ],
    })

    # ── Build exception rows ──────────────────────────────────────────────────
    exceptions: list = []
    if flagged_digits:
        risk_level = "High" if p_value < 0.01 else "Medium"
        flagged_idx = df.index[all_fd.isin(flagged_digits) & (all_fd > 0)].tolist()
        exceptions = [
            {
                "original_index":    i,
                "Exception Type":    f"Benford Deviation — Digit {all_fd[i]}",
                "Exception Category":_CATEGORY,
                "Risk Level":        risk_level,
                "Risk Score":        _SCORES[risk_level],
                "Detail": (
                    f"Leading digit {all_fd[i]}: observed {obs_pct.get(all_fd[i], 0) * 100:.1f}% "
                    f"vs expected {exp_pct.get(all_fd[i], 0) * 100:.1f}%. "
                    f"Chi² p={p_value:.4f}. Flagged digits: {flagged_digits}"
                ),
            }
            for i in flagged_idx
        ]

    return exceptions, digit_df, chi2_df
