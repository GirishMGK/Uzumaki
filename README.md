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

## Install

```bash
pip install -r requirements.txt
```

## Usage

```bash
# Default: writes <input>_formatted.xlsx next to the input file
python -m form26as.cli path/to/26AS.xlsx

# Choose the output name/format (.xlsx or .csv from the extension)
python -m form26as.cli path/to/26AS.html -o formatted.xlsx

# Also drop a CSV alongside the xlsx
python -m form26as.cli path/to/26AS.xlsx --csv

# Diagnose an unexpected layout (prints the detected grid)
python -m form26as.cli path/to/26AS.xlsx --debug
```

### Supported input

- Genuine `.xlsx` / `.xls` workbooks
- HTML exports (including TRACES "Excel" files that are really HTML with an
  `.xls`/`.xlsx` extension — these are detected automatically)

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
