"""Beneficiary tagging and stable internal customer ID creation.

Logic:
  - A stable internal key (fcmr_customer_key) is derived deterministically
    from the best available unique identifier: PAN > Aadhaar-hash > mobile.
    If none is available, falls back to a hash of name+DOB.
  - Customers that share a PAN, Aadhaar-hash, or mobile are grouped into
    the same beneficiary_group_id.
"""

from __future__ import annotations

import hashlib

import polars as pl

from fcmr_core.config import settings
from fcmr_core.rules.registry import register


def _col_or_empty(df: pl.DataFrame, col: str) -> pl.Series:
    if col in df.columns:
        return df[col].cast(pl.Utf8, strict=False).fill_null("")
    return pl.Series(col, [""] * len(df), dtype=pl.Utf8)


def _sha8(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def _hash_aadhaar(raw: str) -> str:
    val = (raw or "").strip().replace(" ", "").replace("-", "")
    if not val or len(val) != 12:
        return ""
    return hashlib.sha256((settings.aadhaar_hash_salt + val).encode()).hexdigest()


@register("beneficiary_tagging", "Assign stable fcmr_customer_key and beneficiary_group_id")
def rule_beneficiary_tagging(df: pl.DataFrame) -> pl.DataFrame:
    pans = _col_or_empty(df, "pan")
    aadhaar_raws = _col_or_empty(df, "aadhaar")
    mobiles = _col_or_empty(df, "mobile")
    names = _col_or_empty(df, "full_name")
    dobs = _col_or_empty(df, "dob")

    customer_keys: list[str] = []
    group_keys: list[str] = []  # canonical linkage key before grouping

    for pan, aadh_raw, mob, name, dob in zip(pans, aadhaar_raws, mobiles, names, dobs):
        pan_norm = (pan or "").strip().upper()
        aadh_hash = _hash_aadhaar(aadh_raw)
        mob_norm = (mob or "").strip().replace(" ", "").replace("-", "")
        name_dob = (
            ((name or "").strip().upper() + "|" + (dob or "").strip())
            if (name or "").strip() and (dob or "").strip()
            else ""
        )

        # Stable customer key: best available identifier (priority: PAN > Aadhaar > mobile > name+DOB)
        if pan_norm and len(pan_norm) == 10:
            ck = "PAN:" + _sha8(pan_norm)
            gk = "PAN:" + pan_norm
        elif aadh_hash:
            ck = "ADH:" + _sha8(aadh_hash)
            gk = "ADH:" + aadh_hash
        elif mob_norm and len(mob_norm) == 10:
            ck = "MOB:" + _sha8(mob_norm)
            gk = "MOB:" + mob_norm
        elif name_dob:
            ck = "NDB:" + _sha8(name_dob)
            gk = "NDB:" + _sha8(name_dob)
        else:
            ck = "UNK:" + _sha8(str(id(pan)) + str(id(mob)))
            gk = ck

        customer_keys.append(ck)
        group_keys.append(gk)

    # Map each unique group_key to a short group ID (stable across runs because hash-based)
    gk_to_gid: dict[str, str] = {}
    for gk in group_keys:
        if gk not in gk_to_gid:
            gk_to_gid[gk] = "GRP-" + hashlib.sha256(gk.encode()).hexdigest()[:8].upper()

    group_ids = [gk_to_gid[gk] for gk in group_keys]

    return df.with_columns(
        [
            pl.Series("_exc_beneficiary_tagging_status", ["OK"] * len(df), dtype=pl.Utf8),
            pl.Series("_exc_beneficiary_tagging_code", [""] * len(df), dtype=pl.Utf8),
            pl.Series("_exc_beneficiary_tagging_desc", [""] * len(df), dtype=pl.Utf8),
            pl.Series("fcmr_customer_key", customer_keys, dtype=pl.Utf8),
            pl.Series("fcmr_group_id", group_ids, dtype=pl.Utf8),
        ]
    )
