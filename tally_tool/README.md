# Tally Ledger Extractor

Pulls **every ledger's full transaction history out of Tally in one shot**,
instead of opening and exporting each ledger one by one. Point it at a single
JSON export from Tally and it produces one clean, columnar Excel/CSV table.

## 1. Export from Tally (one-time, per period)

Gateway of Tally → **Display → Day Book** (or any report/period that covers
everything you need)

1. **Alt+F2** → set the date range to the full period (e.g. the full financial year)
2. **F12** (Configure) → make sure narrations and full ledger-entry detail are shown
3. **Alt+E** (Export) → Format: **JSON (Data Interchange)** → Yes to "Export All"
4. Save the file, e.g. `Transactions.json`

This single file contains every **Ledger master** (name, group, opening
balance) and every **Voucher** with its full ledger-entry detail — everything
the script needs.

## 2. Install dependencies (one-time)

```bash
pip install -r requirements.txt
```

## 3. Run it

```bash
python extract_ledgers.py --input "Transactions.json" --output "ledgers_output.xlsx"
```

First run will take a bit longer (it caches a UTF-8 copy of the file next to
the input, since Tally exports UTF-16 and that copy makes re-runs faster).
For a ~300MB export, expect a few minutes end to end.

### Useful options

| Flag | What it does |
|---|---|
| `--format xlsx\|csv\|both` | Output format (default `xlsx`) |
| `--include-cancelled` | Include cancelled/optional vouchers (default: excluded, same as Tally's own default view) |
| `--from-date YYYY-MM-DD` / `--to-date YYYY-MM-DD` | Only show rows in this window (running balance is still computed over the full period, so it stays correct) |
| `--ledger "Name1,Name2"` | Only extract specific ledgers (exact names, comma-separated) |

## 4. What you get

An Excel workbook with two sheets:

- **Transactions** — one row per ledger entry: Ledger Name, Ledger Group,
  Date, Voucher Type, Voucher No, Reference, Party Ledger, Narration, Debit,
  Credit, Opening Balance, Running Balance (+ Dr/Cr label), Bill Reference,
  Voucher GUID, Master ID.
- **Ledger Summary** — one row per ledger: Group, Opening Balance, Total
  Debit, Total Credit, Closing Balance, Transaction Count. Use this to tie
  out against your trial balance.

The script also prints a **control total** at the end (sum of all Debits vs
all Credits across every ledger) — for a clean double-entry export these
should match to the paisa. If they don't, something in the export or the
cancelled/optional filter needs a second look.

## Notes on conventions

- **Debit/Credit** for each entry is taken from the **sign of the `amount`
  field** (negative = Debit, positive = Credit) — *not* Tally's `isdeemedpositive`
  flag. That flag looked equivalent at first, but was found to be unreliable
  on some statutory/duty ledger entries (e.g. TDS lines on vouchers migrated
  from an older Tally version) — one such voucher only balanced to zero once
  the amount's sign was used instead. The script prints a control total
  (`Total Debit` vs `Total Credit`) every run specifically so this kind of
  mismatch doesn't go unnoticed on a different data set.
- **Opening/Running Balance**: positive = Debit balance, negative = Credit
  balance (matches Tally's own export convention for ledger opening balances).
- Cancelled and optional (memo) vouchers are excluded by default, matching
  what Tally itself shows in a normal ledger view. Pass `--include-cancelled`
  to see them (they'll carry `Cancelled`/`Optional` flag columns).

## Re-running for a different company/period

Just repeat step 1 for the new period/company and point `--input` at the new
file — nothing else changes.
