"""
# workbook.py - the company model as an .xlsx a finance user can audit

Sheets: Cover (company, filings with links, colour key, notes), Income Statement, Balance Sheet, Cash Flow, Ratios,
Valuation, Releases (figures read from the earnings release, verified or not), Checks (tie-outs as live formulas).

- Blue = a number copied from a filing (hover a cell for its XBRL concept, accession number and filing date);
  black = a formula on the same sheet; green = a formula that links to another sheet. A grey note row under each
  title says what the sheet is; the last column names each row's source.
- Every formula cell is recorded with the value Python computed for it (`expected`), so `tieout` can re-calculate
  the saved file and prove the two agree. Excel recalculates everything when the file opens.

Used by `pipeline.py`.
"""
from __future__ import annotations

import re
from pathlib import Path

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from app.company.analysis import ASSUMPTIONS, RATIOS
from app.company.statements import evaluate
from app.company.tieout import CHECKS, TOLERANCE

BLUE, GREEN, RED = Font(color="0000FF"), Font(color="008000"), Font(color="C00000")
BOLD, TITLE = Font(bold=True), Font(bold=True, size=14)
NOTE = PatternFill("solid", fgColor="EDEDED")
FORMATS = {"usd": '#,##0;(#,##0)', "per_share": "0.00", "shares": "#,##0.0", "pct": "0.0%", "x": '0.00"x"'}
_REF = re.compile(r"\{(\w+)(@prev)?\}")


# Fiscal-year label of a year-end date: a year ending in the first week of January is named after the year before
# (Johnson & Johnson's fiscal 2021 ended on 2 January 2022).
def fiscal_year(end: str) -> int:
    return int(end[:4]) - (end[5:7] == "01" and int(end[8:10]) <= 7)


# Column letter of year column i (B, C, ...).
def _col(i: int) -> str:
    return get_column_letter(2 + i)


# Title, grey explanation row and the year header on a sheet.
def _header(ws, title: str, note: str, heads: list[str]) -> None:
    ws["A1"], ws["A1"].font = title, TITLE
    ws["A2"], ws["A2"].fill = note, NOTE
    for c, text in enumerate(["", *heads], start=1):
        cell = ws.cell(row=4, column=c, value=text)
        cell.font, cell.alignment = BOLD, Alignment(horizontal="center", wrap_text=True)
    ws.column_dimensions["A"].width = 48
    for c in range(2, len(heads) + 2):
        ws.column_dimensions[get_column_letter(c)].width = 15
    ws.freeze_panes = "B5"


# Turn "{revenue} - {cost_of_revenue}" into an Excel formula for year column i; also says if it links to another sheet.
def _render(template: str, i: int, here: str, where: dict) -> tuple[str, bool]:
    cross = False

    # One {row} -> its cell reference.
    def ref(m):
        nonlocal cross
        sheet, row = where[m.group(1)]
        col = _col(i - 1 if m.group(2) else i)
        cross |= sheet != here
        return f"{col}{row}" if sheet == here else f"'{sheet}'!{col}{row}"
    return "=" + _REF.sub(ref, template), cross


# Write a row of year values: numbers in blue with their source as a comment, formulas in black / green.
def _write_row(ws, r, label, unit, template, series, srcs, where, expected):
    ws.cell(row=r, column=1, value=label)
    for i, v in enumerate(series):
        if v is None:
            continue
        cell = ws.cell(row=r, column=2 + i)
        if template:
            cell.value, cross = _render(template, i, ws.title, where)
            cell.font = GREEN if cross else Font()
            expected[(ws.title, cell.coordinate)] = v
        else:
            s = srcs[i]
            cell.value, cell.font = v, BLUE
            cell.comment = Comment(f"{s['concept']}\n10-K {s['accn']} filed {s['filed']}\nperiod end {s['end']}", "SEC")
        cell.number_format = FORMATS[unit]


# Build the workbook from a pipeline result and save it; returns {(sheet, cell): python value} for every formula.
def build(result: dict, path: Path) -> dict:
    st, years = result["statements"], result["statements"]["years"]
    values, sources = st["values"], st["sources"]
    heads = [f"FY{fiscal_year(y)}\n{y}" for y in years]
    wb, expected, where = Workbook(), {}, {}
    _cover(wb.active, result)
    for sheet, rows in st["rows"].items():
        for r, line in enumerate(rows, start=5):
            where[line["key"]] = (sheet, r)
    source_col = 2 + len(years)
    for sheet, rows in st["rows"].items():
        ws = wb.create_sheet(sheet)
        _header(ws, f"{result['company']['name']} - {sheet}",
                "$ millions (per-share in $, shares in millions). Blue = from the 10-K, black / green = formula.", heads + ["Source"])
        ws.column_dimensions[get_column_letter(source_col)].width = 60
        for r, line in enumerate(rows, start=5):
            key = line["key"]
            _write_row(ws, r, line["label"], line["unit"], line["formula"], values[key], sources.get(key), where, expected)
            concepts = sorted({s["concept"] for s in sources.get(key) or [] if s})
            note = f"formula: {_REF.sub(lambda m: m.group(1), line['formula'])}" if line["formula"] else ", ".join(concepts) or "not reported"
            ws.cell(row=r, column=source_col, value=note)

    ws = wb.create_sheet("Ratios")
    _header(ws, "Ratios", "Each ratio is a live formula over the statement sheets; blank = a line it needs is not reported.",
            heads + ["Formula"])
    ws.column_dimensions[get_column_letter(source_col)].width = 45
    for r, (key, label, formula, fmt) in enumerate(RATIOS, start=5):
        _write_row(ws, r, label, fmt, formula, result["ratios"][key], None, where, expected)
        ws.cell(row=r, column=source_col, value=_REF.sub(lambda m: m.group(1) + (" (prior year)" if m.group(2) else ""), formula))

    _valuation(wb.create_sheet("Valuation"), result, where, years, expected)
    _releases(wb.create_sheet("Releases"), result.get("releases") or {})
    _checks(wb.create_sheet("Checks"), result, where, heads, expected)
    wb.calculation.fullCalcOnLoad = True
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return expected


# Cover: who, which filings (linked), the colour key and the notes the user should read first.
def _cover(ws, result: dict) -> None:
    c, f = result["company"], result["filings"]
    ws.title = "Cover"
    ws["A1"], ws["A1"].font = f"{c['name']} ({c['ticker']}) - company model from SEC filings", TITLE
    rows = [("SEC company id (CIK)", c["cik"]), ("Industry (SIC)", f"{f['sic']} {f['sic_description']}"),
            ("Built", result["built_at"]), ("Fiscal years", ", ".join(result["statements"]["years"])),
            ("Units", "$ millions; per-share amounts in $; share counts in millions"),
            ("Blue", "copied from a filing (hover a cell for the XBRL concept, accession number and filing date)"),
            ("Black / green", "formula on this sheet / formula linking another sheet"),
            ("Figures source", "SEC XBRL company facts (data.sec.gov); for each period the newest 10-K that reports it wins")]
    for r, (k, v) in enumerate(rows, start=3):
        ws.cell(row=r, column=1, value=k).font = BOLD
        ws.cell(row=r, column=2, value=v).font = BLUE if k == "Blue" else GREEN if k == "Black / green" else Font()
    r = 4 + len(rows)
    ws.cell(row=r, column=1, value="Filings used").font = BOLD
    for x in f["annual"] + f["quarterly"] + f["earnings"]:
        r += 1
        ws.cell(row=r, column=1, value=f"{x['form']} filed {x['filingDate']} (period {x['reportDate']})")
        link = ws.cell(row=r, column=2, value=x["url"])
        link.hyperlink, link.font = x["url"], Font(color="0563C1", underline="single")
    r += 2
    ws.cell(row=r, column=1, value="Notes").font = BOLD
    for note in result.get("notes", []):
        r += 1
        ws.cell(row=r, column=1, value=note)
    ws.column_dimensions["A"].width = 50
    ws.column_dimensions["B"].width = 90


# Valuation: blue assumptions, green links to the statements, and a five-year DCF written as formulas.
def _valuation(ws, result: dict, where: dict, years: list[str], expected: dict) -> None:
    v, i = result["valuation"], len(years) - 1
    ws["A1"], ws["A1"].font = "Valuation - discounted cash flow", TITLE
    ws["A2"], ws["A2"].fill = "Blue cells are assumptions you can change; everything else recalculates. Not investment advice.", NOTE
    ws.column_dimensions["A"].width = 44
    ws.column_dimensions["G"].width = 70
    inp = v["inputs"]
    if v.get("skip"):
        ws["A4"] = v["skip"]
        return
    assumptions = [(5, "Discount rate", inp["discount_rate"], "pct", "flat house rate standing in for a full WACC"),
                   (6, "Terminal growth", inp["terminal_growth"], "pct", "long-run growth after year 5"),
                   (7, "Free cash flow growth, years 1-5", inp["growth"], "pct",
                    f"revenue CAGR over the years shown, held between 0% and {ASSUMPTIONS['growth_cap']:.0%}")]
    for r, label, val, fmt, note in assumptions:
        ws.cell(row=r, column=1, value=label)
        cell = ws.cell(row=r, column=2, value=val)
        cell.font, cell.number_format = BLUE, FORMATS[fmt]
        ws.cell(row=r, column=7, value=note)
    links = [(8, "Free cash flow, latest year", "{fcf}", "base_fcf"), (9, "Total debt", "{total_debt}", "total_debt"),
             (10, "Cash and cash equivalents", "{cash}", "cash"),
             (11, "Short-term investments", "{short_term_investments}", "short_term_investments"),
             (12, "Diluted shares (millions)", "{diluted_shares}", "diluted_shares"), (13, "Revenue, latest year", "{revenue}", "revenue"),
             (14, "EBITDA (operating income + D&A)", "{operating_income} + {dna}", "ebitda"),
             (15, "Diluted EPS ($)", "{eps_diluted}", "eps_diluted")]
    for r, label, template, key in links:
        ws.cell(row=r, column=1, value=label)
        if inp.get(key) is not None:
            cell = ws.cell(row=r, column=2)
            cell.value, _ = _render(template, i, ws.title, where)
            cell.font, cell.number_format = GREEN, FORMATS["per_share" if key == "eps_diluted" else "usd"]
            expected[(ws.title, cell.coordinate)] = inp[key]
    ws.cell(row=17, column=1, value="Year").font = BOLD
    ws.cell(row=18, column=1, value="Free cash flow")
    ws.cell(row=19, column=1, value="Present value")
    for t in range(1, 6):
        col = _col(t - 1)
        ws[f"{col}17"] = t
        ws[f"{col}18"] = f"=B8*(1+B7)^{t}"
        ws[f"{col}19"] = f"={col}18/(1+B5)^{t}"
        expected[(ws.title, f"{col}18")], expected[(ws.title, f"{col}19")] = v["fcf"][t - 1], v["pv"][t - 1]
    out = [(21, "Terminal value (end of year 5)", "=F18*(1+B6)/(B5-B6)", "terminal_value", "usd"),
           (22, "Present value of terminal value", "=B21/(1+B5)^5", "pv_terminal", "usd"),
           (23, "Enterprise value", "=B19+C19+D19+E19+F19+B22", "enterprise_value", "usd"),
           (24, "Net debt (debt - cash - short-term investments)", "=B9-B10-B11", "net_debt", "usd"),
           (25, "Equity value", "=B23-B24", "equity_value", "usd"),
           (26, "Value per share ($)", "=B25/B12", "per_share", "per_share"),
           (27, "Implied EV / revenue", "=B23/B13", "ev_to_revenue", "x"),
           (28, "Implied EV / EBITDA", "=B23/B14", "ev_to_ebitda", "x"),
           (29, "Implied P/E (value per share / diluted EPS)", "=B26/B15", "pe", "x")]
    for r, label, formula, key, fmt in out:
        ws.cell(row=r, column=1, value=label).font = BOLD if key == "per_share" else Font()
        if v.get(key) is not None:
            cell = ws.cell(row=r, column=2, value=formula)
            cell.number_format = FORMATS[fmt]
            expected[(ws.title, cell.coordinate)] = v[key]
    for r in (18, 19, 21, 22, 23, 24, 25):
        for c in "BCDEF":
            ws[f"{c}{r}"].number_format = FORMATS["usd"]
    ws["A31"] = "Market price ($) - type it in to compare"
    ws["B31"].font = BLUE
    ws["A32"] = "Value per share vs market price"
    ws["B32"], ws["B32"].number_format = '=IF(B31>0,B26/B31-1,"")', FORMATS["pct"]


# Releases: what the one AI step read from the latest earnings release, and whether each number was found in it.
def _releases(ws, rel: dict) -> None:
    ws["A1"], ws["A1"].font = "Earnings release - figures read by AI, each checked against the release text", TITLE
    ws["A2"], ws["A2"].fill = ("Only figures whose quote appears word for word in the release and contains the number are "
                               "marked verified. Unverified figures are shown in red and used nowhere else."), NOTE
    ws["A3"] = rel.get("url") or rel.get("error") or "no earnings release read"
    for c, text in enumerate(["Item", "Value", "Unit", "Period", "Quote from the release", "Status"], start=1):
        ws.cell(row=4, column=c, value=text).font = BOLD
    r = 4
    for item in rel.get("items", []):
        r += 1
        ws.cell(row=r, column=1, value=f"{item['kind']}: {item['name']}")
        val = ws.cell(row=r, column=2, value=item["value"])
        ws.cell(row=r, column=3, value=item["unit"])
        ws.cell(row=r, column=4, value=item["period"])
        ws.cell(row=r, column=5, value=item["quote"])
        status = ws.cell(row=r, column=6, value="verified" if item["verified"] else "NOT FOUND in the release - not used")
        val.font = BLUE if item["verified"] else RED
        status.font = Font() if item["verified"] else RED
    for col, width in zip("ABCDEF", (44, 14, 14, 16, 80, 34)):
        ws.column_dimensions[col].width = width


# Checks: each tie-out as a live difference per year, an Excel PASS / FAIL and the Python result.
def _checks(ws, result: dict, where: dict, heads: list[str], expected: dict) -> None:
    _header(ws, "Checks - accounting tie-outs", f"Each cell is a difference that must be within ${TOLERANCE:g}M of zero; "
            "blank = a line it needs is not reported.", heads + ["Excel", "Python check"])
    n = len(result["statements"]["years"])
    py = {c["check"]: c for c in result.get("tieouts", [])}
    for r, (key, what, formula) in enumerate(CHECKS, start=5):
        deltas = [evaluate(formula, result["statements"]["values"], i) for i in range(n)]
        _write_row(ws, r, what, "usd", formula, deltas, None, where, expected)
        cells = ",".join(f"ABS({_col(i)}{r})<={TOLERANCE:g}" for i in range(n))
        ws.cell(row=r, column=2 + n, value=f'=IF(AND({cells}),"PASS","FAIL")')
        c = py.get(key)
        if c:
            ws.cell(row=r, column=3 + n, value=f"{c['status']} ({c['years_passed']}/{c['years_tested']} years)")
    ws.column_dimensions[get_column_letter(3 + n)].width = 24
