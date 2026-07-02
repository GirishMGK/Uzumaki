"""Double-click friendly launcher: pick a 26AS file, convert it, open the result.

The conversion itself lives in :func:`convert` (no GUI, so it is testable). The
:func:`main` entry point adds a Tkinter file picker / password prompt and opens
the finished workbook, which is what the bundled ``.bat`` runs.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional, Tuple

from .loader import load_grid
from .parser import parse_part_a
from .writer import write_xlsx


def convert(path: Path, password: Optional[str] = None) -> Tuple[Path, int]:
    """Load, parse and write the formatted workbook. Returns (output_path, count)."""
    grid = load_grid(path, password=password)
    transactions = parse_part_a(grid)
    out_path = path.with_name(f"{path.stem}_formatted.xlsx")
    write_xlsx(transactions, out_path)
    return out_path, len(transactions)


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


def main(argv: Optional[list[str]] = None) -> int:
    import tkinter as tk
    from tkinter import filedialog, messagebox, simpledialog

    argv = sys.argv[1:] if argv is None else argv
    dropped = argv[0] if argv and argv[0].strip() else None

    root = tk.Tk()
    root.withdraw()

    try:
        if dropped:
            selected = dropped
        else:
            selected = filedialog.askopenfilename(
                title="Select your Form 26AS file",
                filetypes=[
                    ("Form 26AS files", "*.txt *.xlsx *.xls *.html *.htm *.pdf"),
                    ("All files", "*.*"),
                ],
            )
        if not selected:
            return 0

        path = Path(selected)
        password: Optional[str] = None
        try:
            out_path, count = convert(path, password)
        except Exception:
            # Most commonly an encrypted PDF — ask for the password and retry.
            if path.suffix.lower() == ".pdf":
                password = simpledialog.askstring(
                    "PDF password",
                    "This PDF looks protected.\n\nEnter the password "
                    "(TRACES uses your date of birth as DDMMYYYY, e.g. 15041985):",
                    show="*",
                )
                if password is None:
                    return 0
                out_path, count = convert(path, password)
            else:
                raise

        if count == 0:
            messagebox.showwarning(
                "No transactions found",
                "The file was read, but no Part A (TDS) transactions were found.\n\n"
                "The layout may differ from what the tool expects. Re-run from the "
                "command line with --debug and share the output so it can be tuned.",
            )
        else:
            messagebox.showinfo(
                "Done",
                f"Created:\n{out_path}\n\n{count} transactions.\n\nOpening it now...",
            )
            _open_file(out_path)
        return 0

    except Exception as exc:  # noqa: BLE001 - present any failure in a dialog
        messagebox.showerror("Error", f"Could not process the file:\n\n{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
