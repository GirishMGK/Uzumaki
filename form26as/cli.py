"""Command-line entry point for the Form 26AS formatter."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .loader import load_grid
from .parser import parse_part_a
from .writer import write_csv, write_xlsx


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="form26as",
        description=(
            "Parse a TRACES Form 26AS (Excel/HTML) and produce a flat, "
            "searchable table where every transaction carries its deductor's "
            "name and TAN."
        ),
    )
    p.add_argument("input", help="Path to the 26AS file (.xlsx / .xls / .html)")
    p.add_argument(
        "-o",
        "--output",
        help="Output path. Extension picks the format (.xlsx or .csv). "
        "Defaults to '<input>_formatted.xlsx'.",
    )
    p.add_argument(
        "--csv",
        action="store_true",
        help="Also write a .csv alongside the .xlsx output.",
    )
    p.add_argument(
        "--debug",
        action="store_true",
        help="Print the detected grid (first rows) to help diagnose parsing.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    input_path = Path(args.input)
    try:
        grid = load_grid(input_path)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.debug:
        print(f"Loaded {len(grid)} rows. First 20:", file=sys.stderr)
        for i, row in enumerate(grid[:20]):
            print(f"  [{i}] {row}", file=sys.stderr)

    transactions = parse_part_a(grid)
    if not transactions:
        print(
            "warning: no Part A transactions were found. The file layout may "
            "differ from what the parser expects — re-run with --debug and share "
            "the output so the parser can be tuned.",
            file=sys.stderr,
        )

    if args.output:
        out_path = Path(args.output)
    else:
        out_path = input_path.with_name(f"{input_path.stem}_formatted.xlsx")

    if out_path.suffix.lower() == ".csv":
        write_csv(transactions, out_path)
    else:
        write_xlsx(transactions, out_path)
        if args.csv:
            csv_path = out_path.with_suffix(".csv")
            write_csv(transactions, csv_path)
            print(f"Wrote {csv_path}")

    print(f"Wrote {out_path}  ({len(transactions)} transactions)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
