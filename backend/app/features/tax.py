"""
# tax.py - old vs new regime, with capital gains from your tradebook. Educational, not tax advice.

Rates come only from `tax_rules/fy<YYYY>_<YY>.yaml`, each checked against the Income Tax Department's pages (the file
lists them). A year whose file says `verified: false` still computes, with a warning on every result.

For each regime, in this order:
1. normal income = salary - standard deduction + other income - deductions (80C / 80D, old regime only, capped);
2. slab tax on normal income;
3. capital gains on listed equity from the tradebook (`engine` FIFO gains for the FY): short term at the 111A rate,
   long term at the 112A rate above the exemption; a resident's unused basic exemption absorbs them first;
4. rebate 87A (new regime: up to the limit, with marginal relief just above it, never on 111A / 112A tax);
5. surcharge by total income (capped at 15% on the capital gains part), then 4% cess.

ponytail: no surcharge marginal relief (only matters above Rs 50 lakh), no carry-forward of losses, gains treated as
listed equity / equity funds with STT paid (what a tradebook holds); debt funds and property are out of scope.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from app.data import engine
from app.data.engine import MetricQuery

RULES = Path(__file__).with_name("tax_rules")
DISCLAIMER = "Educational estimate from published rates, not tax advice. Verify with a chartered accountant before filing."


# The tax rules for one financial year, read from its yaml file (cached).
@lru_cache(maxsize=8)
def rules(fy: str) -> dict:
    path = RULES / f"fy{fy.replace('-', '_')}.yaml"
    if not path.exists():
        raise ValueError(f"no tax rules for FY {fy}; available: {[p.stem[2:].replace('_', '-') for p in sorted(RULES.glob('fy*.yaml'))]}")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


# Tax on `income` over [[upper limit, rate %], ...] slabs (the last limit is None = no limit).
def slab_tax(income: float, slabs: list) -> float:
    tax, lower = 0.0, 0.0
    for upper, rate in slabs:
        top = income if upper is None else min(income, upper)
        if top > lower:
            tax += (top - lower) * rate / 100
        if upper is None or income <= upper:
            break
        lower = upper
    return tax


# The surcharge percent for this income.
def _rate(income: float, bands: list) -> float:  # surcharge %: the first band whose upper limit covers the income
    return next(rate for upper, rate in bands if upper is None or income <= upper)


# One regime's tax, every step shown. `age` is "below_60", "60_to_80" or "above_80" (old-regime slabs only).
def compute(fy: str, regime: str, salary: float = 0, other_income: float = 0, stcg: float = 0, ltcg: float = 0,
            deductions_80c: float = 0, deductions_80d: float = 0, age: str = "below_60") -> dict:
    r = rules(fy)
    reg = r[f"{regime}_regime"]
    cg = r["capital_gains"]
    sd = min(salary, reg["standard_deduction"])
    deductions = 0.0
    if regime == "old":
        caps = reg["deduction_caps"]
        deductions = min(deductions_80c, caps["80C"]) + min(deductions_80d, caps["80D_self_below_60" if age == "below_60" else "80D_self_60_plus"])
    normal = max(0.0, salary - sd + other_income - deductions)
    slabs = reg["slabs"] if regime == "new" else reg["slabs"][age]
    normal_tax = slab_tax(normal, slabs)

    unused = max(0.0, slabs[0][0] - normal)  # basic exemption not used by normal income
    stcg_taxable = max(0.0, stcg - unused)
    unused = max(0.0, unused - stcg)
    ltcg_taxable = max(0.0, ltcg - cg["ltcg_equity_112a_exemption"] - unused)
    stcg_tax = stcg_taxable * cg["stcg_equity_111a_pct"] / 100
    cg_tax = stcg_tax + ltcg_taxable * cg["ltcg_equity_112a_pct"] / 100

    total_income = normal + stcg + ltcg
    rb = reg["rebate_87a"]
    if total_income <= rb["income_limit"]:
        # s.112A(6): in every regime the rebate never reduces tax on 112A long-term gains
        rebate = min(rb["max"], normal_tax if rb["excludes_special_rate_income"] else normal_tax + stcg_tax)
    elif rb["marginal_relief"]:  # the tax on normal income may not exceed the income above the limit
        rebate = max(0.0, normal_tax - (total_income - rb["income_limit"]))
    else:
        rebate = 0.0
    tax = normal_tax + cg_tax - rebate
    rate = _rate(total_income, reg["surcharge"])
    surcharge = max(0.0, tax - cg_tax) * rate / 100 + min(cg_tax, tax) * min(rate, r["surcharge_cap_special_income"]) / 100
    cess = (tax + surcharge) * r["cess_pct"] / 100
    total = round(tax + surcharge + cess)
    gross = salary + other_income + stcg + ltcg
    return {"regime": regime, "fy": fy, "gross_income": gross, "standard_deduction": sd, "deductions": deductions,
            "normal_income": normal, "slab_tax": round(normal_tax), "stcg": stcg, "ltcg": ltcg,
            "ltcg_exempt": min(ltcg, cg["ltcg_equity_112a_exemption"]), "capital_gains_tax": round(cg_tax),
            "rebate_87a": round(rebate), "surcharge_pct": rate, "surcharge": round(surcharge), "cess": round(cess),
            "total_tax": total, "effective_pct": round(100 * total / gross, 2) if gross else 0.0}


# Both regimes for a user's FY: salary etc. from the form, capital gains from the workspace's tradebook.
def compare(user_id: str, workspace_id: str, fy: str, salary: float = 0, other_income: float = 0,
            deductions_80c: float = 0, deductions_80d: float = 0, age: str = "below_60") -> dict:
    r = rules(fy)
    start, end = f"20{fy[2:4]}-04-01", f"20{fy[5:7]}-03-31"
    stcg = ltcg = 0.0
    warnings, provenance = [], None
    try:
        g = engine.run_metric(user_id, MetricQuery(metrics=["realized_gains"], time=f"{start}..{end}"), workspace_id=workspace_id)
        stcg = sum(row.get("short_term_gain") or 0 for row in g.rows)
        ltcg = sum(row.get("long_term_gain") or 0 for row in g.rows)
        provenance = g.provenance.model_dump()
        warnings += g.warnings
    except LookupError:
        warnings.append("no tradebook in this workspace: capital gains are taken as zero")
    if not r["verified"]:
        warnings.append(f"the FY {fy} rates are not yet verified against the official Finance Act text; they repeat the previous year's")
    if stcg < 0 or ltcg < 0:
        warnings.append("a net capital loss is counted as zero; set-off and carry-forward of losses are not computed")
    args = dict(salary=salary, other_income=other_income, stcg=max(stcg, 0.0), ltcg=max(ltcg, 0.0),
                deductions_80c=deductions_80c, deductions_80d=deductions_80d, age=age)
    new, old = compute(fy, "new", **args), compute(fy, "old", **args)
    return {"fy": fy, "new": new, "old": old, "lower": "new" if new["total_tax"] <= old["total_tax"] else "old",
            "difference": abs(new["total_tax"] - old["total_tax"]),
            "capital_gains": {"short_term": stcg, "long_term": ltcg, "provenance": provenance},
            "headroom_80c": max(0.0, r["old_regime"]["deduction_caps"]["80C"] - deductions_80c),
            "verified": r["verified"], "warnings": warnings, "sources": r["sources"], "notes": r.get("notes", []),
            "disclaimer": DISCLAIMER}
