"""
# company - type a US-listed company, get an Excel model built from its SEC filings

Plain functions run in order by `pipeline.run`:

| Step | Module | What it does |
|---|---|---|
| find the company | `sec.resolve` | name or ticker -> SEC company id (CIK); ambiguous names stop with the candidates |
| list filings | `sec.filings` | last five 10-Ks, latest 10-Q, latest earnings 8-K; banks / insurers / REITs refused |
| read figures | `sec.company_facts` + `statements.build` | five fiscal years of income, balance sheet and cash flow from SEC XBRL facts |
| analyse | `analysis` | ratios and a small DCF, as pure functions |
| read the press release | `releases` | the one LLM step; every number it returns must be quoted from the release |
| check | `tieout` | accounting identities and a re-calculation of every workbook formula |
| write | `workbook` | the .xlsx: blue numbers from filings, black formulas, a source per row |

Around the model, the earnings loop (no AI step of its own):

| View | Module | What it does |
|---|---|---|
| quarters | `quarters` | the last 8 fiscal quarters from 10-Q facts (by period length); Q4 = 10-K year minus Q1-Q3 |
| compare | `compare` | 2-6 companies side by side |
| earnings review | `review` | locked forecasts vs reported quarters, kill rules, verdicts, forecast accuracy |

Used by the `/company/...` API routes and `evals/run_company.py`.
"""
