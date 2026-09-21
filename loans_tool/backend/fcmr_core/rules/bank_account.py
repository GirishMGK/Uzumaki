"""Bank account validation rules.

Checks:
  1. Bank account length (valid range: 9-18 digits)
"""

from __future__ import annotations

import polars as pl

from fcmr_core.rules.registry import register


def _col_or_empty(df: pl.DataFrame, col: str) -> pl.Series:
    if col in df.columns:
        return df[col].cast(pl.Utf8, strict=False).fill_null("")
    return pl.Series(col, [""] * len(df), dtype=pl.Utf8)


def _annotate(
    df: pl.DataFrame, rule_id: str, statuses: list[str], codes: list[str], descs: list[str]
) -> pl.DataFrame:
    return df.with_columns(
        [
            pl.Series(f"_exc_{rule_id}_status", statuses, dtype=pl.Utf8),
            pl.Series(f"_exc_{rule_id}_code", codes, dtype=pl.Utf8),
            pl.Series(f"_exc_{rule_id}_desc", descs, dtype=pl.Utf8),
        ]
    )


@register(
    "bank_account_invalid_length", "Bank account number outside valid length range (9-18 digits)"
)
def rule_bank_account_invalid_length(df: pl.DataFrame) -> pl.DataFrame:
    accts = _col_or_empty(df, "bank_account")

    statuses, codes, descs = [], [], []
    for acct in accts:
        acct = (acct or "").strip()
        if acct:
            # Remove spaces and dashes, keep only digits
            digits_only = "".join(c for c in acct if c.isdigit())
            length = len(digits_only)

            if length < 9 or length > 18:
                statuses.append("ERROR")
                codes.append("BANK_ACCOUNT_INVALID_LENGTH")
                descs.append(f"Bank account '{acct}' has {length} digits (valid range: 9-18)")
            else:
                statuses.append("OK")
                codes.append("")
                descs.append("")
        else:
            statuses.append("OK")
            codes.append("")
            descs.append("")

    return _annotate(df, "bank_account_invalid_length", statuses, codes, descs)
