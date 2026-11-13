"""
# rebalance.py - how far your portfolio is from the mix you chose

You set a target weight per holding (% of the portfolio). From the latest holdings snapshot the code computes each
holding's current weight, its drift (current minus target, in percentage points) and the rupee change that would
bring it back to target. Arithmetic on targets you chose, not advice: it never picks targets or names what to buy.

Targets are stored as `storage/users/<id>/rebalance_targets.json`: `{holding: target_pct}`, summing to 100.
"""
import json

from app.data import engine
from app.data.engine import MetricQuery

LABEL = "Arithmetic on the target mix you set, not investment advice."


def _path(user_id: str):
    return engine.user_dir(user_id) / "rebalance_targets.json"


# The user's target weights {holding: pct}; {} if none set.
def targets(user_id: str) -> dict[str, float]:
    path = _path(user_id)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


# Save target weights: each 0-100, together 100 (within 0.5, so rounded inputs like 33.3 x 3 pass).
def save_targets(user_id: str, new: dict[str, float]) -> dict[str, float]:
    clean = {str(k).strip(): float(v) for k, v in new.items() if str(k).strip() and float(v) > 0}
    if any(v > 100 for v in clean.values()):
        raise ValueError("each target must be between 0 and 100 (%)")
    if clean and abs(sum(clean.values()) - 100) > 0.5:
        raise ValueError(f"targets must add up to 100%; they add up to {sum(clean.values()):g}%")
    path = _path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(clean, indent=1, ensure_ascii=False), encoding="utf-8")
    return clean


# Current vs target weight per holding, from the latest holdings snapshot. `band` = drift (in points) that counts
# as off target.
def drift(user_id: str, workspace_id: str | None = None, band: float = 5.0) -> dict:
    r = engine.run_metric(user_id, MetricQuery(metrics=["portfolio_value"], dimensions=["symbol_or_scheme"], time="today"),
                          workspace_id=workspace_id)
    values = {row["symbol_or_scheme"]: float(row["portfolio_value"] or 0) for row in r.rows}
    total = sum(values.values())
    if not total:
        raise engine.NotFound("no holdings with a value: upload a holdings export or CAS first")
    goal = targets(user_id)
    rows = []
    for name in sorted(values.keys() | goal.keys(), key=lambda n: -values.get(n, 0)):
        value, current = values.get(name, 0.0), 100 * values.get(name, 0.0) / total
        target = goal.get(name)
        rows.append({"holding": name, "value": round(value, 2), "current_pct": round(current, 2), "target_pct": target,
                     "drift_pts": None if target is None else round(current - target, 2),
                     "change_to_target": None if target is None else round(target / 100 * total - value, 2),
                     "off_target": target is not None and abs(current - target) > band})
    warnings = list(r.warnings)
    if not goal:
        warnings.append("no target mix set yet: set one to see drift")
    elif missing := [n for n in values if n not in goal]:
        warnings.append(f"{len(missing)} holding(s) have no target: {', '.join(missing[:5])}")
    return {"total_value": round(total, 2), "band_pts": band, "rows": rows, "warnings": warnings,
            "provenance": r.provenance.model_dump(), "label": LABEL}


# The trades your own targets imply, checked against your own limits. positions: [{ticker, shares, price, sector}];
# targets: {ticker: pct}, the rest stays cash; limits: max_position_pct, max_sector_pct, min_cash_pct, min_trade
# (trades smaller than this are skipped). Breaches are reported, never fixed: changing targets is the user's call.
def plan_trades(positions: list[dict], cash: float, targets: dict[str, float], limits: dict) -> dict:
    if any(v < 0 for v in targets.values()) or sum(targets.values()) > 100.0001:
        raise ValueError(f"targets must be 0-100% each and add up to 100% or less; they add up to {sum(targets.values()):g}%")
    book = {p["ticker"].upper(): p for p in positions}
    if missing := [t for t in targets if t.upper() not in book]:
        raise ValueError(f"add a price for {', '.join(missing)} before giving it a target")
    value = {t: float(p["shares"]) * float(p["price"]) for t, p in book.items()}
    total = sum(value.values()) + cash
    if total <= 0:
        raise ValueError("the book has no value")
    goal = {t.upper(): v for t, v in targets.items()}
    rows, after = [], {}
    for t, p in book.items():
        want = goal.get(t, 100 * value[t] / total)  # no target: leave it where it is
        gap = want / 100 * total - value[t]
        skipped = 0 < abs(gap) < limits.get("min_trade", 0)
        trade = 0.0 if skipped else gap
        after[t] = value[t] + trade
        rows.append({"ticker": t, "sector": p.get("sector") or "Unknown", "value": round(value[t], 2),
                     "weight_pct": round(100 * value[t] / total, 2), "target_pct": goal.get(t),
                     "trade": round(trade, 2), "shares": round(trade / float(p["price"]), 4) if float(p["price"]) else None,
                     "skipped_small": skipped, "weight_after_pct": round(100 * after[t] / total, 2)})
    cash_after = total - sum(after.values())
    sectors: dict[str, list[float]] = {}
    for r in rows:
        s = sectors.setdefault(r["sector"], [0.0, 0.0])
        s[0] += r["weight_pct"]
        s[1] += r["weight_after_pct"]
    breaches = []
    if (cap := limits.get("max_position_pct")) is not None:
        breaches += [f"{r['ticker']} would be {r['weight_after_pct']}% (limit {cap:g}%)" for r in rows if r["weight_after_pct"] > cap]
    if (cap := limits.get("max_sector_pct")) is not None:
        breaches += [f"{s} would be {round(v[1], 2)}% (limit {cap:g}%)" for s, v in sectors.items() if v[1] > cap]
    if cash_after < -0.005:
        breaches.append(f"the trades need {round(-cash_after, 2)} more cash than the book holds")
    elif (floor := limits.get("min_cash_pct")) is not None and 100 * cash_after / total < floor:
        breaches.append(f"cash would be {round(100 * cash_after / total, 2)}% (minimum {floor:g}%)")
    return {"total_value": round(total, 2), "rows": rows, "cash": round(cash, 2), "cash_after": round(cash_after, 2),
            "cash_after_pct": round(100 * cash_after / total, 2),
            "sectors": [{"sector": s, "before_pct": round(v[0], 2), "after_pct": round(v[1], 2)} for s, v in sorted(sectors.items())],
            "sold_pct": round(100 * -sum(r["trade"] for r in rows if r["trade"] < 0) / total, 2),
            "breaches": breaches, "label": "Arithmetic on the targets and limits you set, not investment advice."}
