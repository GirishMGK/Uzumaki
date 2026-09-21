"""Email validation rules.

Checks:
  1. Company emails with generic/free-domain providers (should use business domain)
"""

from __future__ import annotations

import polars as pl

from fcmr_core.rules.registry import register

# Common free/generic email providers
_GENERIC_DOMAINS = {
    "gmail.com",
    "yahoo.com",
    "outlook.com",
    "hotmail.com",
    "aol.com",
    "icloud.com",
    "mail.com",
    "protonmail.com",
    "yandex.com",
    "rediffmail.com",
    "dataone.in",
    "airtelmail.com",
    "bsnl.in",
}


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
    "email_company_generic_domain", "Company email using generic/free domain (e.g., gmail.com)"
)
def rule_email_company_generic_domain(df: pl.DataFrame) -> pl.DataFrame:
    emails = _col_or_empty(df, "email")

    statuses, codes, descs = [], [], []
    for email in emails:
        email = (email or "").strip().lower()
        if email and "@" in email:
            domain = email.split("@", 1)[1]
            if domain in _GENERIC_DOMAINS:
                statuses.append("WARN")
                codes.append("EMAIL_COMPANY_GENERIC_DOMAIN")
                descs.append(
                    f"Company email uses generic domain '{domain}' (consider business domain)"
                )
            else:
                statuses.append("OK")
                codes.append("")
                descs.append("")
        else:
            statuses.append("OK")
            codes.append("")
            descs.append("")

    return _annotate(df, "email_company_generic_domain", statuses, codes, descs)
