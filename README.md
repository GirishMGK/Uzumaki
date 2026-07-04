# Uzumaki — Form 26AS Formatter

A small tool that takes one or more **Form 26AS** downloads (text, Excel, HTML,
or PDF) and turns their nested **TDS** (Part I) and **TCS** (Part VI) sections
into a **flat, searchable table** — including combining **multiple years into
a single workbook**, since TRACES only ever lets you download one assessment
year at a time.

In a raw 26AS, each deductor/collector is a summary block with its
transactions listed underneath, so the Name and TAN are not on the same row as
each transaction — which makes searching and filtering painful. This tool
flattens everything so **every transaction row carries its deductor/collector's
Name and TAN**, then writes a clean Excel file (with AutoFilter + frozen
header) and/or a CSV.

## Output columns

Each row = one transaction, with deductor/collector **and source file/year/category** context attached:

| Assessment Year | Source File | Category | Deductor/Collector Sr. No. | Name of Deductor/Collector | TAN of Deductor/Collector | Total Amount Paid/Credited | Total Tax Deducted/Collected | Total TDS/TCS Deposited | Txn Sr. No. | Section | Transaction Date | Status of Booking | Date of Booking | Remarks | Amount Paid/Credited | Tax Deducted/Collected | TDS/TCS Deposited |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|

**Category** is `TDS` or `TCS` so you can filter/pivot either on its own or
together. Amounts (including Indian-format numbers like `2,80,482.79`) are
parsed to real numbers, and dates (whether text like `31-Mar-2026` or a native
Excel date cell) are parsed to real dates, so sorting and filtering in Excel
work correctly. **Assessment Year** is auto-detected from each file's contents
(falling back to a year found in the filename), so when you combine several
years the rows stay distinguishable.

## Summary tabs

The Excel output also includes ready-made rollups (each with a grand-total row)
so you can run analysis immediately without building pivots first:

- **Summary by Category** — TDS vs. TCS totals (only added when a file has both)
- **Summary by Year** — totals per assessment year (only added when you convert more than one year at once)
- **Summary by Deductor** — transactions and totals per Category/Name/TAN (kept separate per category, since one entity can be both a TDS deductor and a TCS collector)
- **Summary by Section** — totals per section (194A, 192, 206CL, 206CR, …)
- **Summary by Month** — totals per transaction month

The flat `Form 26AS - TDS & TCS` sheet remains the analysis-ready base table
for your own pivots.

## Easiest: double-click (Windows)

1. Make sure **Python 3.9+** is installed (from
   [python.org](https://www.python.org/downloads/) — tick *"Add Python to
   PATH"* **and** leave *"tcl/tk and IDLE"* ticked during install; the latter
   is what makes the file-picker window possible).
2. **Double-click `Run-26AS-Formatter.bat`.**
   - A console window opens and stays open the whole time (this is normal —
     it shows progress and will tell you if anything goes wrong; press any
     key to close it once you're done).
   - The first run sets everything up automatically (about a minute).
   - A file picker opens — you can **select multiple files at once**
     (Ctrl-click or Shift-click each year's 26AS) to convert **5-10 years in
     one go**; they'll be combined into a single workbook.
   - If any are password-protected PDFs, you'll be asked for the password
     (your date of birth as `DDMMYYYY`, e.g. `15041985`) once for the whole
     batch — if a particular year needs a different password, you'll be
     re-prompted just for that file.
   - You'll be asked where to save the formatted Excel file; it opens
     automatically once saved.

You can also **select several 26AS files in Explorer and drag them all onto
`Run-26AS-Formatter.bat` together** to skip the picker.

### If nothing visibly happens when you double-click it

The console window should stay open now and tell you exactly what's wrong
(missing Python, missing Tkinter, failed install, etc.) — read the last few
lines before closing it. If the window still disappears instantly, open
Command Prompt, `cd` into this folder, and run `Run-26AS-Formatter.bat`
directly so the output doesn't disappear; then send that output along.

## Run from the command line

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

# Excel or PDF work the same way — the format is auto-detected
python -m form26as path/to/26AS.xlsx
python -m form26as path/to/26AS.pdf --password 15041985

# Multiple files (e.g. one per assessment year) combine into one workbook
python -m form26as 26AS_2020-21.txt 26AS_2021-22.txt 26AS_2022-23.pdf --password 15041985 -o 26AS_all_years.xlsx

# Choose the output name/format (.xlsx or .csv from the extension)
python -m form26as path/to/26AS.html -o formatted.xlsx

# Also drop a CSV alongside the xlsx
python -m form26as path/to/26AS.xlsx --csv

# Diagnose an unexpected layout (prints each file's detected grid)
python -m form26as path/to/26AS.txt --debug
```

When several files are given, a single `--password` applies to all of them
(useful since TRACES PDFs for one person share the same DOB-based password).
A problem with one file (wrong password, unreadable, empty) is reported but
does not stop the rest of the batch from being combined.

(`python -m form26as.cli ...` also works.)

### Supported input (auto-detected)

- **Caret (`^`) delimited `.txt`** — the native TRACES download (also tab/comma)
- Genuine `.xlsx` / `.xlsm` workbooks, **including password-protected ones** —
  pass `--password` (TRACES/bank exports use your **date of birth as
  `DDMMYYYY`**, e.g. `15041985`), same as for PDFs. Merged header/data cells
  (common in these reports) are handled automatically.
- **PDF** — including the password-protected TRACES download. Table-based
  PDFs extract best; if a PDF isn't ruled, the loader falls back to
  whitespace-based column splitting.
- HTML exports (including TRACES "Excel" files that are really HTML with an
  `.xls`/`.xlsx` extension)

The format is detected from the file's contents, not just its extension. A
genuine pre-2007 binary `.xls` file (as opposed to modern `.xlsx`) can't be
read directly — you'll get a clear message asking you to re-save it as
`.xlsx` from Excel, rather than a silent "0 transactions" result.

**TDS and TCS are both summarized.** Real TRACES exports label these sections
"PART-I" (TDS) and "PART-VI" (TCS); older exports sometimes use "PART A" /
"PART B" instead — both conventions are recognized. Everything else — PART-II
(15G/15H declarations, where no tax was actually deducted), PART-III/IV/V
(TDS on property/rent/virtual digital assets, which use a different column
layout keyed by a TDS Certificate Number instead of a TAN) — is detected and
skipped so it can't be mis-parsed into the TDS/TCS totals.

> **Tip:** if you download the `.txt` from TRACES, run the tool directly on it —
> no need to manually convert it to Excel first.

## How it works

1. **Load** the file into a uniform grid of cells (delimited text split on `^`,
   `openpyxl` for workbooks, BeautifulSoup for HTML, `pdfplumber` for PDF;
   content is sniffed so a mislabeled HTML "Excel" file still works).
2. **Track which section is active** by watching for a "PART-... - Details
   of ..." boundary row, classifying it as TDS, TCS, or "other" (skipped) from
   its Roman-numeral/letter code. The full "- Details of" phrase is required,
   not just the word "part" - a deductor literally named e.g. "PARTH ..." or
   "PARTNERS ..." must never be mistaken for a section boundary (this was a
   real bug: it silently truncated the deductor list at that row).
3. **Detect** each section's two header rows by their labels (TDS calls them
   "Name/TAN of Deductor" and "Tax Deducted"; TCS calls the identical layout
   "Name/TAN of Collector" and "Tax Collected") so column positions are found
   dynamically rather than hard-coded.
4. **Walk** the rows: a row with a TAN in the TAN column starts a new
   deductor/collector; rows with a valid section code (`194A`, `192`, `206CL`,
   …) *while inside a recognized TDS/TCS section* are its transactions.
5. **Write** one flat row per transaction, tagged with its Category.

## Tests

```bash
pip install -r requirements-dev.txt   # adds reportlab, used to build sample PDFs
python -m unittest discover -s tests -v
```

Synthetic samples live in `samples/` (`.html` and caret-delimited `.txt`); the
PDF tests generate their fixtures on the fly and skip if the PDF libraries are
not installed.

## Note on other parts / layout variations

This targets **TDS (Part I) and TCS (Part VI)**, matching the common layout.
Real 26AS files vary by source and year (and include other parts like
property/rent TDS under a TDS Certificate Number, or 15G/15H declarations)
that are intentionally skipped since they need a different column layout to
parse correctly. If your file doesn't parse cleanly, run with `--debug` and
share the (redacted) output — the header detection and section list can be
extended to match.
