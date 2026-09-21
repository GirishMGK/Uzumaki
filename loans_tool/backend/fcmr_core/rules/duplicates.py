"""Duplicate detection rules using DuckDB for cross-row self-joins.

Checks (all deterministic, no fuzzy matching):
  1. Shared PAN across different customer_ids (flagged unless same UCID + different LANs)
  2. Shared Aadhaar hash across different customer_ids (flagged unless same UCID + different LANs)
  3. Shared mobile number across different customer_ids (flagged unless same UCID + different LANs)
  4. Shared bank account number across different customer_ids (flagged unless same UCID + different LANs)
  5. Shared Voter ID across different customer_ids (flagged unless same UCID + different LANs)
  6. Address fuzzy match (token-set Jaccard â‰¥0.85) across different customer_ids
  7. Exact name+DOB match (normalised) across different customer_ids

UCID scoping: A duplicate within the same UCID is OK only if rows have distinct LANs.
"""

from __future__ import annotations

import hashlib

import duckdb
import polars as pl

from fcmr_core.config import apply_duckdb_limits, settings
from fcmr_core.rules.registry import register


def _col_or_empty(df: pl.DataFrame, col: str) -> pl.Series:
    if col in df.columns:
        return df[col].cast(pl.Utf8, strict=False).fill_null("")
    return pl.Series(col, [""] * len(df), dtype=pl.Utf8)


def _ensure_row_id(df: pl.DataFrame) -> pl.DataFrame:
    """Add a synthetic _row_num column if neither customer_id nor _row_num exists."""
    if "customer_id" not in df.columns and "_row_num" not in df.columns:
        df = df.with_columns(pl.Series("_row_num", [str(i) for i in range(len(df))]))
    return df


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


def _hash_aadhaar(raw: str) -> str:
    """One-way hash of Aadhaar for dedup; salt prevents rainbow-table attacks."""
    val = (raw or "").strip().replace(" ", "").replace("-", "")
    if not val or len(val) != 12:
        return ""
    salted = settings.aadhaar_hash_salt + val
    return hashlib.sha256(salted.encode()).hexdigest()


def _normalize_address(addr: str) -> str:
    """Normalize address for fuzzy comparison."""
    return (addr or "").strip().upper()


def _address_similarity(a1: str, a2: str) -> float:
    """Token-set Jaccard similarity for addresses. Returns [0.0, 1.0]."""
    if not a1 or not a2:
        return 0.0
    t1 = set(_normalize_address(a1).split())
    t2 = set(_normalize_address(a2).split())
    if not t1 or not t2:
        return 0.0
    intersection = len(t1 & t2)
    union = len(t1 | t2)
    return intersection / union if union > 0 else 0.0


def _find_duplicates_duckdb(
    df: pl.DataFrame, key_col: str, id_col: str, ucid_col: str = None, lan_col: str = None
) -> dict[str, list[tuple[str, str, str]]]:
    """Find duplicates with optional UCID + LAN context.

    Returns {key_value: [(id, ucid, lan), ...]} for keys appearing > once.
    If ucid_col is None, returns {key_value: [(id, "", ""), ...]}.
    """
    with duckdb.connect() as con:
        apply_duckdb_limits(con)
        con.register("tbl", df.to_arrow())
        if ucid_col and lan_col:
            query = f"""
                SELECT a.{key_col}::VARCHAR, a.{id_col}::VARCHAR, a.{ucid_col}::VARCHAR, a.{lan_col}::VARCHAR
                FROM tbl a
                WHERE a.{key_col} IS NOT NULL AND a.{key_col}::VARCHAR <> ''
                  AND EXISTS (
                      SELECT 1 FROM tbl b
                      WHERE b.{key_col}::VARCHAR = a.{key_col}::VARCHAR
                        AND b.{id_col}::VARCHAR <> a.{id_col}::VARCHAR
                        AND b.{id_col} IS NOT NULL AND b.{id_col}::VARCHAR <> ''
                  )
                ORDER BY a.{key_col}::VARCHAR
            """
        else:
            query = f"""
                SELECT a.{key_col}::VARCHAR, a.{id_col}::VARCHAR
                FROM tbl a
                WHERE a.{key_col} IS NOT NULL AND a.{key_col}::VARCHAR <> ''
                  AND EXISTS (
                      SELECT 1 FROM tbl b
                      WHERE b.{key_col}::VARCHAR = a.{key_col}::VARCHAR
                        AND b.{id_col}::VARCHAR <> a.{id_col}::VARCHAR
                        AND b.{id_col} IS NOT NULL AND b.{id_col}::VARCHAR <> ''
                  )
                ORDER BY a.{key_col}::VARCHAR
            """
        rows = con.execute(query).fetchall()

    result: dict[str, list[tuple[str, str, str]]] = {}
    for row in rows:
        key = str(row[0])
        cid = str(row[1])
        ucid = str(row[2]) if len(row) > 2 else ""
        lan = str(row[3]) if len(row) > 3 else ""
        result.setdefault(key, []).append((cid, ucid, lan))
    return result


def _is_allowed_duplicate(cid: str, key: str, dupes: dict, ucid: str = "", lan: str = "") -> bool:
    """Check if this duplicate is allowed (same UCID + different LANs)."""
    if not ucid or ucid not in [d[1] for d in dupes.get(key, [])]:
        # Different UCID or no UCID info -> flag it
        return False
    # Same UCID: check if all duplicates have different LANs
    duplicate_rows = dupes.get(key, [])
    ucid_matches = [d for d in duplicate_rows if d[1] == ucid and d[0] != cid]
    if not ucid_matches:
        return True
    # Check if all have distinct LANs
    lans = [d[2] for d in ucid_matches] + [lan]
    return len(set(lans)) == len(lans)


@register(
    "pan_duplicate",
    "Shared PAN across different customer IDs (flagged unless same UCID + different LANs)",
)
def rule_pan_duplicate(df: pl.DataFrame) -> pl.DataFrame:
    df = _ensure_row_id(df)
    work = df.with_columns(
        pl.col("pan").fill_null("").str.strip_chars().str.to_uppercase().alias("_pan_norm")
        if "pan" in df.columns
        else pl.lit("").alias("_pan_norm")
    )
    cid_col = "customer_id" if "customer_id" in df.columns else "_row_num"
    ucid_col = "ucid" if "ucid" in df.columns else None
    lan_col = "lan" if "lan" in df.columns else None

    work_select = ["_pan_norm", cid_col]
    if ucid_col:
        work_select.append(ucid_col)
    if lan_col:
        work_select.append(lan_col)

    dupes = _find_duplicates_duckdb(
        work.select(work_select), "_pan_norm", cid_col, ucid_col, lan_col
    )

    cids = _col_or_empty(df, "customer_id")
    pans = work["_pan_norm"]
    ucids = _col_or_empty(df, "ucid") if ucid_col else pl.Series("ucid", [""] * len(df))
    lans = _col_or_empty(df, "lan") if lan_col else pl.Series("lan", [""] * len(df))

    statuses, codes, descs = [], [], []
    for cid, pan, ucid, lan in zip(cids, pans, ucids, lans):
        pan = (pan or "").strip()
        if pan and pan in dupes:
            if _is_allowed_duplicate(cid, pan, dupes, ucid, lan):
                statuses.append("OK")
                codes.append("")
                descs.append("")
            else:
                others = [d[0] for d in dupes[pan] if d[0] != cid]
                statuses.append("ERROR")
                codes.append("PAN_DUPLICATE")
                descs.append(f"PAN '{pan}' is shared with customer(s): {', '.join(others[:5])}")
        else:
            statuses.append("OK")
            codes.append("")
            descs.append("")
    return _annotate(df, "pan_duplicate", statuses, codes, descs)


@register(
    "aadhaar_duplicate",
    "Shared Aadhaar (hash-based) across different customer IDs (flagged unless same UCID + different LANs)",
)
def rule_aadhaar_duplicate(df: pl.DataFrame) -> pl.DataFrame:
    aadh_series = _col_or_empty(df, "aadhaar")
    cids = _col_or_empty(df, "customer_id")
    ucids = _col_or_empty(df, "ucid") if "ucid" in df.columns else pl.Series("ucid", [""] * len(df))
    lans = _col_or_empty(df, "lan") if "lan" in df.columns else pl.Series("lan", [""] * len(df))

    hashes = [_hash_aadhaar(a) for a in aadh_series]
    work_data = {"customer_id": cids, "_aadhaar_hash": hashes}
    if "ucid" in df.columns:
        work_data["ucid"] = ucids
    if "lan" in df.columns:
        work_data["lan"] = lans

    work = pl.DataFrame(work_data)
    dupes = _find_duplicates_duckdb(
        work,
        "_aadhaar_hash",
        "customer_id",
        "ucid" if "ucid" in df.columns else None,
        "lan" if "lan" in df.columns else None,
    )

    statuses, codes, descs = [], [], []
    for cid, h, ucid, lan in zip(cids, hashes, ucids, lans):
        if h and h in dupes:
            if _is_allowed_duplicate(cid, h, dupes, ucid, lan):
                statuses.append("OK")
                codes.append("")
                descs.append("")
            else:
                others = [d[0] for d in dupes[h] if d[0] != cid]
                statuses.append("ERROR")
                codes.append("AADHAAR_DUPLICATE")
                descs.append(
                    f"Aadhaar (masked) is shared with customer(s): {', '.join(others[:5])}"
                )
        else:
            statuses.append("OK")
            codes.append("")
            descs.append("")
    return _annotate(df, "aadhaar_duplicate", statuses, codes, descs)


@register(
    "mobile_duplicate",
    "Shared mobile number across different customer IDs (flagged unless same UCID + different LANs)",
)
def rule_mobile_duplicate(df: pl.DataFrame) -> pl.DataFrame:
    mobiles = _col_or_empty(df, "mobile")
    cids = _col_or_empty(df, "customer_id")
    ucids = _col_or_empty(df, "ucid") if "ucid" in df.columns else pl.Series("ucid", [""] * len(df))
    lans = _col_or_empty(df, "lan") if "lan" in df.columns else pl.Series("lan", [""] * len(df))

    norm_mobiles = pl.Series([(m or "").strip().replace(" ", "").replace("-", "") for m in mobiles])

    work_data = {"customer_id": cids, "_mobile_norm": norm_mobiles}
    if "ucid" in df.columns:
        work_data["ucid"] = ucids
    if "lan" in df.columns:
        work_data["lan"] = lans

    work = pl.DataFrame(work_data)
    dupes = _find_duplicates_duckdb(
        work,
        "_mobile_norm",
        "customer_id",
        "ucid" if "ucid" in df.columns else None,
        "lan" if "lan" in df.columns else None,
    )

    statuses, codes, descs = [], [], []
    for cid, mob, ucid, lan in zip(cids, norm_mobiles, ucids, lans):
        if mob and mob in dupes:
            if _is_allowed_duplicate(cid, mob, dupes, ucid, lan):
                statuses.append("OK")
                codes.append("")
                descs.append("")
            else:
                others = [d[0] for d in dupes[mob] if d[0] != cid]
                statuses.append("ERROR")
                codes.append("MOBILE_DUPLICATE")
                descs.append(f"Mobile '{mob}' is shared with customer(s): {', '.join(others[:5])}")
        else:
            statuses.append("OK")
            codes.append("")
            descs.append("")
    return _annotate(df, "mobile_duplicate", statuses, codes, descs)


@register(
    "bank_account_duplicate",
    "Shared bank account number across different customer IDs (flagged unless same UCID + different LANs)",
)
def rule_bank_account_duplicate(df: pl.DataFrame) -> pl.DataFrame:
    accts = _col_or_empty(df, "bank_account")
    cids = _col_or_empty(df, "customer_id")
    ucids = _col_or_empty(df, "ucid") if "ucid" in df.columns else pl.Series("ucid", [""] * len(df))
    lans = _col_or_empty(df, "lan") if "lan" in df.columns else pl.Series("lan", [""] * len(df))

    work_data = {"customer_id": cids, "_acct": accts}
    if "ucid" in df.columns:
        work_data["ucid"] = ucids
    if "lan" in df.columns:
        work_data["lan"] = lans

    work = pl.DataFrame(work_data)
    dupes = _find_duplicates_duckdb(
        work,
        "_acct",
        "customer_id",
        "ucid" if "ucid" in df.columns else None,
        "lan" if "lan" in df.columns else None,
    )

    statuses, codes, descs = [], [], []
    for cid, acct, ucid, lan in zip(cids, accts, ucids, lans):
        acct = (acct or "").strip()
        if acct and acct in dupes:
            if _is_allowed_duplicate(cid, acct, dupes, ucid, lan):
                statuses.append("OK")
                codes.append("")
                descs.append("")
            else:
                others = [d[0] for d in dupes[acct] if d[0] != cid]
                statuses.append("ERROR")
                codes.append("BANK_ACCOUNT_DUPLICATE")
                descs.append(
                    f"Bank account '{acct}' is shared with customer(s): {', '.join(others[:5])}"
                )
        else:
            statuses.append("OK")
            codes.append("")
            descs.append("")
    return _annotate(df, "bank_account_duplicate", statuses, codes, descs)


@register(
    "name_dob_duplicate",
    "Exact name+DOB duplicate (normalised) across different customer IDs (flagged unless same UCID + different LANs)",
)
def rule_name_dob_duplicate(df: pl.DataFrame) -> pl.DataFrame:
    names = _col_or_empty(df, "full_name")
    dobs = _col_or_empty(df, "dob")
    cids = _col_or_empty(df, "customer_id")
    ucids = _col_or_empty(df, "ucid") if "ucid" in df.columns else pl.Series("ucid", [""] * len(df))
    lans = _col_or_empty(df, "lan") if "lan" in df.columns else pl.Series("lan", [""] * len(df))

    keys = [
        (
            ((n or "").strip().upper() + "|" + (d or "").strip())
            if ((n or "").strip() and (d or "").strip())
            else ""
        )
        for n, d in zip(names, dobs)
    ]

    work_data = {"customer_id": cids, "_name_dob": keys}
    if "ucid" in df.columns:
        work_data["ucid"] = ucids
    if "lan" in df.columns:
        work_data["lan"] = lans

    work = pl.DataFrame(work_data)
    dupes = _find_duplicates_duckdb(
        work,
        "_name_dob",
        "customer_id",
        "ucid" if "ucid" in df.columns else None,
        "lan" if "lan" in df.columns else None,
    )

    statuses, codes, descs = [], [], []
    for cid, key, ucid, lan in zip(cids, keys, ucids, lans):
        if key and key in dupes:
            if _is_allowed_duplicate(cid, key, dupes, ucid, lan):
                statuses.append("OK")
                codes.append("")
                descs.append("")
            else:
                others = [d[0] for d in dupes[key] if d[0] != cid]
                name, dob = key.split("|", 1)
                statuses.append("ERROR")
                codes.append("NAME_DOB_DUPLICATE")
                descs.append(
                    f"Name+DOB combination '{name} / {dob}' matches customer(s): {', '.join(others[:5])}"
                )
        else:
            statuses.append("OK")
            codes.append("")
            descs.append("")
    return _annotate(df, "name_dob_duplicate", statuses, codes, descs)


@register(
    "voter_id_duplicate",
    "Shared Voter ID across different customer IDs (flagged unless same UCID + different LANs)",
)
def rule_voter_id_duplicate(df: pl.DataFrame) -> pl.DataFrame:
    vids = _col_or_empty(df, "voter_id")
    cids = _col_or_empty(df, "customer_id")
    ucids = _col_or_empty(df, "ucid") if "ucid" in df.columns else pl.Series("ucid", [""] * len(df))
    lans = _col_or_empty(df, "lan") if "lan" in df.columns else pl.Series("lan", [""] * len(df))

    norm_vids = pl.Series([(v or "").strip().upper() for v in vids])

    work_data = {"customer_id": cids, "_voter_id": norm_vids}
    if "ucid" in df.columns:
        work_data["ucid"] = ucids
    if "lan" in df.columns:
        work_data["lan"] = lans

    work = pl.DataFrame(work_data)
    dupes = _find_duplicates_duckdb(
        work,
        "_voter_id",
        "customer_id",
        "ucid" if "ucid" in df.columns else None,
        "lan" if "lan" in df.columns else None,
    )

    statuses, codes, descs = [], [], []
    for cid, vid, ucid, lan in zip(cids, norm_vids, ucids, lans):
        if vid and vid in dupes:
            if _is_allowed_duplicate(cid, vid, dupes, ucid, lan):
                statuses.append("OK")
                codes.append("")
                descs.append("")
            else:
                others = [d[0] for d in dupes[vid] if d[0] != cid]
                statuses.append("ERROR")
                codes.append("VOTER_ID_DUPLICATE")
                descs.append(
                    f"Voter ID '{vid}' is shared with customer(s): {', '.join(others[:5])}"
                )
        else:
            statuses.append("OK")
            codes.append("")
            descs.append("")
    return _annotate(df, "voter_id_duplicate", statuses, codes, descs)


@register(
    "address_duplicate",
    "Fuzzy address match (token-set Jaccard ≥0.85) across different customer IDs",
)
def rule_address_duplicate(df: pl.DataFrame) -> pl.DataFrame:
    """Flag addresses that are fuzzy-similar using a token inverted-index.

    Replaces the O(n²) Python loop: builds an inverted index of 4+-char tokens,
    finds candidate pairs sharing ≥3 tokens, then computes Jaccard only for those.
    For typical address data this is O(n × avg_tokens) rather than O(n²).
    """
    addrs = _col_or_empty(df, "address_line1").to_list()
    cids = _col_or_empty(df, "customer_id").to_list()

    # Build inverted token index
    tok_idx: dict[str, list[int]] = {}
    for i, addr in enumerate(addrs):
        normalized = (addr or "").strip().upper()
        if not normalized:
            continue
        tokens = {w for w in normalized.split() if len(w) >= 4}
        for tok in tokens:
            tok_idx.setdefault(tok, []).append(i)

    # Find candidate pairs sharing ≥3 tokens; skip very common tokens
    pair_counts: dict[tuple[int, int], int] = {}
    for bucket in tok_idx.values():
        if len(bucket) > 500:
            continue
        for ii in range(len(bucket)):
            for jj in range(ii + 1, len(bucket)):
                p = (min(bucket[ii], bucket[jj]), max(bucket[ii], bucket[jj]))
                pair_counts[p] = pair_counts.get(p, 0) + 1

    # Confirm candidates with actual Jaccard similarity
    matched: set[int] = set()
    for (i, j), count in pair_counts.items():
        if count >= 3:
            if _address_similarity(addrs[i], addrs[j]) >= 0.85:
                if (cids[i] or "") != (cids[j] or ""):
                    matched.add(i)
                    matched.add(j)

    statuses = ["WARN" if i in matched else "OK" for i in range(len(addrs))]
    codes = ["ADDRESS_DUPLICATE" if i in matched else "" for i in range(len(addrs))]
    descs = [
        "Address is fuzzy-similar to another record (similarity ≥85%)" if i in matched else ""
        for i in range(len(addrs))
    ]
    return _annotate(df, "address_duplicate", statuses, codes, descs)
