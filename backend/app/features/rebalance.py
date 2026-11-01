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
