"""Exception CSV builders.

Two outputs per run:

  wide CSV  — one row per input record; appends:
                overall_status, exception_count, exception_codes, exception_descriptions
                (codes and descriptions are pipe-joined for multi-exception rows)

  long CSV  — one row per (record, exception); columns:
                _row_num, customer_id, rule_id, status, exception_code, exception_description

Plus one Excel workbook (build_exception_excel_by_rule): one sheet per
rule, each holding only the rows that rule flagged -- for someone who
wants "all my EAD exceptions in one file, but each exception type on its
own tab" rather than the long CSV's everything-interleaved-by-row shape.
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


_SHEET_NAME_INVALID_CHARS_RE = re.compile(r"[:\\/?*\[\]]")


def _excel_safe_sheet_name(name: str, used: set[str]) -> str:
    """Excel sheet names: max 31 chars, no : \\ / ? * [ ], must be unique
    within the workbook. A rule_id that collides after truncation (two
    different rule_ids sharing the same first 31 safe chars) gets a
    numeric suffix rather than silently overwriting the earlier sheet."""
    safe = _SHEET_NAME_INVALID_CHARS_RE.sub("_", name)[:31]
    candidate = safe
    i = 2
    while candidate in used:
        suffix = f"_{i}"
        candidate = safe[: 31 - len(suffix)] + suffix
        i += 1
    return candidate


#  xlsx is a structured, per-cell-styled format -- xlsxwriter (and so
# polars' write_excel(), which hands it whole columns rather than looping
# per-cell in Python) still costs roughly constant time per cell written,
# measured at ~100us/row for a 13-column sheet regardless of autofit/
# autofilter settings. A rule that flags a large fraction of a large
# dataset reproduces a sheet close to the full dataset's size, and unlike
# consolidate.py's Excel download (one such sheet), this file can have up
# to eleven -- a synthetic worst case (two rules each flagging all
# 400,000 rows) took over two minutes before this cap existed. CSV/Parquet
# have no such cost and stay uncapped; this cap is specific to the Excel
# sheet-per-rule format's inherent overhead.
_MAX_EXCEL_SHEET_ROWS = 50_000


def build_exception_excel_by_rule(annotated: pl.DataFrame, run_id: str, outputs_dir: Path) -> Path:
    """One workbook, one sheet per rule that ran against this dataset --
    each sheet holds only the rows *that rule* flagged (status != OK, up
    to _MAX_EXCEL_SHEET_ROWS of them), with that rule's own exception
    code/description columns appended. A sheet is still created even for
    a rule with zero exceptions, so the file documents every check that
    ran, not just the ones that found something (same "show what was
    tested, not just what failed" principle fcmr_core.reporting.
    workpaper's ICAI workpaper follows for Customer Master). A Summary
    sheet lists every rule with its exception count and a jump link to
    its sheet, noting when a sheet was truncated.
    """
    import xlsxwriter

    outputs_dir.mkdir(parents=True, exist_ok=True)
    xlsx_path = outputs_dir / f"{run_id}_by_exception.xlsx"

    rule_ids = [
        _EXC_STATUS_RE.match(c).group(1)  # type: ignore[union-attr]
        for c in annotated.columns
        if _EXC_STATUS_RE.match(c)
    ]
    exc_cols = [c for c in annotated.columns if c.startswith("_exc_")]
    base_cols = [c for c in annotated.columns if c not in exc_cols]

    header_format = {"bold": True, "bg_color": "#1B3A5C", "font_color": "white"}

    wb = xlsxwriter.Workbook(str(xlsx_path))
    summary_ws = wb.add_worksheet("Summary")
    summary_ws.write_row(0, 0, ["Rule", "Exceptions", "Note"], wb.add_format(header_format))
    summary_ws.set_column(0, 0, 42)
    summary_ws.set_column(1, 1, 14)
    summary_ws.set_column(2, 2, 60)
    link_format = wb.add_format({"underline": True, "font_color": "#1155CC"})

    used_sheet_names: set[str] = set()
    for row_idx, rid in enumerate(rule_ids, start=1):
        flagged = annotated.filter(pl.col(f"_exc_{rid}_status") != "OK")
        total_flagged = flagged.height
        if total_flagged > _MAX_EXCEL_SHEET_ROWS:
            flagged = flagged.head(_MAX_EXCEL_SHEET_ROWS)

        sheet_name = _excel_safe_sheet_name(rid, used_sheet_names)
        used_sheet_names.add(sheet_name)

        summary_ws.write_url(row_idx, 0, f"internal:'{sheet_name}'!A1", link_format, string=rid.replace("_", " ").title())
        summary_ws.write_number(row_idx, 1, total_flagged)
        if total_flagged > _MAX_EXCEL_SHEET_ROWS:
            summary_ws.write_string(
                row_idx,
                2,
                f"Showing first {_MAX_EXCEL_SHEET_ROWS:,} of {total_flagged:,} -- download Wide/Long CSV for the complete set.",
            )

        sheet_cols = [*base_cols, f"_exc_{rid}_code", f"_exc_{rid}_desc"]
        headers = [*base_cols, "exception_code", "exception_description"]
        display_headers = [h.replace("_", " ").title() for h in headers]
        sheet_df = flagged.select(sheet_cols).rename(dict(zip(sheet_cols, display_headers, strict=True)))

        sheet_df.write_excel(
            workbook=wb,
            worksheet=sheet_name,
            header_format=header_format,
            freeze_panes=(1, 0),
            # autofit measures every cell to size columns -- on a rule that
            # flags a large fraction of a large dataset that scan alone
            # took seconds per sheet, multiplied by up to eleven sheets.
            # Fixed widths from the header text are instant and still far
            # more readable than Excel's own un-sized default.
            autofit=False,
            column_widths={h: max(len(h) * 7 + 10, 90) for h in display_headers},
        )

    wb.close()
    return xlsx_path
