# Getting data in

How WealthOS turns an uploaded file into tables it can answer questions from. Everything here is code: no AI reads
an amount. The one optional AI step only suggests which column is which, and the user confirms it.

Code: `backend/app/data/ingest/` (one entry point, `parse_file`), `backend/app/data/mapping.py`,
`backend/app/data/samples.py`.

## What parses

| File | What it becomes | Module |
|---|---|---|
| `.csv`, `.xlsx` | Bank / card statement → `transactions`; broker tradebook → `trades`; holdings export → `holdings`; daily prices → `prices` | `tabular.py` |
| `.pdf` with a password | CAMS / KFintech / NSDL / CDSL CAS → `holdings` and mutual fund `trades` (read with `casparser`; the password is used once, never stored) | `cas.py` |
| `.pdf` bank statement | HDFC, ICICI and SBI style layouts → `transactions` | `bank_pdf.py` |
| any other `.pdf` | 10-K / annual report → `statements` (each number verified or unverified by arithmetic) | `filings.py` |
| `.md`, `.txt` | Notes the assistant can quote | `ai/research.py` |
| Google Sheet link | Read like a CSV (link must be "anyone with the link can view") | `sheets.py` |

Not read: scanned (image-only) PDFs, which have no text layer. The upload says so and suggests the CSV or Excel
download from net banking.

## How a file is recognised

1. **Header row.** Exports often have title lines ("Statement of account…") above the table. The row with the
   most cells that are known column names ("Txn Date", "Narration", "Withdrawal Amt.", "Qty." …) is the header.
2. **Shape.** Each shape has must-have columns. Trades need date, symbol, side, quantity and price; bank
   transactions need date, description and either debit / credit columns or an amount (with an optional Dr/Cr
   column). The first shape whose must-haves are all found wins.
3. **Clean.** Dates are read day-first (Indian style), numbers like `1,23,456.00`, `₹500` or `450.00 Dr` become
   plain numbers, debit / credit becomes one positive amount and a direction. "Total" rows are dropped with a warning.
4. **Checks** (`checks.py`) add warnings, never change data: duplicate rows, a running balance that does not add
   up, gaps of 45+ days between bank entries, and an unusual first-digit pattern.

### PDFs

A password-less PDF goes through these in order:

1. **Bank statement?** The words on each page are grouped into lines (the `finx` reader). If a line on page 1 or 2
   splits into cells that name every must-have bank column (the same name lists as for CSVs), it is a statement.
   An annual report never has such a line, so it falls through to step 2.
   - The header cells set the column edges; each word below goes to the column it sits under.
   - A line with no date and no number right under a row is wrapped narration and is joined to that row.
   - A header repeated on later pages is dropped, as is anything above it (bank name, address).
   - The rebuilt grid goes through the same steps as a CSV, so the running-balance check runs on it too.
2. **Filing.** Otherwise `finx` looks for financial statements.

With a password, the CAS reader is tried first.

Tested layouts (synthetic PDFs built in `tests/test_bank_pdf.py`):

| Style | Columns |
|---|---|
| HDFC | Date, Narration, Chq./Ref.No., Value Dt, Withdrawal Amt., Deposit Amt., Closing Balance |
| SBI | Txn Date, Particulars (wrapping onto a second line), Withdrawals, Deposits, Balance |
| ICICI | Txn Date, Value Date, Description, Amount (Dr/Cr) with "Dr" / "Cr" after each amount, Balance |

## When columns are not recognised: mapping

A table whose must-have columns are not all found comes back as `unknown`. The user maps it once. The suggestion
is built cheapest first:

1. **Saved.** The user already confirmed a mapping for these exact column names: use it. On upload this is
   applied automatically.
2. **Rules.** Known column names, as above.
3. **AI.** Only if must-have columns are still missing: one call with the column names and a few example values
   (personal details masked first). It may only answer with our standard column names.
4. **The user confirms** in the mapper (Upload panel → "Map columns").

Confidence shown per column in the mapper:

| Label | Meaning |
|---|---|
| `saved` | From a mapping this user confirmed before |
| `high` | Rules and AI picked the same column |
| `medium` | Only one of them found it (rules alone when the AI was not needed) |
| `your pick` | The user changed the suggestion |

Must-have columns still missing are listed above the Save button, and Save stays off until they are chosen.
Saving remembers the mapping, so the next file with the same columns maps itself.

## Sample data

"Load sample data" on the upload panel (`POST /sources/{user_id}/sample`) loads the same synthetic files the eval
uses (`app/data/samples.py`): 24 months of a bank statement (Nov 2024 to Oct 2026), a Zerodha-style tradebook with
five trades and a two-line holdings export. They go through the normal import path, so the original CSVs are kept
and every answer points back at them. One bank row's narration is a prompt-injection attempt on purpose; it shows
that text in the user's data is treated as data. The button is off once the account has any data.
