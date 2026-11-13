# Company mode

Type a US-listed company's name or ticker on the **Company** tab and get an Excel model built from its SEC filings:
five fiscal years of income statement, balance sheet and cash flow, ratios, a small DCF, the figures in its latest
earnings release, and a sheet of accounting checks. Every reported number is copied from a filing (blue, with its
XBRL concept, accession number and filing date as a cell comment); every derived number is a live Excel formula.

Code: `backend/app/company/`. API: `POST /company/{user_id}/build` (`{"query": "GE Vernova"}`) and
`GET /company/{user_id}/runs/{run_id}/workbook.xlsx`, both token-guarded; building counts against the AI rate limit.

## Data sources

| What | Where | Notes |
|---|---|---|
| Ticker / name -> company id (CIK) | `www.sec.gov/files/company_tickers.json` | exact ticker, then exact name, then a partial name only one company has; otherwise the candidates are returned |
| Filing list, industry code | `data.sec.gov/submissions/CIK##########.json` | last five 10-Ks, latest 10-Q, latest 8-K with Item 2.02 |
| Every reported figure | `data.sec.gov/api/xbrl/companyfacts/CIK##########.json` | one JSON per company, far simpler than parsing each filing's XBRL |
| Earnings release | exhibit 99 of the latest Item 2.02 8-K | found on the filing's index page |

SEC fair access: every request sends `SEC_USER_AGENT` from the environment, requests are spaced at least 0.15 s
apart (under 10 a second), and answers are cached on disk under `backend/storage/sec_cache/` (one day; filing
documents for good). Banks, insurers and REITs (SIC 6000-6799) and companies without a 10-K are refused with a plain
message: their statements have a different shape.

## The pipeline

Plain functions called in order by `pipeline.run`:

1. `sec.resolve` - find the company, or stop with the list of candidates ("General" -> GE, GD, GM ...).
2. `sec.filings` - the filings above; refuse banks and foreign filers.
3. `statements.build` - each row is a list of standard us-gaap concepts tried in order, then any concept SEC labels
   exactly like the row. Values are matched to the five newest fiscal year-end dates; when several 10-Ks report the
   same period, the newest filing wins, so restatements replace old numbers. Totals the model derives (gross
   profit, net change in cash, total debt, free cash flow) are formulas; the reported totals stay as separate rows
   for the checks.
4. `analysis.ratios` and `analysis.dcf` - pure functions (see below).
5. `releases.read` - the one AI step.
6. `tieout.run` - the accounting checks.
7. `workbook.build` - the .xlsx; then `tieout.workbook_check` re-calculates the saved file.

Sheets: Cover (company, filings with links, colour key, notes), Income Statement, Balance Sheet, Cash Flow,
Ratios, Valuation, Releases, Checks.

### Ratios

Each ratio is one formula text over statement rows; Python evaluates it and the workbook writes the very same
text as an Excel formula. Growth (revenue), margins (gross, operating, net, free cash flow), returns (ROE and ROA on
year-end balances), liquidity (current ratio), leverage (debt to equity, interest cover), cash conversion (operating
cash flow / net income) and capex intensity. A ratio whose rows are not reported is left blank.

### Valuation

Five years of free cash flow growing at a fixed rate, a Gordon-growth terminal value, one flat discount rate:

| Assumption | Value | Why |
|---|---|---|
| Discount rate | 9% | flat house rate standing in for a full WACC; no beta or bond yield is fetched |
| Terminal growth | 3% | long-run nominal growth |
| Growth, years 1-5 | revenue CAGR over the five years shown, held between 0% and 15% | the company's own history, capped |

Net debt = total debt - cash - short-term investments; value per share uses diluted shares. The sheet also shows
the EV / revenue, EV / EBITDA and P/E the value implies, and a blank market-price cell to compare against. All
assumptions are blue input cells. When the latest free cash flow is negative or debt / cash / shares are not
tagged, the DCF is skipped with the reason. No market data is used.

## The one AI step and how its numbers are checked

`releases.read` strips the release's HTML to text and makes one `llm_client.call_json` call (which redacts, logs
cost per user and enforces the daily caps) asking for up to 20 KPIs, guidance items and one-offs. For each item the
model must return a `quote` copied from the release and the `value` exactly as written in that quote.

Code then checks every item (`releases.verify`): the quote must appear in the release text (ignoring case and
spacing) **and** the quote must contain the value. Items that fail stay in the list marked unverified, are shown in
red on the Releases sheet and in the app, and are used nowhere else. In the live run below most unverified items
were table figures the model rescaled ("35,631" returned as 35.631 "USD million"): the check catches exactly that.

## Tie-outs: the eval

Deterministic checks, no judge and no labels ($2M tolerance for rounding; a year whose rows are missing is skipped,
not failed):

| Check | Identity |
|---|---|
| `balance_sheet_balances` | total assets = total liabilities + redeemable equity + total equity |
| `reported_totals_agree` | total assets = total liabilities and equity, both as reported |
| `cash_flow_sums` | operating + investing + financing + FX = reported net change in cash |
| `cash_roll_forward` | cash at end - cash at start (incl. restricted cash, from the balance sheets) = reported net change |
| `gross_profit_ties` | revenue - cost of revenue = reported gross profit |
| `workbook_recalculates` | every formula cell in the saved workbook, re-calculated from the cells it references, equals the Python value |

Live run over five companies (`python -m evals.run_company`), model `openai/gpt-oss-20b` on Groq (the 120b default
had used up its free daily tokens; `LLM_MODEL` overrides it):

<!-- src: backend/evals/company_tieouts.json -->
| Ticker | Balance sheet balances | Reported totals agree | Cash flow sums | Cash roll-forward | Gross profit ties | Workbook recalculates (cells) | Release figures verified |
|---|---|---|---|---|---|---|---|
| AAPL | 5/5 | 5/5 | 5/5 | 5/5 | 5/5 | 128/128 | 13/18 |
| MSFT | 5/5 | 5/5 | 5/5 | 5/5 | 5/5 | 129/129 | 14/19 |
| NVDA | 5/5 | 5/5 | 5/5 | 5/5 | 5/5 | 131/131 | 15/20 |
| JNJ | 5/5 | 5/5 | 5/5 | 5/5 | 5/5 | 119/119 | 14/15 |
| GEV | 3/3 | 3/3 | 4/4 | 1/3 | 4/4 | 66/66 | 18/20 |

<!-- src: backend/evals/company_tieouts.json -->
Totals: 115 of 117 year-checks pass; 573 of 573 workbook formula cells re-calculate to the Python value; 74 of 92
release figures (80.4%) verified against the release text; LLM cost of the whole run $0.0075 (Groq list price, an
upper bound on the free tier).

**The one failure, GE Vernova's cash roll-forward:** GE Vernova was spun off from GE in April 2024. Its newest 10-K
reports year-end 2023 cash of $1,551M, but its cash-flow statements do not roll from that balance: the 2023 and
2024 differences are -$582M and +$603M and nearly cancel, which points to carve-out cash moved outside the
cash-flow statement around the spin. GE Vernova also has only four fiscal years in XBRL and tags no long-term debt
line, so its DCF is skipped.

## The earnings loop: compare, review, plan

Three more views on the Company page turn the one-off model into the loop a portfolio manager runs every quarter.
All three are arithmetic on SEC figures and on what the user typed; none picks a stock, a weight or a trade.

| View | Module | What it does |
|---|---|---|
| Compare | `company/compare.py` | 2-6 companies side by side: latest fiscal year revenue, growth, margins, ROE, leverage, cash conversion (the workbook's own ratio formulas), the latest quarter's revenue growth and operating margin, and P/E at a price the user types. A bank or an ambiguous name gets an error row; the rest still compare. |
| Earnings review | `company/quarters.py`, `company/review.py` | The user **locks** a forecast for a quarter (revenue, margins, diluted EPS, optional consensus, kill rules); it is append-only and hashed. Once the 10-Q is filed, each driver's variance is shown, a quarter is *material* at 5% EPS variance or when the release carries guidance, kill rules are checked in code, and verified one-off and guidance quotes come from the release reader. A forecast locked on or after the 10-Q's filing date is listed but never used (no hindsight). The user records their own verdict (intact / weakened / broken); EPS forecast accuracy (mean absolute error, bias, share within 5%) builds up over reviewed quarters. |
| Rebalance plan | `features/rebalance.plan_trades` | Positions, cash, the user's target weights and limits (max position, max sector, min cash, min trade) → the trades the targets imply, small trades skipped, weights and sector exposure after, and every limit breach. Breaches are reported, never fixed. The book stays in the browser. |

**Quarters from XBRL.** SEC files a quarter's 3-month figure and the 6- or 9-month year-to-date figure under the same
concept, so `quarters.py` picks quarters by period length (80-100 days), not by the `frame` tag. Q4 has no 10-Q: its
amounts are the 10-K year minus Q1-Q3 (marked derived). Q4 diluted EPS is left blank, because EPS does not subtract.
Actuals come from the 10-Q only: an earnings release is not XBRL-tagged, so until the 10-Q is filed the review says
"awaiting results".

**Quarter check (the eval).** Q1 + Q2 + Q3, each from its own 3-month fact, against the separately reported 9-month
figure, within 0.5% or $1M, for AAPL, MSFT, NVDA, JNJ and GEV (`python -m evals.run_company --quarters`):
revenue 68 of 68 fiscal years <!-- src: backend/evals/company_quarters.json#totals.revenue -->,
net income 72 of 72 <!-- src: backend/evals/company_quarters.json#totals.net_income -->.
Unit tests check the variance, rule, accuracy and limit arithmetic exactly on a made-up company
(`tests/test_company_review.py`).

**Not built, on purpose:** price and consensus feeds (licences; Stooq now sits behind a bot check), web search
(everything needed is on EDGAR, with provenance), tracking error (needs a risk model), earnings-call transcripts
(mostly paywalled), automatic re-forecasting (the analyst's judgment), Indian listed companies (a different source).

## Limits

- A build gets 240 seconds. After that the API answers 408; the build finishes only the step it is in (one SEC
  fetch or the AI call), then stops without saving. There is no background job queue or progress report.
- The workbook has five fiscal years only; quarters appear in the earnings review and comparison, not in the
  workbook.
- No segments: company facts carry no dimensional (segment) data; that would need each filing's own XBRL.
- Per-share figures and share counts of older years are as filed, so they are not restated for later stock splits
  (NVIDIA's oldest year shows pre-split shares).
- Rows a company does not tag with a standard concept are blank (Apple's interest expense, Microsoft's SG&A,
  Johnson & Johnson's operating income); ratios that need them are blank too.
- Short-term debt is taken from one concept (`ShortTermBorrowings`, else `CommercialPaper`), so a company tagging
  both separately is understated.
