# Uzumaki — Form 26AS Formatter

A small command-line tool that takes a **Form 26AS** download (the TRACES
Excel/HTML export) and turns its nested **Part A** (Details of Tax Deducted at
Source) into a **flat, searchable table**.

In a raw 26AS, each deductor is a summary block with its transactions listed
underneath, so the deductor's **Name** and **TAN** are not on the same row as
each transaction — which makes searching and filtering painful. This tool
flattens everything so **every transaction row carries its deductor's Name and
TAN**, then writes a clean Excel file (with AutoFilter + frozen header) and/or a
CSV.

## Output columns

Each row = one transaction, with deductor context attached:

| Deductor Sr. No. | Name of Deductor | TAN of Deductor | Total Amount Paid/Credited | Total Tax Deducted | Total TDS Deposited | Txn Sr. No. | Section | Transaction Date | Status of Booking | Date of Booking | Remarks | Amount Paid/Credited | Tax Deducted | TDS Deposited |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|

Amounts (including Indian-format numbers like `2,80,482.79`) are parsed to real
numbers, and dates like `31-Mar-2026` are parsed to real dates, so sorting and
filtering in Excel work correctly.

## Summary tabs

The Excel output also includes ready-made rollups (each with a grand-total row)
so you can run analysis immediately without building pivots first:

- **Summary by Deductor** — transactions and totals per Name/TAN
- **Summary by Section** — totals per TDS section (194A, 192, …)
- **Summary by Month** — totals per transaction month

The flat `Form 26AS - Part A` sheet remains the analysis-ready base table for
your own pivots.

## Run it locally

```bash
# 1. (optional but recommended) create a virtual environment
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 2. install dependencies
pip install -r requirements.txt

# 3. run it on your 26AS file
python -m form26as path/to/26AS.txt
```

Requires Python 3.9+.

## Usage

```bash
# Default: writes <input>_formatted.xlsx next to the input file
python -m form26as path/to/26AS.txt

# Choose the output name/format (.xlsx or .csv from the extension)
python -m form26as path/to/26AS.html -o formatted.xlsx

# Also drop a CSV alongside the xlsx
python -m form26as path/to/26AS.xlsx --csv

# Diagnose an unexpected layout (prints the detected grid)
python -m form26as path/to/26AS.txt --debug
```

(`python -m form26as.cli ...` also works.)

### Supported input

- **Caret (`^`) delimited `.txt`** — the native TRACES download (also tab/comma)
- Genuine `.xlsx` / `.xls` workbooks
- HTML exports (including TRACES "Excel" files that are really HTML with an
  `.xls`/`.xlsx` extension — these are detected automatically)

Only **Part A** (TDS) is summarized; Part A1/A2/B/C rows are detected and
skipped so they don't pollute the TDS totals.

> **Tip:** if you download the `.txt` from TRACES, run the tool directly on it —
> no need to manually convert it to Excel first.

## How it works

1. **Load** the file into a uniform grid of cells (`openpyxl` for real
   workbooks, BeautifulSoup for HTML; content is sniffed so a mislabeled HTML
   "Excel" file still works).
2. **Detect** Part A's two header rows by their labels, so column positions are
   found dynamically rather than hard-coded.
3. **Walk** the rows: a row with a TAN in the TAN column starts a new deductor;
   rows with a valid section code (`194A`, `192`, `195`, …) are that deductor's
   transactions.
4. **Write** one flat row per transaction.

## Tests

```bash
python -m unittest discover -s tests -v
```

A synthetic sample lives in `samples/sample_26as.html`.

## Note on other parts / layout variations

This currently targets **Part A**, matching the common layout. Real 26AS files
vary by source and year (and include Part A1/A2/B/C/etc.). If your file doesn't
parse cleanly, run with `--debug` and share the (redacted) output — the header
detection and section list can be extended to match.
