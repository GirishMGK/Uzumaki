"""Exception CSV builders.

Two outputs per run:

  wide CSV  — one row per input record; appends:
                overall_status, exception_count, exception_codes, exception_descriptions
                (codes and descriptions are pipe-joined for multi-exception rows)

  long CSV  — one row per (record, exception); columns:
                _row_num, customer_id, rule_id, status, exception_code, exception_description
"""

from __future__ import annotations

import re
from pathlib import Path

import polars as pl

_EXC_STATUS_RE = re.compile(r"^_exc_(.+)_status$")

# Severity order for overall_status rollup
_SEVERITY = {"OK": 0, "WARN": 1, "ERROR": 2}
_SEVERITY_REV = {0: "OK", 1: "WARN", 2: "ERROR"}


def build_exception_csvs(
    annotated: pl.DataFrame,
    run_id: str,
    outputs_dir: Path,
) -> tuple[Path, Path]:
    """Write wide and long exception CSVs.  Returns (wide_path, long_path).

    Both builds are fully vectorized in Polars (Rust) — no per-row Python loop.
    The previous row×rule scalar-index approach issued ~160M Python↔Rust calls
    on a 1M-row / 27-rule run; this version is a handful of column operations.
    """
    outputs_dir.mkdir(parents=True, exist_ok=True)
    wide_path = outputs_dir / f"{run_id}_wide.csv"
    long_path = outputs_dir / f"{run_id}_long.csv"

    # Collect rule IDs from the annotated frame
    rule_ids = [
        _EXC_STATUS_RE.match(c).group(1)  # type: ignore[union-attr]
        for c in annotated.columns
        if _EXC_STATUS_RE.match(c)
    ]

    exc_cols = [c for c in annotated.columns if c.startswith("_exc_")]
    base_df = annotated.drop(exc_cols)

    # ---- Wide CSV (vectorized) ------------------------------------------
    if rule_ids:
        # Worst severity across all rules: map status→int, horizontal max, map back.
        sev_exprs = [
            pl.col(f"_exc_{rid}_status").fill_null("OK").replace_strict(_SEVERITY, default=0)
            for rid in rule_ids
        ]
        # exception_count = number of rules with a non-empty code.
        count_expr = pl.sum_horizontal(
            [(pl.col(f"_exc_{rid}_code").fill_null("") != "").cast(pl.Int32) for rid in rule_ids]
        )
        # Codes/descs: blank → null so concat_str(ignore_nulls) skips them,
        # preserving rule order and pipe-joining only the ones that fired.
        code_exprs = [
            pl.when(pl.col(f"_exc_{rid}_code").fill_null("") == "")
            .then(pl.lit(None, dtype=pl.Utf8))
            .otherwise(pl.col(f"_exc_{rid}_code").fill_null(""))
            for rid in rule_ids
        ]
        desc_exprs = [
            pl.when(pl.col(f"_exc_{rid}_desc").fill_null("") == "")
            .then(pl.lit(None, dtype=pl.Utf8))
            .otherwise(pl.col(f"_exc_{rid}_desc").fill_null(""))
            for rid in rule_ids
        ]

        codes_joined = pl.concat_str(code_exprs, separator="|", ignore_nulls=True)
        descs_joined = pl.concat_str(desc_exprs, separator="|", ignore_nulls=True)
        overall = pl.col("_worst_sev").replace_strict(_SEVERITY_REV, default="OK")

        wide_df = (
            annotated.with_columns(
                [
                    pl.max_horizontal(sev_exprs).alias("_worst_sev"),
                    count_expr.alias("exception_count"),
                    codes_joined.alias("exception_codes"),
                    descs_joined.alias("exception_descriptions"),
                ]
            )
            .with_columns(overall.alias("overall_status"))
            .drop([*exc_cols, "_worst_sev"])
            .with_columns(
                [
                    pl.col("exception_codes").fill_null(""),
                    pl.col("exception_descriptions").fill_null(""),
                ]
            )
        )
    else:
        wide_df = base_df.with_columns(
            [
                pl.lit("OK").alias("overall_status"),
                pl.lit(0, dtype=pl.Int32).alias("exception_count"),
                pl.lit("").alias("exception_codes"),
                pl.lit("").alias("exception_descriptions"),
            ]
        )

    wide_df.write_csv(str(wide_path))

    # ---- Long CSV (vectorized per rule) ---------------------------------
    # One filtered+selected frame per rule, then a single vertical concat.
    has_rownum = "_row_num" in annotated.columns
    has_cid = "customer_id" in annotated.columns

    parts: list[pl.DataFrame] = []
    for rid in rule_ids:
        part = annotated.select(
            [
                (pl.col("_row_num") if has_rownum else pl.lit(None)).alias("_row_num"),
                (pl.col("customer_id").cast(pl.Utf8) if has_cid else pl.lit("")).alias(
                    "customer_id"
                ),
                pl.lit(rid).alias("rule_id"),
                pl.col(f"_exc_{rid}_status").fill_null("OK").alias("status"),
                pl.col(f"_exc_{rid}_code").fill_null("").alias("exception_code"),
                pl.col(f"_exc_{rid}_desc").fill_null("").alias("exception_description"),
            ]
        ).filter(pl.col("status") != "OK")
        if part.height > 0:
            parts.append(part)

    if parts:
        long_df = pl.concat(parts, how="vertical")
    else:
        long_df = pl.DataFrame(
            {
                "_row_num": pl.Series([], dtype=pl.Int64),
                "customer_id": pl.Series([], dtype=pl.Utf8),
                "rule_id": pl.Series([], dtype=pl.Utf8),
                "status": pl.Series([], dtype=pl.Utf8),
                "exception_code": pl.Series([], dtype=pl.Utf8),
                "exception_description": pl.Series([], dtype=pl.Utf8),
            }
        )
    long_df.write_csv(str(long_path))

    return wide_path, long_path
