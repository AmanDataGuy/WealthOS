# tests/test_tax.py
"""Tax on hand-computed cases (FY 2025-26 rules, checked against the Income Tax Department's pages)."""
import pytest

from wealthos import engine
from wealthos import tax
from wealthos.ingest import parse_file


@pytest.mark.parametrize("regime, kw, expected", [
    ("new", dict(salary=1275000), 0),                       # 12.75 L salary -> 12 L taxable -> rebate wipes 60,000
    ("new", dict(salary=1285000), 10400),                   # 12.10 L: marginal relief, tax = the 10,000 above 12 L, + 4% cess
    ("new", dict(salary=2075000), 208000),                  # 20 L: 20k+40k+60k+80k = 2,00,000 + cess
    ("old", dict(salary=1000000, deductions_80c=200000), 75400),  # 10 L - 50k SD - 1.5 L (80C cap) = 8 L: 72,500 + cess
    ("new", dict(stcg=100000, ltcg=300000), 0),              # unused basic exemption (4 L) absorbs the gains
    ("new", dict(salary=1075000, stcg=100000), 20800),       # rebate covers slab tax only: 20,000 on STCG stays, + cess
    ("new", dict(salary=1575000, stcg=50000, ltcg=225000), 132600),  # 1,05,000 slab + 10,000 STCG + 12,500 LTCG, + cess
    ("new", dict(salary=6075000), 1578720),                  # 60 L: 13,80,000 + 10% surcharge + cess
    ("old", dict(salary=250000, ltcg=275000), 13000),        # 2 L normal + 2.75 L LTCG = 4.75 L: 87A can't touch the
                                                             # 12,500 on 112A gains (s.112A(6)), + cess
    ("old", dict(salary=250000, stcg=100000), 0),            # old regime: 87A does cover 111A STCG tax (10,000)
])
def test_hand_computed(regime, kw, expected):
    assert tax.compute("2025-26", regime, **kw)["total_tax"] == expected


def test_compare_uses_tradebook_gains(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "DATA_ROOT", tmp_path / "users")
    p = tmp_path / "tradebook.csv"
    p.write_text("symbol,isin,trade_date,exchange,segment,series,trade_type,auction,quantity,price,trade_id,order_id,order_execution_time\n"
                 "INFY,INE009A01021,2024-01-10,NSE,EQ,EQ,buy,false,100,1400,1,1,2024-01-10T10:00:00\n"
                 "INFY,INE009A01021,2025-06-10,NSE,EQ,EQ,sell,false,100,3400,2,2,2025-06-10T10:00:00\n", encoding="utf-8")
    engine.save_tables("u", parse_file(p))
    out = tax.compare("u", "default", "2025-26", salary=1575000)
    assert out["capital_gains"]["long_term"] == 200000 and out["new"]["capital_gains_tax"] == 9375  # (2 L - 1.25 L) x 12.5%
    assert out["lower"] == "new" and out["verified"] and out["sources"]
    assert "not yet verified" in tax.compare("u", "default", "2026-27", salary=1)["warnings"][-1]
    with pytest.raises(ValueError):
        tax.rules("2019-20")
