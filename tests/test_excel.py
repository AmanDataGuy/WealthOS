# tests/test_excel.py
"""Excel templates. Bad templates are refused with reasons (no LibreOffice needed); a spending template is
filled with a bank statement, recalculated by LibreOffice, and its named outputs equal the numbers computed in Python
(skipped when LibreOffice is not installed)."""
import openpyxl
import pytest
from openpyxl.workbook.defined_name import DefinedName

from wealthos import engine
from wealthos import excel
from wealthos.ingest import parse_file

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
