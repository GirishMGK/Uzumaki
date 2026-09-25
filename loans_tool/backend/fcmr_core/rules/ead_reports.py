"""EAD Files summary reports -- aggregate views for the "Analytics" screen,
as opposed to `ead_rules.py`'s per-row exception flags. Each function takes
a consolidated EAD DataFrame and returns a small result table (not
annotated row-by-row), meant to be rendered as an HTML table and offered
as a CSV download.
"""

from __future__ import annotations

from datetime import date

import polars as pl

_FY_MONTH_ORDER = ["Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec", "Jan", "Feb", "Mar"]

_REPORT_COLUMNS: dict[str, pl.PolarsDataType] = {
    "loan_id": pl.Utf8,
    "product_helper": pl.Utf8,
    "system": pl.Utf8,
    "scheme_name": pl.Utf8,
    "state": pl.Utf8,
    "stage": pl.Utf8,
    "ead": pl.Float64,
    "gross_book_value": pl.Float64,
    "zero_90_days_interest": pl.Float64,
    "zero_90_int_final": pl.Float64,
    "sanction_amount": pl.Float64,
    "financial_irr": pl.Float64,
    "original_tenure": pl.Int64,
    "disbursement_date": pl.Utf8,
    "disbursed_amount": pl.Float64,
    "business_date": pl.Utf8,
    "npa_flag_date": pl.Utf8,
    "_source_file": pl.Utf8,
}


def _ensure_columns(df: pl.DataFrame, names: list[str]) -> pl.DataFrame:
    missing = [pl.lit(None, dtype=_REPORT_COLUMNS[n]).alias(n) for n in names if n not in df.columns]
    return df.with_columns(missing) if missing else df


def _parse_date(df: pl.DataFrame, col: str) -> pl.Expr:
    """See fcmr_core.rules.ead_rules._parse_date -- same dtype-aware logic:
    an already-Date/Datetime column (DuckDB-backed ingestion auto-parses
    recognized date columns) is cast directly; a raw string column is
    parsed as DD-MM-YYYY."""
    if df.schema.get(col) in (pl.Date, pl.Datetime):
        return pl.col(col).cast(pl.Date)
    return pl.col(col).cast(pl.Utf8, strict=False).str.strptime(pl.Date, "%d-%m-%Y", strict=False)


def product_ead_reconciliation_summary(df: pl.DataFrame) -> pl.DataFrame:
    """Rule 7: product-wise EAD reconciliation. Two tie-out checks:
    Check 1: Stage 1 + Stage 2 + Stage 3 EAD should equal total EAD.
    Check 2: (Gross Book Value + Zero-90-days Interest, both Stage 1+2)
             + Zero-90-Interest-Final (Stage 3) should equal total EAD.
    Minor rounding residuals in either check are expected and acceptable.
    """
    df = _ensure_columns(
        df, ["product_helper", "stage", "ead", "gross_book_value", "zero_90_days_interest", "zero_90_int_final", "loan_id"]
    )
    work = df.with_columns(
        [
            pl.col("stage").cast(pl.Utf8, strict=False).str.extract(r"(\d)", 1).alias("_stage_num"),
            pl.col("product_helper").fill_null("(Unmapped)").alias("_product"),
        ]
    )

    totals = work.group_by("_product").agg(
        [
            pl.col("loan_id").n_unique().alias("lans"),
            pl.col("ead").sum().alias("ead_total"),
        ]
    )

    def _stage_sum(stage_vals: list[str], col: str, out_name: str) -> pl.DataFrame:
        return (
            work.filter(pl.col("_stage_num").is_in(stage_vals))
            .group_by("_product")
            .agg(pl.col(col).sum().alias(out_name))
        )

    parts = [
        _stage_sum(["1"], "ead", "stage1_ead"),
        _stage_sum(["2"], "ead", "stage2_ead"),
        _stage_sum(["3"], "ead", "stage3_ead"),
        _stage_sum(["1", "2"], "gross_book_value", "gbv_stage12"),
        _stage_sum(["1", "2"], "zero_90_days_interest", "zero90_stage12"),
        _stage_sum(["3"], "zero_90_int_final", "zero90final_stage3"),
    ]
    result = totals
    for part in parts:
        result = result.join(part, on="_product", how="left")

    fill_cols = ["stage1_ead", "stage2_ead", "stage3_ead", "gbv_stage12", "zero90_stage12", "zero90final_stage3"]
    result = result.with_columns([pl.col(c).fill_null(0.0) for c in fill_cols])

    result = result.with_columns(
        [
            (pl.col("stage1_ead") + pl.col("stage2_ead") + pl.col("stage3_ead")).alias("check1_stage_sum"),
            (pl.col("ead_total") - (pl.col("stage1_ead") + pl.col("stage2_ead") + pl.col("stage3_ead"))).alias(
                "check1_diff"
            ),
            (pl.col("gbv_stage12") + pl.col("zero90_stage12") + pl.col("zero90final_stage3")).alias(
                "check2_component_sum"
            ),
            (
                pl.col("ead_total") - (pl.col("gbv_stage12") + pl.col("zero90_stage12") + pl.col("zero90final_stage3"))
            ).alias("check2_diff"),
        ]
    )

    return result.rename({"_product": "product_helper"}).sort("product_helper")


def system_product_minmax_summary(df: pl.DataFrame) -> pl.DataFrame:
    """Rule 9: min/max Sanctioned Amount, Financial IRR, and Original Tenure
    for every (System, Product Name) pair."""
    df = _ensure_columns(df, ["system", "scheme_name", "sanction_amount", "financial_irr", "original_tenure", "loan_id"])
    return (
        df.group_by(["system", "scheme_name"])
        .agg(
            [
                pl.col("loan_id").n_unique().alias("lans"),
                pl.col("sanction_amount").min().alias("min_sanction_amount"),
                pl.col("sanction_amount").max().alias("max_sanction_amount"),
                pl.col("financial_irr").min().alias("min_financial_irr"),
                pl.col("financial_irr").max().alias("max_financial_irr"),
                pl.col("original_tenure").min().alias("min_original_tenure"),
                pl.col("original_tenure").max().alias("max_original_tenure"),
            ]
        )
        .sort(["system", "scheme_name"])
    )


def month_wise_disbursal_summary(df: pl.DataFrame, fy_start_year: int, include_state: bool) -> pl.DataFrame:
    """Rule 10: disbursal amount + count by month (Apr-Mar) for the given
    financial year, grouped by System + Product Helper, optionally also by
    State."""
    needed = ["system", "product_helper", "disbursement_date", "disbursed_amount", "loan_id"]
    if include_state:
        needed.append("state")
    df = _ensure_columns(df, needed)

    fy_start, fy_end = date(fy_start_year, 4, 1), date(fy_start_year + 1, 3, 31)
    work = (
        df.with_columns(_parse_date(df, "disbursement_date").alias("_disb_dt"))
        .filter(pl.col("_disb_dt").is_between(fy_start, fy_end, closed="both"))
        .with_columns(pl.col("_disb_dt").dt.strftime("%b").alias("_month"))
    )

    group_cols = ["system", "product_helper"] + (["state"] if include_state else [])
    if work.height == 0:
        empty_cols = group_cols + [f"{m}_{suffix}" for m in _FY_MONTH_ORDER for suffix in ("amount", "count")]
        return pl.DataFrame(schema=dict.fromkeys(empty_cols, pl.Utf8))

    agg = work.group_by(group_cols + ["_month"]).agg(
        [
            pl.col("disbursed_amount").sum().alias("amount"),
            pl.col("loan_id").n_unique().alias("count"),
        ]
    )

    amount_pivot = agg.pivot(values="amount", index=group_cols, on="_month").fill_null(0.0)
    count_pivot = agg.pivot(values="count", index=group_cols, on="_month").fill_null(0)

    amount_pivot = amount_pivot.rename({m: f"{m}_amount" for m in _FY_MONTH_ORDER if m in amount_pivot.columns})
    count_pivot = count_pivot.rename({m: f"{m}_count" for m in _FY_MONTH_ORDER if m in count_pivot.columns})

    result = amount_pivot.join(count_pivot, on=group_cols, how="left")

    ordered_cols = list(group_cols)
    for m in _FY_MONTH_ORDER:
        if f"{m}_amount" in result.columns:
            ordered_cols.append(f"{m}_amount")
        if f"{m}_count" in result.columns:
            ordered_cols.append(f"{m}_count")
    return result.select(ordered_cols).sort(group_cols)


def npa_flag_date_change_report(df: pl.DataFrame) -> pl.DataFrame:
    """Rule 18: flags a loan whose NPAFlagDate was already set in an earlier
    EAD file (by Business Date) and then changed to a *different* non-null
    value in a later one -- a normal null -> first-NPA transition is not
    flagged, only an already-NPA loan's date actually moving."""
    df = _ensure_columns(df, ["loan_id", "business_date", "npa_flag_date", "_source_file"])
    work = df.select(
        [
            pl.col("loan_id"),
            pl.col("_source_file"),
            _parse_date(df, "business_date").alias("_biz_dt"),
            _parse_date(df, "npa_flag_date").alias("_npa_dt"),
        ]
    ).filter(pl.col("_biz_dt").is_not_null() & pl.col("_npa_dt").is_not_null())

    empty_schema = {
        "loan_id": pl.Utf8,
        "from_file": pl.Utf8,
        "from_business_date": pl.Date,
        "from_npa_flag_date": pl.Date,
        "to_file": pl.Utf8,
        "to_business_date": pl.Date,
        "to_npa_flag_date": pl.Date,
    }
    if work.height == 0:
        return pl.DataFrame(schema=empty_schema)

    work = work.unique(subset=["loan_id", "_biz_dt"], keep="first").sort(["loan_id", "_biz_dt"])
    work = work.with_columns(
        [
            pl.col("_npa_dt").shift(1).over("loan_id").alias("_prev_npa_dt"),
            pl.col("_biz_dt").shift(1).over("loan_id").alias("_prev_biz_dt"),
            pl.col("_source_file").shift(1).over("loan_id").alias("_prev_file"),
        ]
    )

    changed = work.filter(pl.col("_prev_npa_dt").is_not_null() & (pl.col("_npa_dt") != pl.col("_prev_npa_dt")))
    if changed.height == 0:
        return pl.DataFrame(schema=empty_schema)

    return changed.select(
        [
            pl.col("loan_id"),
            pl.col("_prev_file").alias("from_file"),
            pl.col("_prev_biz_dt").alias("from_business_date"),
            pl.col("_prev_npa_dt").alias("from_npa_flag_date"),
            pl.col("_source_file").alias("to_file"),
            pl.col("_biz_dt").alias("to_business_date"),
            pl.col("_npa_dt").alias("to_npa_flag_date"),
        ]
    ).sort(["loan_id", "to_business_date"])
