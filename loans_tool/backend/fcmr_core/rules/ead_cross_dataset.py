"""EAD Analytics cross-dataset checks (rules 16, 17) -- unlike ead_rules.py
(single EAD DataFrame, per-row _exc_* flags) and ead_reports.py (single EAD
DataFrame, aggregate summary), these two need a SECOND, separately-uploaded
dataset (Customer Master / Technical Writeoff) to check EAD rows against.
Each function returns its own exceptions table (one row per match found),
meant to be rendered as an HTML table and offered as a CSV download --
same shape as ead_reports.py's npa_flag_date_change_report.
"""

from __future__ import annotations

import polars as pl

_STR_COLS = {
    "loan_id": pl.Utf8,
    "ucid": pl.Utf8,
    "customer_id": pl.Utf8,
    "lan": pl.Utf8,
    "disbursement_date": pl.Utf8,
    "business_date": pl.Utf8,
}


def _ensure_columns(df: pl.DataFrame, names: list[str]) -> pl.DataFrame:
    missing = [pl.lit(None, dtype=_STR_COLS[n]).alias(n) for n in names if n not in df.columns]
    return df.with_columns(missing) if missing else df


_UCID_MISMATCH_SCHEMA = {
    "loan_id": pl.Utf8,
    "ead_ucid": pl.Utf8,
    "customer_master_ucid": pl.Utf8,
    "ead_customer_id": pl.Utf8,
    "customer_master_customer_id": pl.Utf8,
}


def ucid_cross_check_report(ead_df: pl.DataFrame, customer_master_df: pl.DataFrame) -> pl.DataFrame:
    """Rule 16: EAD's UCID should match Customer Master's UCID for the same
    loan (matched via EAD's `loan_id` <-> Customer Master's `lan`). Flags
    only loans present in both datasets where both UCIDs are populated and
    disagree -- a loan Customer Master hasn't been uploaded/mapped for yet
    isn't an exception, just unmatched.
    """
    if customer_master_df.is_empty():
        return pl.DataFrame(schema=_UCID_MISMATCH_SCHEMA)

    ead = _ensure_columns(ead_df, ["loan_id", "ucid", "customer_id"])
    cm = _ensure_columns(customer_master_df, ["lan", "ucid", "customer_id"])

    cm_small = cm.select(
        [
            pl.col("lan").alias("_cm_loan_id"),
            pl.col("ucid").alias("_cm_ucid"),
            pl.col("customer_id").alias("_cm_customer_id"),
        ]
    )
    joined = ead.select(["loan_id", "ucid", "customer_id"]).join(
        cm_small, left_on="loan_id", right_on="_cm_loan_id", how="inner"
    )
    mismatches = joined.filter(
        pl.col("ucid").is_not_null() & pl.col("_cm_ucid").is_not_null() & (pl.col("ucid") != pl.col("_cm_ucid"))
    )
    return mismatches.rename(
        {
            "ucid": "ead_ucid",
            "_cm_ucid": "customer_master_ucid",
            "customer_id": "ead_customer_id",
            "_cm_customer_id": "customer_master_customer_id",
        }
    ).select(list(_UCID_MISMATCH_SCHEMA.keys())).sort("loan_id")


_WRITEOFF_MATCH_SCHEMA = {
    "loan_id": pl.Utf8,
    "customer_key": pl.Utf8,
    "disbursement_date": pl.Utf8,
    "written_off_loan_id": pl.Utf8,
    "written_off_business_date": pl.Utf8,
    "same_loan_id": pl.Boolean,
}


def written_off_customer_fresh_disbursal_report(ead_df: pl.DataFrame, writeoff_df: pl.DataFrame) -> pl.DataFrame:
    """Rule 17: fresh EAD disbursements matched against the separately
    uploaded Written-Off Accounts list, by AgreementID (loan_id) and/or
    UCID as the user specified -- flags either the exact same loan_id
    appearing in both (a written-off account still showing live EAD
    activity) or a different loan_id for the same customer (identified by
    UCID, falling back to customer_id) that's on the Written-Off list --
    i.e. a fresh loan to a previously written-off customer.
    """
    if writeoff_df.is_empty():
        return pl.DataFrame(schema=_WRITEOFF_MATCH_SCHEMA)

    ead = _ensure_columns(ead_df, ["loan_id", "ucid", "customer_id", "disbursement_date"])
    wo = _ensure_columns(writeoff_df, ["loan_id", "ucid", "customer_id", "business_date"])

    ead_keyed = ead.with_columns(pl.coalesce([pl.col("ucid"), pl.col("customer_id")]).alias("_cust_key"))
    wo_keyed = wo.with_columns(pl.coalesce([pl.col("ucid"), pl.col("customer_id")]).alias("_cust_key"))

    wo_events = wo_keyed.filter(pl.col("_cust_key").is_not_null()).select(
        [
            pl.col("_cust_key"),
            pl.col("loan_id").alias("_wo_loan_id"),
            pl.col("business_date").alias("_wo_business_date"),
        ]
    )

    joined = ead_keyed.filter(pl.col("_cust_key").is_not_null()).join(wo_events, on="_cust_key", how="inner")
    if joined.height == 0:
        return pl.DataFrame(schema=_WRITEOFF_MATCH_SCHEMA)

    flagged = joined.unique(subset=["loan_id", "_wo_loan_id"])
    return flagged.select(
        [
            pl.col("loan_id"),
            pl.col("_cust_key").alias("customer_key"),
            pl.col("disbursement_date"),
            pl.col("_wo_loan_id").alias("written_off_loan_id"),
            pl.col("_wo_business_date").alias("written_off_business_date"),
            (pl.col("loan_id") == pl.col("_wo_loan_id")).alias("same_loan_id"),
        ]
    ).sort(["customer_key", "loan_id"])
