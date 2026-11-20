# tests/test_excel.py
"""Excel templates. Bad templates are refused with reasons (no LibreOffice needed); a spending template is
filled with a bank statement, recalculated by LibreOffice, and its named outputs equal the numbers computed in Python
(skipped when LibreOffice is not installed)."""
import openpyxl
import pytest
from openpyxl.workbook.defined_name import DefinedName

from app.data import engine
from app.features import excel
from app.data.ingest import parse_file

BANK = ("Date,Narration,Debit,Credit\n05/04/2026,Rent April,25000,\n06/04/2026,Salary,,90000\n10/05/2026,Groceries,4000,\n"
        "05/05/2026,Rent May,25000,\n06/05/2026,Salary,,90000\n")
COL = lambda name: f'INDEX(Data!$A:$Z,0,MATCH("{name}",Data!$1:$1,0))'  # a column found by its header
FORMULAS = {
    "total_spend": f'=SUMIFS({COL("amount")},{COL("direction")},"debit")',
    "total_income": f'=SUMIFS({COL("amount")},{COL("direction")},"credit")',
    "savings_rate": "=IFERROR(ROUND((B2-B1)/B2*100,1),0)",
    "rent": f'=SUMIFS({COL("amount")},{COL("description")},"*Rent*",{COL("direction")},"debit")',
    "debits": f'=COUNTIFS({COL("direction")},"debit")',
    "biggest": f'=MAX({COL("amount")})',
}


def _template(path, formulas=FORMULAS, data_sheet=True, names=True):
    wb = openpyxl.Workbook()
    summary = wb.active
    summary.title = "Summary"
    for i, (name, f) in enumerate(formulas.items(), start=1):
        summary[f"A{i}"], summary[f"B{i}"] = name, f
        if names:
            wb.defined_names[name] = DefinedName(name, attr_text=f"Summary!$B${i}")
    if data_sheet:
        wb.create_sheet("Data")["A1"] = "placeholder"
    wb.save(path)
    return path


def test_bad_templates_refused(tmp_path):
    assert excel.check(_template(tmp_path / "ok.xlsx"))["ok"]
    bad = {"offset.xlsx": dict(formulas={"x": "=SUM(OFFSET(Data!A1,0,0,5,1))"}),
           "indirect.xlsx": dict(formulas={"x": '=SUM(INDIRECT("Data!A1:A5"))'}),
           "dynamic.xlsx": dict(formulas={"x": "=_xlfn.UNIQUE(Data!A:A)"}),
           "table.xlsx": dict(formulas={"x": "=SUM(Sales[amount])"}),
           "nodata.xlsx": dict(data_sheet=False), "nonames.xlsx": dict(names=False)}
    for name, kw in bad.items():
        res = excel.check(_template(tmp_path / name, **kw))
        assert not res["ok"] and res["problems"], name
    (tmp_path / "macro.xlsm").write_bytes(b"x")
    assert "macros" in excel.check(tmp_path / "macro.xlsm")["problems"][0]


def test_template_recalculated_by_libreoffice(tmp_path, monkeypatch):
    try:
        excel.soffice()
    except RuntimeError:
        pytest.skip("LibreOffice is not installed")
    monkeypatch.setattr(engine, "DATA_ROOT", tmp_path / "users")
    (tmp_path / "bank.csv").write_text(BANK, encoding="utf-8")
    engine.save_tables("u", parse_file(tmp_path / "bank.csv"))
    meta = excel.add("u", "default", _template(tmp_path / "spending.xlsx"), "Spending", "transactions")
    out = excel.run("u", "default", meta["id"])
    # the same numbers, computed in Python: debits 25000 + 4000 + 25000, credits 2 x 90000
    assert out["errors"] == [] and out["rows_written"] == 5
    assert out["outputs"] == {"total_spend": 54000, "total_income": 180000, "savings_rate": 70.0, "rent": 50000,
                              "debits": 3, "biggest": 90000}
    assert excel.filled_path("u", "default", meta["id"]).exists()
    assert excel.list_templates("u", "default")[0]["last_run"]["outputs"]["rent"] == 50000


# A defined name gets the same formula check as a cell: web calls, links and other workbooks are refused.
def test_defined_names_checked(tmp_path):
    for i, text in enumerate(['WEBSERVICE("http://example.com")', 'HYPERLINK("http://example.com")',
                              "[1]Sheet1!$A$1", "'C:\\x\\[book.xlsx]Sheet1'!$A$1", "cmd|' /c calc'!A0"]):
        path = _template(tmp_path / f"name{i}.xlsx")
        wb = openpyxl.load_workbook(path)
        wb.defined_names["evil"] = DefinedName("evil", attr_text=text)
        wb.save(path)
        res = excel.check(path)
        assert not res["ok"] and any(p.startswith("defined name evil") for p in res["problems"]), text


# Workspace strings that start like a formula are written to the Data sheet as text, showing the original string.
def test_data_strings_written_as_text(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "DATA_ROOT", tmp_path / "users")
    monkeypatch.setattr(excel, "recalc", lambda src, out_dir: src)  # no LibreOffice needed: keep the filled file
    bank = ("Date,Narration,Debit,Credit\n05/04/2026,\"=HYPERLINK(\"\"http://x\"\")\",100,\n"
            "06/04/2026,+1+1,50,\n07/04/2026,@SUM(1),20,\n08/04/2026,-2+3,10,\n09/04/2026,Rent,5,\n")
    (tmp_path / "bank.csv").write_text(bank, encoding="utf-8")
    engine.save_tables("u", parse_file(tmp_path / "bank.csv"))
    meta = excel.add("u", "default", _template(tmp_path / "t.xlsx"), "T", "transactions")
    excel.run("u", "default", meta["id"])
    ws = openpyxl.load_workbook(excel.filled_path("u", "default", meta["id"]))["Data"]
    cells = [c for row in ws.iter_rows() for c in row if isinstance(c.value, str) and c.value[:1] in "=+-@"]
    assert {c.value for c in cells} >= {'=HYPERLINK("http://x")', "+1+1", "@SUM(1)", "-2+3"}
    assert all(c.data_type == "s" for c in cells)
