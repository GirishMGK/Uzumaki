"""Command-line entry point for the Form 26AS formatter."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .merge import parse_files
from .writer import write_csv, write_xlsx


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="form26as",
        description=(
            "Parse one or more TRACES Form 26AS files (text/Excel/HTML/PDF) and "
            "produce a flat, searchable table where every transaction carries "
            "its deductor's name and TAN. Pass multiple files (e.g. one per "
            "assessment year) to get a single combined workbook."
        ),
    )
    p.add_argument(
        "inputs",
        nargs="+",
        help="Path(s) to one or more 26AS files (.txt/.xlsx/.xls/.html/.pdf). "
        "Provide several files to combine multiple years into one workbook.",
    )
    p.add_argument(
        "-o",
        "--output",
        help="Output path. Extension picks the format (.xlsx or .csv). "
        "Defaults to '<input>_formatted.xlsx' for a single file, or "
        "'26AS_Combined_formatted.xlsx' for multiple.",
    )
    p.add_argument(
        "--csv",
        action="store_true",
        help="Also write a .csv alongside the .xlsx output.",
    )
    p.add_argument(
        "--password",
        help="Password applied to any encrypted PDF or Excel inputs (TRACES "
        "26AS downloads use your date of birth as DDMMYYYY, e.g. 15041985).",
    )
    p.add_argument(
        "--debug",
        action="store_true",
        help="Print each file's detected grid (first rows) to help diagnose parsing.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    paths = [Path(p) for p in args.inputs]
    for p in paths:
        if not p.exists():
            print(f"error: No such file: {p}", file=sys.stderr)
            return 2

    passwords = {p: args.password for p in paths} if args.password else {}
    transactions, results = parse_files(paths, passwords=passwords, debug=args.debug)

    for r in results:
        name = r["path"].name
        if r["error"]:
            hint = " (if it's password-protected, pass --password)" if r["needs_password"] else ""
            print(f"error: could not read {name}: {r['error']}{hint}", file=sys.stderr)
        elif r["count"] == 0:
            print(
                f"warning: no TDS/TCS transactions found in {name} "
                f"(detected year: {r['year']}).",
                file=sys.stderr,
            )
        else:
            print(f"  {name}: {r['count']} transactions (year: {r['year']})")

    if args.output:
        out_path = Path(args.output)
    elif len(paths) == 1:
        out_path = paths[0].with_name(f"{paths[0].stem}_formatted.xlsx")
    else:
        out_path = paths[0].parent / "26AS_Combined_formatted.xlsx"

    if out_path.suffix.lower() == ".csv":
        write_csv(transactions, out_path)
    else:
        write_xlsx(transactions, out_path)
        if args.csv:
            csv_path = out_path.with_suffix(".csv")
            write_csv(transactions, csv_path)
            print(f"Wrote {csv_path}")

    print(f"Wrote {out_path}  ({len(transactions)} transactions total across {len(paths)} file(s))")
    return 0 if transactions else 1


if __name__ == "__main__":
    raise SystemExit(main())
