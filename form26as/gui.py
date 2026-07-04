"""Double-click friendly launcher: pick 26AS file(s), convert, open the result.

The conversion itself lives in :mod:`form26as.merge` (no GUI, so it is
testable). This module adds a Tkinter multi-file picker, a PDF password
prompt, and opens the finished workbook — this is what the bundled ``.bat``
runs. Selecting several files (e.g. one 26AS per assessment year) combines
them into a single workbook tagged by year, so 5-10 years of data can be
converted in one go.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import List, Optional

from .merge import FileResult, parse_files
from .writer import write_xlsx


def _open_file(path: Path) -> None:
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            os.system(f'open "{path}"')
        else:
            os.system(f'xdg-open "{path}"')
    except Exception:
        pass


def _default_output_name(paths: List[Path]) -> str:
    if len(paths) == 1:
        return f"{paths[0].stem}_formatted.xlsx"
    return "26AS_Combined_formatted.xlsx"


def _format_results(results: List[FileResult]) -> str:
    lines = []
    for r in results:
        name = r["path"].name
        if r["error"]:
            lines.append(f"  FAILED  {name}  ({r['error']})")
        else:
            lines.append(f"  OK      {name}  -  {r['count']} transactions (year {r['year']})")
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    import tkinter as tk
    from tkinter import filedialog, messagebox, simpledialog

    argv = sys.argv[1:] if argv is None else argv
    dropped = [a for a in argv if a.strip()]

    root = tk.Tk()
    root.withdraw()

    try:
        if dropped:
            selected = dropped
        else:
            selected = filedialog.askopenfilenames(
                title="Select one or more Form 26AS files "
                "(Ctrl/Shift-click to pick several years at once)",
                filetypes=[
                    ("Form 26AS files", "*.txt *.xlsx *.xls *.html *.htm *.pdf"),
                    ("All files", "*.*"),
                ],
            )
        if not selected:
            return 0

        paths = [Path(s) for s in selected]
        protectable = {".pdf", ".xlsx", ".xls", ".xlsm"}

        # TRACES PDFs/Excel files for the same person share one password (date
        # of birth), so one prompt up front covers a whole multi-year batch.
        passwords = {}
        if any(p.suffix.lower() in protectable for p in paths):
            pw = simpledialog.askstring(
                "Password",
                "One or more selected files are PDF/Excel.\n\n"
                "If any are password-protected, enter the password now "
                "(TRACES uses your date of birth as DDMMYYYY, e.g. 15041985).\n"
                "Leave this blank if none are protected.",
                show="*",
            )
            if pw:
                passwords = {p: pw for p in paths if p.suffix.lower() in protectable}

        transactions, results = parse_files(paths, passwords=passwords)

        # Give any file that failed specifically for password reasons (e.g. a
        # different password for one year) one more chance, one at a time.
        # A LegacyExcelError (old .xls format) is never fixed by a password,
        # so those are left alone and just reported.
        for i, r in enumerate(results):
            if r["error"] and r["needs_password"]:
                pw2 = simpledialog.askstring(
                    "Password",
                    f"Could not open:\n{r['path'].name}\n\n({r['error']})\n\n"
                    "Enter its password (or leave blank to skip this file):",
                    show="*",
                )
                if pw2:
                    retry_txns, retry_results = parse_files(
                        [r["path"]], passwords={r["path"]: pw2}
                    )
                    if retry_results[0]["error"] is None:
                        transactions.extend(retry_txns)
                        results[i] = retry_results[0]

        summary_text = _format_results(results)

        save_path = filedialog.asksaveasfilename(
            title="Save the formatted workbook as",
            initialdir=str(paths[0].parent),
            initialfile=_default_output_name(paths),
            defaultextension=".xlsx",
            filetypes=[("Excel workbook", "*.xlsx")],
        )
        if not save_path:
            return 0
        out_path = Path(save_path)
        write_xlsx(transactions, out_path)

        if not transactions:
            messagebox.showwarning(
                "No transactions found",
                "None of the selected files produced any Part A (TDS) "
                "transactions.\n\n" + summary_text + "\n\n"
                "Re-run from the command line with --debug for details.",
            )
        else:
            messagebox.showinfo(
                "Done",
                f"Created:\n{out_path}\n\n"
                f"{len(transactions)} transactions from {len(paths)} file(s):\n\n"
                f"{summary_text}\n\nOpening it now...",
            )
            _open_file(out_path)
        return 0

    except Exception as exc:  # noqa: BLE001 - present any failure in a dialog
        messagebox.showerror("Error", f"Could not process the file(s):\n\n{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
