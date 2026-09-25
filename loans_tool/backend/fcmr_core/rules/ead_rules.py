"""EAD Files exception rules -- row-level checks for the "Analytics" screen.

Deliberately a separate, self-contained registry from
`fcmr_core.rules.registry` (the Customer Master KYC rule engine): that
registry has no report_type scoping at all (see its module docstring/
CATEGORIES), so folding EAD rules into it would risk a rule_id collision
and, worse, would let either rule set silently run against the wrong
report type's data (Customer Master columns don't exist in an EAD
dataset and vice versa). Keeping EAD rules in their own small registry
means "Analytics -> EAD Files" can only ever offer EAD-shaped rules.

Each rule still follows the same three-column contract the existing
reporting pipeline expects, so `fcmr_core.reporting.builder.
build_exception_csvs` and `fcmr_core.reporting.aggregation` work
unchanged against EAD results too:
    _exc_{rule_id}_status   : "OK" | "WARN" | "ERROR"
    _exc_{rule_id}_code     : short exception code string or ""
    _exc_{rule_id}_desc     : human-readable description or ""

All rules are pure, vectorized Polars expressions (no per-row Python
loops) -- EAD datasets run to millions of rows, unlike the customer
master files the KYC rules were built for.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import polars as pl

RuleFn = Callable[[pl.DataFrame], pl.DataFrame]
ProgressFn = Callable[[int, int, str], None]  # (completed, total, rule_id)

# Every canonical EAD column any rule below touches. Ensured present
# (as an all-null column when missing) before rules run, so a rule can
# reference pl.col(...) unconditionally instead of every rule guarding
# for a column an upload never mapped.
_REQUIRED_COLUMNS: dict[str, pl.PolarsDataType] = {
    "loan_id": pl.Utf8,
    "disbursement_date": pl.Utf8,
    "sanction_date": pl.Utf8,
    "sanction_amount": pl.Float64,
    "disbursed_amount": pl.Float64,
    "future_pos": pl.Float64,
    "ead": pl.Float64,
    "npa_flag_date": pl.Utf8,
    "business_date": pl.Utf8,
    "maturity_date": pl.Utf8,
    "original_tenure": pl.Int64,
    "product_helper": pl.Utf8,
    "ucid": pl.Utf8,
    "customer_id": pl.Utf8,
}


@dataclass(frozen=True)
class EadRuleMeta:
    rule_id: str
    label: str
    fn: RuleFn


def _ensure_columns(df: pl.DataFrame) -> pl.DataFrame:
    missing = [
        pl.lit(None, dtype=dtype).alias(name)
        for name, dtype in _REQUIRED_COLUMNS.items()
        if name not in df.columns
    ]
    return df.with_columns(missing) if missing else df


def _parse_date(df: pl.DataFrame, col: str) -> pl.Expr:
    """A date column -> pl.Date, tolerant of either form it may arrive in:
    already a proper Date/Datetime (DuckDB-backed ingestion auto-parses
    recognized date columns at upload time) or a raw "DD-MM-YYYY" string
    (e.g. a DataFrame built directly from CSV without going through that
    ingestion, or one built in a test). Casting an already-Date column to
    Utf8 first would reformat it to ISO text and then fail to re-parse as
    DD-MM-YYYY, silently nulling out every date -- hence the dtype check.
    """
    if df.schema.get(col) in (pl.Date, pl.Datetime):
        return pl.col(col).cast(pl.Date)
    return pl.col(col).cast(pl.Utf8, strict=False).str.strptime(pl.Date, "%d-%m-%Y", strict=False)


def _annotate(df: pl.DataFrame, rule_id: str, cond: pl.Expr, code: str, desc: pl.Expr, severity: str = "WARN") -> pl.DataFrame:
    status = pl.when(cond).then(pl.lit(severity)).otherwise(pl.lit("OK"))
    code_e = pl.when(cond).then(pl.lit(code)).otherwise(pl.lit(""))
    desc_e = pl.when(cond).then(desc).otherwise(pl.lit(""))
    return df.with_columns(
        [
            status.alias(f"_exc_{rule_id}_status"),
            code_e.alias(f"_exc_{rule_id}_code"),
            desc_e.alias(f"_exc_{rule_id}_desc"),
        ]
    )


# ══════════════════════════════════════════════════════════════════════════════
# Rules
# ══════════════════════════════════════════════════════════════════════════════
def rule_quick_mortality(df: pl.DataFrame) -> pl.DataFrame:
    """Rule 1: loan went NPA less than 365 days after disbursal."""
    disb, npa = _parse_date(df, "disbursement_date"), _parse_date(df, "npa_flag_date")
    days = (npa - disb).dt.total_days()
    cond = npa.is_not_null() & disb.is_not_null() & (days < 365)
    desc = pl.lit("Went NPA ") + days.cast(pl.Utf8) + pl.lit(" days after disbursal (< 365)")
    return _annotate(df, "quick_mortality", cond, "QUICK_MORTALITY", desc)


def rule_sanction_disbursal_delay(
    df: pl.DataFrame, thresholds: dict[str, int] | None = None, default_days: int = 30
) -> pl.DataFrame:
    """Rule 2: disbursed too long after sanction, threshold configurable per Product Helper (Type)."""
    disb, sanc = _parse_date(df, "disbursement_date"), _parse_date(df, "sanction_date")
    delay = (disb - sanc).dt.total_days()
    threshold = pl.col("product_helper").replace_strict(
        thresholds or {}, default=default_days, return_dtype=pl.Int64
    )
    cond = disb.is_not_null() & sanc.is_not_null() & (delay > threshold)
    desc = (
        pl.lit("Disbursed ")
        + delay.cast(pl.Utf8)
        + pl.lit(" days after sanction (threshold ")
        + threshold.cast(pl.Utf8)
        + pl.lit(")")
    )
    return _annotate(df, "sanction_disbursal_delay", cond, "SANCTION_DISBURSAL_DELAY", desc)


def rule_disbursal_before_sanction(df: pl.DataFrame) -> pl.DataFrame:
    """Rule 4: disbursed before it was sanctioned."""
    disb, sanc = _parse_date(df, "disbursement_date"), _parse_date(df, "sanction_date")
    cond = disb.is_not_null() & sanc.is_not_null() & (disb < sanc)
    desc = pl.lit("Disbursed ") + (sanc - disb).dt.total_days().cast(pl.Utf8) + pl.lit(" days before sanction")
    return _annotate(df, "disbursal_before_sanction", cond, "DISBURSAL_BEFORE_SANCTION", desc)


def rule_outstanding_exceeds_disbursed(df: pl.DataFrame) -> pl.DataFrame:
    """Rule 5: outstanding principal (FuturePOS) exceeds the amount ever disbursed."""
    disbursed, future_pos = pl.col("disbursed_amount"), pl.col("future_pos")
    cond = disbursed.is_not_null() & future_pos.is_not_null() & (disbursed < future_pos)
    desc = (
        pl.lit("Outstanding ")
        + future_pos.cast(pl.Utf8)
        + pl.lit(" exceeds disbursed amount ")
        + disbursed.cast(pl.Utf8)
        + pl.lit(" (as of ")
        + pl.col("business_date").cast(pl.Utf8, strict=False)
        + pl.lit(")")
    )
    return _annotate(df, "outstanding_exceeds_disbursed", cond, "OUTSTANDING_EXCEEDS_DISBURSED", desc)


def rule_disbursed_exceeds_sanctioned(df: pl.DataFrame) -> pl.DataFrame:
    """Rule 6: disbursed more than what was sanctioned."""
    disbursed, sanctioned = pl.col("disbursed_amount"), pl.col("sanction_amount")
    cond = disbursed.is_not_null() & sanctioned.is_not_null() & (disbursed > sanctioned)
    desc = pl.lit("Disbursed ") + disbursed.cast(pl.Utf8) + pl.lit(" exceeds sanctioned ") + sanctioned.cast(pl.Utf8)
    return _annotate(df, "disbursed_exceeds_sanctioned", cond, "DISBURSED_EXCEEDS_SANCTIONED", desc)


def rule_negative_ead(df: pl.DataFrame) -> pl.DataFrame:
    """Rule 8: EAD is negative."""
    ead = pl.col("ead")
    cond = ead.is_not_null() & (ead < 0)
    desc = pl.lit("EAD is ") + ead.cast(pl.Utf8)
    return _annotate(df, "negative_ead", cond, "NEGATIVE_EAD", desc)


def rule_tenure_mismatch(df: pl.DataFrame) -> pl.DataFrame:
    """Rule 11: (Maturity - Disbursal) in months doesn't match Original Tenure (±1 month tolerance)."""
    disb, maturity = _parse_date(df, "disbursement_date"), _parse_date(df, "maturity_date")
    months = (maturity.dt.year() - disb.dt.year()) * 12 + (maturity.dt.month() - disb.dt.month())
    tenure = pl.col("original_tenure")
    diff = (months - tenure).abs()
    cond = disb.is_not_null() & maturity.is_not_null() & tenure.is_not_null() & (diff > 1)
    desc = (
        pl.lit("Maturity implies ")
        + months.cast(pl.Utf8)
        + pl.lit(" month tenure, but Original Tenure is ")
        + tenure.cast(pl.Utf8)
    )
    return _annotate(df, "tenure_mismatch", cond, "TENURE_MISMATCH", desc)


def rule_maturity_before_disbursal_or_sanction(df: pl.DataFrame) -> pl.DataFrame:
    """Rule 12: maturity date is before disbursal and/or sanction date."""
    disb, sanc, maturity = (
        _parse_date(df, "disbursement_date"),
        _parse_date(df, "sanction_date"),
        _parse_date(df, "maturity_date"),
    )
    before_disb = maturity.is_not_null() & disb.is_not_null() & (maturity < disb)
    before_sanc = maturity.is_not_null() & sanc.is_not_null() & (maturity < sanc)
    cond = before_disb | before_sanc
    desc = (
        pl.when(before_disb & before_sanc)
        .then(pl.lit("Maturity is before both disbursal and sanction"))
        .when(before_disb)
        .then(pl.lit("Maturity is before disbursal date"))
        .otherwise(pl.lit("Maturity is before sanction date"))
    )
    return _annotate(df, "maturity_before_disbursal_or_sanction", cond, "MATURITY_BEFORE_DISBURSAL_OR_SANCTION", desc)


def rule_matured_still_on_book(df: pl.DataFrame) -> pl.DataFrame:
    """Rule 13: past maturity date but still carrying outstanding exposure."""
    maturity, business = _parse_date(df, "maturity_date"), _parse_date(df, "business_date")
    future_pos = pl.col("future_pos").fill_null(0.0)
    cond = maturity.is_not_null() & business.is_not_null() & (maturity < business) & (future_pos > 0)
    desc = (
        pl.lit("Matured ")
        + (business - maturity).dt.total_days().cast(pl.Utf8)
        + pl.lit(" days ago, still ")
        + future_pos.cast(pl.Utf8)
        + pl.lit(" outstanding")
    )
    return _annotate(df, "matured_still_on_book", cond, "MATURED_STILL_ON_BOOK", desc)


def rule_invalid_tenure(df: pl.DataFrame) -> pl.DataFrame:
    """Rule 14: Original Tenure is zero or negative."""
    tenure = pl.col("original_tenure")
    cond = tenure.is_not_null() & (tenure <= 0)
    desc = pl.lit("Original Tenure is ") + tenure.cast(pl.Utf8)
    return _annotate(df, "invalid_tenure", cond, "INVALID_TENURE", desc)


def rule_new_disbursal_to_npa_customer(df: pl.DataFrame) -> pl.DataFrame:
    """Rule 15: a customer's new loan disbursed on/after another of their loans going NPA."""
    work = df.with_columns(
        [
            pl.coalesce([pl.col("ucid"), pl.col("customer_id")]).alias("_cust_key"),
            _parse_date(df, "disbursement_date").alias("_disb_dt"),
            _parse_date(df, "npa_flag_date").alias("_npa_dt"),
        ]
    )
    npa_events = work.filter(pl.col("_npa_dt").is_not_null() & pl.col("_cust_key").is_not_null()).select(
        [
            pl.col("_cust_key"),
            pl.col("loan_id").alias("_other_loan_id"),
            pl.col("_npa_dt").alias("_other_npa_dt"),
        ]
    )
    joined = work.join(npa_events, on="_cust_key", how="left")
    offending = joined.filter(
        (pl.col("loan_id") != pl.col("_other_loan_id"))
        & pl.col("_disb_dt").is_not_null()
        & pl.col("_other_npa_dt").is_not_null()
        & (pl.col("_disb_dt") >= pl.col("_other_npa_dt"))
    )
    earliest = offending.group_by("loan_id").agg(pl.col("_other_npa_dt").min().alias("_earliest_other_npa"))

    work = work.join(earliest, on="loan_id", how="left")
    gap_days = (pl.col("_disb_dt") - pl.col("_earliest_other_npa")).dt.total_days()
    cond = pl.col("_earliest_other_npa").is_not_null()
    desc = pl.lit("Disbursed ") + gap_days.cast(pl.Utf8) + pl.lit(" days after another loan of this customer went NPA")
    work = _annotate(work, "new_disbursal_to_npa_customer", cond, "NEW_DISBURSAL_TO_NPA_CUSTOMER", desc)
    return work.drop(["_cust_key", "_disb_dt", "_npa_dt", "_earliest_other_npa"])


EAD_ROW_RULES: list[EadRuleMeta] = [
    EadRuleMeta("quick_mortality", "Quick Mortality (NPA < 365 days after disbursal)", rule_quick_mortality),
    EadRuleMeta(
        "sanction_disbursal_delay", "Sanction-to-Disbursal Delay", rule_sanction_disbursal_delay
    ),
    EadRuleMeta(
        "disbursal_before_sanction", "Disbursal Before Sanction", rule_disbursal_before_sanction
    ),
    EadRuleMeta(
        "outstanding_exceeds_disbursed", "Outstanding Exceeds Disbursed", rule_outstanding_exceeds_disbursed
    ),
    EadRuleMeta(
        "disbursed_exceeds_sanctioned", "Disbursed Exceeds Sanctioned", rule_disbursed_exceeds_sanctioned
    ),
    EadRuleMeta("negative_ead", "Negative EAD", rule_negative_ead),
    EadRuleMeta("tenure_mismatch", "Tenure Mismatch", rule_tenure_mismatch),
    EadRuleMeta(
        "maturity_before_disbursal_or_sanction",
        "Maturity Before Disbursal/Sanction",
        rule_maturity_before_disbursal_or_sanction,
    ),
    EadRuleMeta("matured_still_on_book", "Already Matured, Still On-Book", rule_matured_still_on_book),
    EadRuleMeta("invalid_tenure", "Invalid Tenure (<= 0)", rule_invalid_tenure),
    EadRuleMeta(
        "new_disbursal_to_npa_customer",
        "New Disbursal to Already-NPA Customer",
        rule_new_disbursal_to_npa_customer,
    ),
]

_RULES_NEEDING_THRESHOLDS = {"sanction_disbursal_delay"}


def list_ead_row_rules() -> list[EadRuleMeta]:
    return list(EAD_ROW_RULES)


def run_ead_row_rules(
    df: pl.DataFrame,
    rule_ids: list[str] | None = None,
    *,
    sanction_delay_thresholds: dict[str, int] | None = None,
    sanction_delay_default_days: int = 30,
    on_progress: ProgressFn | None = None,
) -> pl.DataFrame:
    """Run the selected EAD row rules (or all of them) against a consolidated
    EAD DataFrame, returning it annotated with _exc_* columns per rule."""
    df = _ensure_columns(df)
    selected = EAD_ROW_RULES if rule_ids is None else [m for m in EAD_ROW_RULES if m.rule_id in set(rule_ids)]
    total = len(selected)
    for idx, meta in enumerate(selected):
        if meta.rule_id in _RULES_NEEDING_THRESHOLDS:
            df = meta.fn(df, sanction_delay_thresholds, sanction_delay_default_days)
        else:
            df = meta.fn(df)
        if on_progress:
            on_progress(idx + 1, total, meta.rule_id)
    return df
