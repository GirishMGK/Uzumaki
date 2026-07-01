# Sangir Analytics — Tools

A unified Streamlit hub that groups every tool under a **Tools** section in the
sidebar; each tool opens as its own page.

```bash
pip install -r requirements.txt
streamlit run Home.py
```

**Tools in the hub**
| Tool | What it does |
|------|--------------|
| **Disha Parquet Tool** (`parquet_tool.py`) | Python/Streamlit port of the Sangir WPF app: Convert (CSV/Excel→Parquet w/ compression + type inference), Viewer (schema, row-groups, stats, filter, sort), CSV Tools (delimiter, encoding, merge, split-rows, schema-compare), Analytics (DuckDB SQL + query history). |
| **PF & Statutory** (`Combined_PF_Statutory.py`) | PF Challan / ECR Return / TRRN **and** ESI · PT · TDS · GSTR-1 · GSTR-3B extraction — already combined into one two-tab app (shared PDF-read/regex utilities). |
| **PDF Tools** (`pdf_tools.py` + `tools/`) | Merge, split, edit (remove/insert/reorder pages) and PDF→Word. |
| **SOA · RPS · Reconcile** (`app.py`) | L&T Finance SOA extractor with TOC/TOD audit — ships as a standalone Flask app (SSE live progress); the hub page links to it. |
| **Document Redaction** (`redaction_tool/`) | Auto-detect + redact PAN/TAN/GSTIN/CIN/Aadhaar/Phone/Email plus custom keywords across PDF (true redaction via PyMuPDF), DOCX, XLSX, and images (Tesseract OCR) — ships as a standalone tkinter desktop app; the hub page links to it. |
| **JE Audit Analytics** (`je_audit_tool/`) | Journal Entry exception testing for statutory/forensic audit: Amount (duplicates, high-value, split transactions), Timing (weekend/holiday, year-end cutoff, reversals), User & Access Control (SOD violations, dormant users, related parties), Vendor Master Data (duplicate GSTIN/PAN, MSME delay, inactive vendors), Benford's Law (chi-square digit analysis). DuckDB-backed for large GL dumps; exports a multi-sheet Excel audit report + working paper. |
| **JE Audit (Blazor)** (`je_audit_tool_blazor/JEAuditApp.razor`) | Self-contained .NET/Blazor Server port of the same JE audit workflow (7-step wizard, same test families) for teams preferring a C#/.NET deployment — needs the `EPPlus` NuGet package for Excel I/O. |

> **PF + Statutory combinable?** Yes — they already share `_read_pdf_text`,
> `_g`/`normalize_period` helpers and live together in `Combined_PF_Statutory.py`
> as two tabs, so the hub mounts that single file.

---

# SOA Extractor & TOC/TOD Tool (L&T Finance NBFC SOAs)

Parses L&T Finance Statement-of-Account (SOA) PDFs into structured Excel and
auto-computes **Test of Controls / Test of Details** validation checks for NBFC
loan audits. All L&T SOAs share the same layout, so the parser is tuned to it.

## Install
```bash
pip install pdfplumber openpyxl flask
```

## Web UI (localhost)
```bash
python app.py            # then open http://127.0.0.1:5000
```
Pick a document type with the **SOA / RPS** tabs, then upload SOAs/RPS as
**individual PDFs, a whole folder, or a `.zip`**. Files stream through with a
**live progress bar** and an on-screen results table.

**SOA mode** — per-loan working-paper workbooks + a Portfolio Exception Report,
with TOC/TOD validation. Running totals (uploaded / clean / with-exceptions /
failed), NPA count, total sanctioned. Download any single loan, the portfolio
report, all loans (zip), or exceptions-only (zip).

**RPS mode** — one **combined workbook** from all uploaded Repayment Schedules:
- `Loan_Details` — one row per RPS with all header fields (Customer & Bank,
  Loan, Instalment details + contact block).
- `Repayment_Schedule` — every schedule row stacked, each tagged with its
  **Agreement No** so rows trace to the right loan.

**Reconcile mode** — upload a mix of SOAs and RPS; the tool auto-detects each
document type, matches them by **Agreement No**, and reports where *actual*
servicing deviated from the *scheduled* plan:
- `Reconciliation_Summary` — one row per agreement (term mismatches, matched,
  amount-mismatch / paid-late / unpaid counts, RECONCILED vs EXCEPTION).
- `Term_Checks` — static terms (loan amount, rate, tenure, EMI, disb date).
- `Instalment_Comparison` — per instalment, scheduled vs actual with status.
- `Unmatched` — agreements present on only one side.

A bad/corrupt file is reported as FAILED with the reason and never aborts the
batch. Nothing is persisted beyond the running session. Custom port:
`PORT=8080 python app.py`.

### RPS from the command line
```bash
python extract_rps.py schedule.pdf              # single -> 2-sheet workbook
python extract_rps.py --dir ./rps_pdfs out.xlsx # combine a folder
```

## Command line
```bash
# 1. Single loan -> full working-paper workbook
python extract_soa.py SOA_xxxx.pdf [output.xlsx]

# 2. A folder -> one workbook per loan
python extract_soa.py --dir ./pdfs ./out

# 3. A folder -> ONE portfolio exception report (+ per-loan books in ./loan_details)
python extract_soa.py --portfolio ./pdfs Portfolio_Exception_Report.xlsx
```

## Per-loan workbook tabs
| Sheet | Contents |
|-------|----------|
| `Loan_Master` | Customer/contact, GST & KYC ids, loan, instalment, bank & mandate details |
| `Finance_Summary` | Op/Debits/Credits/Closing + receivable block |
| `Disbursements` | Disbursal no / date / amount / particulars |
| `Transactions` | Full ledger (Date, Value Date, Particulars, DR, CR, Balance) |
| `Part_Payment` | Part-payment summary |
| `Bounce_Summary` | Bounce events (Date / Narration / Amount / Reason) |
| `Charges` | Discrete fee line-items (Processing Fee, Broken-Period Interest, Insurance, TDS) |
| `Bounce_Charge_Grid` | EMI-bounce charge slab table |
| `Amortization` | Recomputed reducing-balance schedule |
| `DPD_Analysis` | Per-instalment days-past-due + SMA/NPA stage |
| `TOC_TOD` | Validation checks (see below) |
| `Parse_Quality` | Data-integrity log: completeness %, missing fields, structural warnings, cosmetic notes |

## Portfolio exception report tabs
- **Portfolio_Summary** — one row per loan with key terms, current stage, max DPD,
  exception count, and a CLEAN / EXCEPTION result flag.
- **Exceptions** — only FAIL / REVIEW checks across all loans.
- **DPD_NPA** — delayed instalments (DPD > 0) across all loans, with stage.
- **Charges** — all charge line-items across all loans.
- **Parse_Quality** — per-loan extraction status (OK / REVIEW), completeness %,
  missing critical fields, structural warnings, and cosmetic notes. Use this to
  decide which SOAs need manual review before relying on the numbers.

### Parse quality
Each loan is graded **OK** or **REVIEW**. A loan is flagged **REVIEW** only for
*structural* problems — a missing critical field (e.g. sanctioned amount, rate) or
an unparsed section (finance summary, ledger). Purely *cosmetic* issues (e.g. a
ledger row whose description wrapped across lines, but whose amounts are correct)
are recorded as **notes** and do **not** force a review.

## Validation checks (TOC/TOD)
| ID | Family | Procedure |
|----|--------|-----------|
| TOD-01 | EMI / amortization | Recompute EMI from sanctioned amount, rate & tenure |
| TOD-02 | Reconciliation | Principal Paid + Future Principal = Loan Amount |
| TOD-03 | Reconciliation | Op.Bal + Debits − Credits = Cl.Bal |
| TOD-04 | Reconciliation | Sum of receivable components = Total Receivable |
| TOC-01 | Reconciliation | Sum of disbursals = sanctioned amount |
| TOC-02 | DPD/NPA | Max historical days-past-due (SMA/NPA staging) |
| TOC-03 | DPD/NPA | Current asset classification as on statement date |
| TOC-04 | Income | Interest income recognized |
| TOC-05 | Charges | Processing fee % of sanction (GST-adjusted) |
| TOD-06 | Charges | Broken-period interest recompute (disbursal → 1st billing cycle) |
| TOC-06 | Charges | Bounce charge vs sanction-slab grid |
| TOD-05 | Reconciliation | Final ledger balance |

Status colours: **PASS** green, **REVIEW** amber, **FAIL** red, **INFO** blue.

## Tunable assumptions (top of `extract_soa.py`)
- `GST_RATE = 0.18` — GST grossed into "Incl. Tax" charge lines
- `PF_PCT_THRESHOLD = 3.0` — processing-fee % of sanction above which we flag REVIEW
- `BPI_TOLERANCE = 0.15` — acceptable deviation on broken-period interest

> Note: PII fields in the SOA are masked by the issuer; the `Address / Contact`
> field is best-effort due to the multi-column layout and should be reviewed manually.

## PDF Tools (Streamlit)
A separate utility app for general PDF operations:
```bash
pip install streamlit pypdf
streamlit run pdf_tools.py
```
Features: Merge PDFs, Split PDF, Edit PDF (remove/insert/reorder pages), PDF to Word.
