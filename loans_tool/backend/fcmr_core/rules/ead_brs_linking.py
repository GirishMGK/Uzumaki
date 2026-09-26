"""EAD <-> BRS linking (rule set built on top of rules 16/17's cross-dataset
pattern) -- reconciles EAD against a BRS Consolidator export (Disbursement
or Collection), matched by BRS's `agreement_no` against EAD's `loan_id`.

Unlike Customer Master/Technical Writeoff (rules 16/17), a BRS export
never lands in this engagement's DuckDB catalog -- BRS Consolidator is a
separate, stateless standalone tool with nothing persisted. So instead of
reading a second consolidated dataset from the catalog, the already-
consolidated BRS file (its own CSV/Excel/Parquet download, canonical
columns already in place) is uploaded directly on this check and read
in-memory for one comparison; nothing about it is stored.
"""

from __future__ import annotations

import io

import polars as pl

_STR_COLS = {
    "loan_id": pl.Utf8,
    "disbursed_amount": pl.Float64,
    "principal_paid": pl.Float64,
    "interest_paid": pl.Float64,
    "agreement_no": pl.Utf8,
    "amount": pl.Float64,
    "product": pl.Utf8,
}

# Amounts recorded to two decimal places in practice; anything past this
# is a rounding artifact, not a real reconciling difference.
_AMOUNT_TOLERANCE = 0.01


def _ensure_columns(df: pl.DataFrame, names: list[str]) -> pl.DataFrame:
    """Add any of `names` missing from `df` (typed null), and cast any that
    already exist to their expected dtype -- an all-null or empty upload
    (e.g. a BRS export with zero data rows) otherwise gets inferred as
    polars' untyped Null dtype, which then fails the join below with a
    dtype mismatch instead of simply producing zero matches."""
    exprs = [
        pl.lit(None, dtype=_STR_COLS[n]).alias(n) if n not in df.columns else pl.col(n).cast(_STR_COLS[n])
        for n in names
    ]
    return df.with_columns(exprs)


def _require_brs_columns(df: pl.DataFrame) -> None:
    """A BRS Consolidator export always carries these two columns. Their
    absence means the wrong file was uploaded here (e.g. a raw, unmapped
    export, or a Disbursement file dropped on the Collection form) -- catch
    that with a clear error instead of silently reconciling against an
    all-null join key."""
    missing = [c for c in ("agreement_no", "amount") if c not in df.columns]
    if missing:
        raise ValueError(
            "This doesn't look like a BRS Consolidator export -- missing column(s): "
            f"{', '.join(missing)}. Upload the consolidated CSV/Excel/Parquet download "
            "from BRS Consolidator, not a raw source file."
        )


def read_brs_export(filename: str, data: bytes) -> pl.DataFrame:
    """Reads a BRS Consolidator download (its own CSV/Excel/Parquet
    output -- canonical columns already in place, no mapping needed)."""
    lower = filename.lower()
    if lower.endswith(".csv"):
        return pl.read_csv(data, infer_schema_length=10000, ignore_errors=True)
    if lower.endswith((".xlsx", ".xls")):
        return pl.read_excel(io.BytesIO(data), engine="openpyxl")
    if lower.endswith(".parquet"):
        return pl.read_parquet(io.BytesIO(data))
    raise ValueError(f"{filename}: unsupported file type (expected .csv, .xlsx, .xls, or .parquet)")


def _reconcile(
    ead_df: pl.DataFrame,
    brs_df: pl.DataFrame,
    ead_amount_expr: pl.Expr,
    ead_amount_alias: str,
    brs_amount_alias: str,
) -> pl.DataFrame:
    ead_agg = ead_df.group_by("loan_id").agg(ead_amount_expr.alias(ead_amount_alias))

    brs = _ensure_columns(brs_df, ["agreement_no", "amount", "product"])
    brs_agg = brs.group_by("agreement_no").agg(
        [
            pl.col("amount").sum().alias(brs_amount_alias),
            pl.col("product").first().alias("product"),
        ]
    )

    joined = ead_agg.join(brs_agg, left_on="loan_id", right_on="agreement_no", how="full", coalesce=True)
    joined = joined.with_columns(
        [
            pl.col(ead_amount_alias).fill_null(0.0),
            pl.col(brs_amount_alias).fill_null(0.0),
        ]
    )
    joined = joined.with_columns((pl.col(ead_amount_alias) - pl.col(brs_amount_alias)).alias("difference"))
    joined = joined.with_columns(
        pl.when(pl.col(ead_amount_alias) == 0)
        .then(pl.lit("Missing in EAD"))
        .when(pl.col(brs_amount_alias) == 0)
        .then(pl.lit("Missing in BRS"))
        .otherwise(pl.lit("Amount Mismatch"))
        .alias("status")
    )

    # Only genuine discrepancies -- a matched, equal-amount loan isn't an
    # exception, same as every EAD row-level rule's philosophy.
    flagged = joined.filter(pl.col("difference").abs() > _AMOUNT_TOLERANCE)
    return flagged.select(["loan_id", "product", ead_amount_alias, brs_amount_alias, "difference", "status"]).sort(
        "loan_id"
    )


def ead_vs_brs_disbursement_report(ead_df: pl.DataFrame, brs_disbursement_df: pl.DataFrame) -> pl.DataFrame:
    """EAD's disbursed_amount per loan vs the sum of the uploaded BRS
    Disbursement export's amount for the same loan (BRS agreement_no <->
    EAD loan_id). Flags a loan disbursed per EAD with no (or a mismatched)
    corresponding bank-side Disbursement entry, and vice versa."""
    _require_brs_columns(brs_disbursement_df)
    ead = _ensure_columns(ead_df, ["loan_id", "disbursed_amount"])
    return _reconcile(
        ead, brs_disbursement_df, pl.col("disbursed_amount").sum(), "ead_disbursed_amount", "brs_disbursed_amount"
    )


def ead_vs_brs_collection_report(ead_df: pl.DataFrame, brs_collection_df: pl.DataFrame) -> pl.DataFrame:
    """EAD's principal_paid + interest_paid per loan vs the sum of the
    uploaded BRS Collection export's amount for the same loan. Flags a
    collection recorded on the bank side with no (or a mismatched)
    corresponding repayment in EAD, and vice versa."""
    _require_brs_columns(brs_collection_df)
    ead = _ensure_columns(ead_df, ["loan_id", "principal_paid", "interest_paid"])
    ead_amount_expr = (pl.col("principal_paid").fill_null(0.0) + pl.col("interest_paid").fill_null(0.0)).sum()
    return _reconcile(ead, brs_collection_df, ead_amount_expr, "ead_collection_amount", "brs_collection_amount")
