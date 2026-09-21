"""KYC document format validation rules.

All logic is deterministic, hard-coded Python â€” no AI/LLM dependency.

Covered:
  PAN       â€” AAAAA9999A format + 4th-char entity type
  Aadhaar   â€” 12 digits + Verhoeff checksum; masked in output
  Voter ID  â€” EPIC format ^[A-Z]{3}[0-9]{7}$
  Passport  â€” ^[A-PR-WY][0-9]{7}$
  DL        â€” State code prefix + structural pattern
  Mobile    â€” 10-digit starting 6-9
  Email     â€” RFC-style basic format check
  DOB       â€” Valid date + age in plausible range (1 â€“ 100 years)
"""

from __future__ import annotations

import re
from datetime import date, datetime

import polars as pl

from fcmr_core.rules.registry import register

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MULT = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
    [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
    [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
    [4, 0, 1, 2, 3, 9, 5, 6, 7, 8],
    [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2],
    [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
    [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
    [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
]
_PERM = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
    [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
    [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
    [9, 4, 5, 3, 1, 2, 6, 8, 7, 0],
    [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5],
    [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
]
_INV = [0, 4, 3, 2, 1, 5, 6, 7, 8, 9]

_PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
_PAN_ENTITY_CHARS = set("PFHBCAGLJT")  # P=individual, F=firm, H=HUF, etc.
_EPIC_RE = re.compile(r"^[A-Z]{3}[0-9]{7}$")
_PASSPORT_RE = re.compile(r"^[A-PR-WY][0-9]{7}$")
_MOBILE_RE = re.compile(r"^[6-9]\d{9}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# State codes recognised by RTO for driving licences
_DL_STATE_CODES = {
    "AP",
    "AR",
    "AS",
    "BR",
    "CG",
    "GA",
    "GJ",
    "HR",
    "HP",
    "JH",
    "JK",
    "KA",
    "KL",
    "LA",
    "LD",
    "MH",
    "ML",
    "MN",
    "MP",
    "MZ",
    "NL",
    "OD",
    "PB",
    "PY",
    "RJ",
    "SK",
    "TN",
    "TS",
    "TR",
    "UK",
    "UP",
    "WB",
    "AN",
    "CH",
    "DD",
    "DL",
    "DN",
}
_DL_RE = re.compile(r"^([A-Z]{2})\d{2}\d{4}\d+$")

_DOB_FMTS = ["%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d", "%d%m%Y"]


def _verhoeff_valid(aadhaar: str) -> bool:
    if not aadhaar.isdigit() or len(aadhaar) != 12:
        return False
    if aadhaar[0] in "01":
        return False  # invalid prefix
    c = 0
    for i, d in enumerate(reversed(aadhaar)):
        c = _MULT[c][_PERM[i % 8][int(d)]]
    return c == 0


def _parse_dob(value: str) -> date | None:
    for fmt in _DOB_FMTS:
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    return None


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


def _col_or_empty(df: pl.DataFrame, col: str) -> pl.Series:
    if col in df.columns:
        return df[col].cast(pl.Utf8, strict=False).fill_null("")
    return pl.Series(col, [""] * len(df), dtype=pl.Utf8)


# ---------------------------------------------------------------------------
# PAN
# ---------------------------------------------------------------------------


@register("pan_format", "PAN number format validation (AAAAA9999A + entity type)")
def rule_pan_format(df: pl.DataFrame) -> pl.DataFrame:
    pan_series = _col_or_empty(df, "pan")
    statuses, codes, descs = [], [], []
    for pan in pan_series:
        pan = (pan or "").strip().upper()
        if not pan:
            statuses.append("WARN")
            codes.append("PAN_MISSING")
            descs.append("PAN not provided")
        elif not _PAN_RE.match(pan):
            statuses.append("ERROR")
            codes.append("PAN_INVALID_FORMAT")
            descs.append(f"PAN '{pan}' does not match AAAAA9999A pattern")
        elif pan[3] not in _PAN_ENTITY_CHARS:
            statuses.append("ERROR")
            codes.append("PAN_INVALID_ENTITY_CHAR")
            descs.append(f"PAN '{pan}' has unrecognised entity type character '{pan[3]}'")
        else:
            statuses.append("OK")
            codes.append("")
            descs.append("")
    return _annotate(df, "pan_format", statuses, codes, descs)


# ---------------------------------------------------------------------------
# Aadhaar
# ---------------------------------------------------------------------------


@register("aadhaar_format", "Aadhaar format + Verhoeff checksum; output is always masked")
def rule_aadhaar_format(df: pl.DataFrame) -> pl.DataFrame:
    aadh_series = _col_or_empty(df, "aadhaar")
    statuses, codes, descs = [], [], []
    for raw in aadh_series:
        val = (raw or "").strip().replace(" ", "").replace("-", "")
        if not val:
            statuses.append("WARN")
            codes.append("AADHAAR_MISSING")
            descs.append("Aadhaar not provided")
        elif not val.isdigit() or len(val) != 12:
            statuses.append("ERROR")
            codes.append("AADHAAR_INVALID_FORMAT")
            descs.append("Aadhaar must be exactly 12 digits")
        elif val[0] in "01":
            statuses.append("ERROR")
            codes.append("AADHAAR_INVALID_PREFIX")
            descs.append("Aadhaar cannot start with 0 or 1")
        elif not _verhoeff_valid(val):
            statuses.append("ERROR")
            codes.append("AADHAAR_CHECKSUM_FAIL")
            descs.append("Aadhaar Verhoeff checksum validation failed")
        else:
            statuses.append("OK")
            codes.append("")
            descs.append("")
    return _annotate(df, "aadhaar_format", statuses, codes, descs)


# ---------------------------------------------------------------------------
# Voter ID (EPIC)
# ---------------------------------------------------------------------------


@register("voter_id_format", "Voter ID (EPIC) format: 3 letters + 7 digits")
def rule_voter_id_format(df: pl.DataFrame) -> pl.DataFrame:
    series = _col_or_empty(df, "voter_id")
    statuses, codes, descs = [], [], []
    for val in series:
        val = (val or "").strip().upper()
        if not val:
            statuses.append("WARN")
            codes.append("VOTER_ID_MISSING")
            descs.append("Voter ID not provided")
        elif not _EPIC_RE.match(val):
            statuses.append("ERROR")
            codes.append("VOTER_ID_INVALID_FORMAT")
            descs.append(f"Voter ID '{val}' must match pattern AAA9999999 (3 letters + 7 digits)")
        else:
            statuses.append("OK")
            codes.append("")
            descs.append("")
    return _annotate(df, "voter_id_format", statuses, codes, descs)


# ---------------------------------------------------------------------------
# Passport
# ---------------------------------------------------------------------------


@register("passport_format", "Indian passport format: letter (not Q/X/Z) + 7 digits")
def rule_passport_format(df: pl.DataFrame) -> pl.DataFrame:
    series = _col_or_empty(df, "passport")
    statuses, codes, descs = [], [], []
    for val in series:
        val = (val or "").strip().upper()
        if not val:
            statuses.append("OK")
            codes.append("")
            descs.append("")  # passport is optional
        elif not _PASSPORT_RE.match(val):
            statuses.append("ERROR")
            codes.append("PASSPORT_INVALID_FORMAT")
            descs.append(f"Passport '{val}' must be 1 letter (A-PR-WY) followed by 7 digits")
        else:
            statuses.append("OK")
            codes.append("")
            descs.append("")
    return _annotate(df, "passport_format", statuses, codes, descs)


# ---------------------------------------------------------------------------
# Driving Licence
# ---------------------------------------------------------------------------


@register("dl_format", "Driving licence: state code + RTO + year + sequence")
def rule_dl_format(df: pl.DataFrame) -> pl.DataFrame:
    series = _col_or_empty(df, "driving_licence")
    statuses, codes, descs = [], [], []
    for val in series:
        val = (val or "").strip().upper().replace("-", "").replace(" ", "")
        if not val:
            statuses.append("OK")
            codes.append("")
            descs.append("")  # DL is optional
            continue
        m = _DL_RE.match(val)
        if not m:
            statuses.append("ERROR")
            codes.append("DL_INVALID_FORMAT")
            descs.append(f"DL '{val}' does not match expected pattern (SSRRYYYYNNNNNNN)")
        elif m.group(1) not in _DL_STATE_CODES:
            statuses.append("ERROR")
            codes.append("DL_INVALID_STATE_CODE")
            descs.append(f"DL '{val}' has unrecognised state code '{m.group(1)}'")
        else:
            statuses.append("OK")
            codes.append("")
            descs.append("")
    return _annotate(df, "dl_format", statuses, codes, descs)


# ---------------------------------------------------------------------------
# Mobile
# ---------------------------------------------------------------------------


@register("mobile_format", "Mobile number: 10 digits starting 6-9")
def rule_mobile_format(df: pl.DataFrame) -> pl.DataFrame:
    series = _col_or_empty(df, "mobile")
    statuses, codes, descs = [], [], []
    for val in series:
        val = (val or "").strip().replace(" ", "").replace("-", "")
        if val.startswith("+91"):
            val = val[3:]
        if not val:
            statuses.append("WARN")
            codes.append("MOBILE_MISSING")
            descs.append("Mobile number not provided")
        elif not _MOBILE_RE.match(val):
            statuses.append("ERROR")
            codes.append("MOBILE_INVALID_FORMAT")
            descs.append(f"Mobile '{val}' must be 10 digits starting with 6-9")
        else:
            statuses.append("OK")
            codes.append("")
            descs.append("")
    return _annotate(df, "mobile_format", statuses, codes, descs)


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------


@register("email_format", "Email address basic format validation")
def rule_email_format(df: pl.DataFrame) -> pl.DataFrame:
    series = _col_or_empty(df, "email")
    statuses, codes, descs = [], [], []
    for val in series:
        val = (val or "").strip()
        if not val:
            statuses.append("WARN")
            codes.append("EMAIL_MISSING")
            descs.append("Email not provided")
        elif not _EMAIL_RE.match(val):
            statuses.append("ERROR")
            codes.append("EMAIL_INVALID_FORMAT")
            descs.append(f"Email '{val}' is not a valid email address format")
        else:
            statuses.append("OK")
            codes.append("")
            descs.append("")
    return _annotate(df, "email_format", statuses, codes, descs)


# ---------------------------------------------------------------------------
# Date of Birth
# ---------------------------------------------------------------------------


@register("dob_validity", "Date of birth: valid date, age 1â€“100 years")
def rule_dob_validity(df: pl.DataFrame) -> pl.DataFrame:
    series = _col_or_empty(df, "dob")
    today = date.today()
    statuses, codes, descs = [], [], []
    for val in series:
        val = (val or "").strip()
        if not val:
            statuses.append("WARN")
            codes.append("DOB_MISSING")
            descs.append("Date of birth not provided")
            continue
        parsed = _parse_dob(val)
        if parsed is None:
            statuses.append("ERROR")
            codes.append("DOB_INVALID_FORMAT")
            descs.append(f"DOB '{val}' could not be parsed; expected YYYY-MM-DD or DD-MM-YYYY")
        elif parsed >= today:
            statuses.append("ERROR")
            codes.append("DOB_FUTURE_DATE")
            descs.append(f"DOB '{val}' is a future date")
        else:
            age = (today - parsed).days // 365
            if age > 100:
                statuses.append("ERROR")
                codes.append("DOB_AGE_IMPLAUSIBLE")
                descs.append(f"DOB '{val}' implies age {age} years, which is implausible")
            elif age < 1:
                statuses.append("ERROR")
                codes.append("DOB_AGE_TOO_YOUNG")
                descs.append(f"DOB '{val}' implies age less than 1 year")
            else:
                statuses.append("OK")
                codes.append("")
                descs.append("")
    return _annotate(df, "dob_validity", statuses, codes, descs)


@register("dob_age_range", "Age must be between 18 and 65 (inclusive)")
def rule_dob_age_range(df: pl.DataFrame) -> pl.DataFrame:
    series = _col_or_empty(df, "dob")
    today = date.today()
    statuses, codes, descs = [], [], []
    for val in series:
        val = (val or "").strip()
        if not val:
            statuses.append("OK")
            codes.append("")
            descs.append("")  # Missing DOB handled by dob_validity
            continue
        parsed = _parse_dob(val)
        if parsed is None or parsed >= today:
            statuses.append("OK")
            codes.append("")
            descs.append("")  # Invalid/future DOB handled by dob_validity
            continue
        age = (today - parsed).days // 365
        if age < 18:
            statuses.append("WARN")
            codes.append("DOB_AGE_OUT_OF_RANGE")
            descs.append(f"Age {age} years is below 18 (minimum lending age)")
        elif age > 65:
            statuses.append("WARN")
            codes.append("DOB_AGE_OUT_OF_RANGE")
            descs.append(f"Age {age} years is above 65 (standard retirement age)")
        else:
            statuses.append("OK")
            codes.append("")
            descs.append("")
    return _annotate(df, "dob_age_range", statuses, codes, descs)
