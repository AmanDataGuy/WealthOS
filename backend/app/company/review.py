"""
# review.py - earnings review: your locked forecast for a quarter against what the company reported

The loop a portfolio manager runs each quarter, as arithmetic on the user's own numbers:

1. **Lock a forecast** (`lock`): revenue, margins and diluted EPS for one company quarter, optional consensus EPS,
   and kill rules ("operating margin below 15% for 2 quarters"). Append-only: each lock gets a time and a SHA-256 of
   its content, and nothing is ever edited.
2. **Review** (`review`): the quarter's actuals from the 10-Q (`quarters.py`) against the forecast, one variance per
   driver; material when diluted EPS is 5% or more off, or the release carries guidance. **No hindsight:** only a
   forecast locked before the 10-Q's filing date is used; later ones are listed as not used. Kill rules are checked
   in code. One-off items and guidance come from the earnings-release reader, verified quotes only.
3. **Verdict** (`record_verdict`): the user's own call, intact / weakened / broken, with a note.
4. **Accuracy** (`accuracy`): mean absolute and mean signed EPS error and the share within 5%, over reviewed quarters.

The code never says what to buy or sell: it reports variances, rule hits and the user's verdicts.

Files: `storage/users/<id>/company/forecasts.jsonl` and `verdicts.jsonl`. Used by the `/company/...` API routes.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.company import quarters
from app.data import engine

MATERIAL_EPS_PCT = 5.0
MATCH_DAYS = 20  # a forecast's quarter end matches a reported quarter ending within this many days
HISTORY = ("revenue_yoy_pct", "gross_margin_pct", "operating_margin_pct", "eps_diluted")
VERSUS = ("eps_vs_forecast_pct", "revenue_vs_forecast_pct")
LABEL = "Arithmetic on the forecasts and rules you set, not investment advice."


# A condition that would break the thesis: `metric op value`, true for `quarters` reported quarters in a row.
class KillRule(BaseModel):
    metric: Literal["revenue_yoy_pct", "gross_margin_pct", "operating_margin_pct", "eps_diluted",
                    "eps_vs_forecast_pct", "revenue_vs_forecast_pct"]
    op: Literal["<", "<=", ">", ">="]
    value: float
    quarters: int = Field(1, ge=1, le=4)


# What the user believes about one company quarter, before the results.
class Forecast(BaseModel):
    ticker: str = Field(min_length=1, max_length=10, pattern=r"^[A-Za-z.\-]+$")
    quarter_end: date  # the quarter's last day as the company reports it (within 20 days is fine)
    revenue_musd: float | None = None
    gross_margin_pct: float | None = Field(None, ge=-100, le=100)
    operating_margin_pct: float | None = Field(None, ge=-1000, le=100)
    eps_diluted: float | None = None
    consensus_eps: float | None = None
    kill_rules: list[KillRule] = Field(default_factory=list, max_length=5)
    note: str = Field("", max_length=500)

    # At least one number to compare.
    @model_validator(mode="after")
    def _something(self):
        if all(v is None for v in (self.revenue_musd, self.gross_margin_pct, self.operating_margin_pct, self.eps_diluted)):
            raise ValueError("give at least one of revenue, gross margin, operating margin or EPS")
        self.ticker = self.ticker.upper()
        return self


# The user's own call after a review.
class Verdict(BaseModel):
    forecast_id: str = Field(min_length=1, max_length=80)
    verdict: Literal["intact", "weakened", "broken"]
    note: str = Field("", max_length=500)


def _file(user_id: str, name: str):
    return engine.user_dir(user_id) / "company" / name


def _read(user_id: str, name: str) -> list[dict]:
    path = _file(user_id, name)
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line] if path.exists() else []


def _append(user_id: str, name: str, row: dict) -> dict:
    path = _file(user_id, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, default=str) + "\n")
    return row


# The hash a locked forecast carries: any later change to its content shows.
def content_hash(row: dict) -> str:
    body = {k: v for k, v in row.items() if k not in ("id", "sha256")}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


# Lock a forecast now. Returns the stored row with its id, lock time and hash.
def lock(user_id: str, forecast: Forecast, now: datetime | None = None) -> dict:
    row = {**forecast.model_dump(mode="json"), "locked_at": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")}
    n = sum(f["ticker"] == forecast.ticker for f in _read(user_id, "forecasts.jsonl")) + 1
    return _append(user_id, "forecasts.jsonl", {"id": f"{forecast.ticker}-{forecast.quarter_end}-{n}", **row,
                                                "sha256": content_hash(row)})


# Every locked forecast, oldest first, each marked whether its hash still matches.
def forecasts(user_id: str, ticker: str | None = None) -> list[dict]:
    rows = [r for r in _read(user_id, "forecasts.jsonl") if not ticker or r["ticker"] == ticker.upper()]
    return [{**r, "intact": content_hash(r) == r.get("sha256")} for r in rows]


def record_verdict(user_id: str, v: Verdict) -> dict:
    if not any(f["id"] == v.forecast_id for f in _read(user_id, "forecasts.jsonl")):
        raise engine.NotFound("no such forecast")
    return _append(user_id, "verdicts.jsonl", {**v.model_dump(), "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})


def _pct_diff(actual, forecast):
    return None if actual is None or forecast in (None, 0) else round(100 * (actual - forecast) / abs(forecast), 2)


def _pts(actual, forecast):
    return None if actual is None or forecast is None else round(actual - forecast, 2)


_OPS = {"<": lambda a, b: a < b, "<=": lambda a, b: a <= b, ">": lambda a, b: a > b, ">=": lambda a, b: a >= b}


# Is the rule true for its last `quarters` reported quarters (history metrics) or for this quarter (vs forecast)?
def check_rule(rule: dict, history: list[dict], versus: dict) -> dict:
    if rule["metric"] in VERSUS:
        values = [versus.get(rule["metric"])]
    else:
        values = [q.get(rule["metric"]) for q in history[-rule["quarters"]:]] if len(history) >= rule["quarters"] else [None]
    hit = None if any(v is None for v in values) else all(_OPS[rule["op"]](v, rule["value"]) for v in values)
    return {**rule, "values": values, "hit": hit}


# Review one forecast quarter against the reported quarters `series` (oldest first). `release`: the reader's output.
def review_quarter(group: list[dict], series: list[dict], release: dict | None = None) -> dict:
    target = date.fromisoformat(group[0]["quarter_end"])
    idx = next((i for i, q in enumerate(series) if abs((date.fromisoformat(q["end"]) - target).days) <= MATCH_DAYS), None)
    if idx is None:
        return {"quarter_end": group[0]["quarter_end"], "status": "awaiting results",
                "forecasts": [f["id"] for f in group]}
    actual = series[idx]
    usable = [f for f in group if f["locked_at"][:10] < actual["filed"]]
    late = [f["id"] for f in group if f not in usable]
    if not usable:
        return {"quarter_end": actual["end"], "status": "no forecast locked before the results", "late": late,
                "actual": actual}
    f = usable[-1]
    drivers = [
        ("Revenue ($M)", f["revenue_musd"], actual["revenue"], _pct_diff(actual["revenue"], f["revenue_musd"]), "%"),
        ("Gross margin (%)", f["gross_margin_pct"], actual["gross_margin_pct"], _pts(actual["gross_margin_pct"], f["gross_margin_pct"]), "pts"),
        ("Operating margin (%)", f["operating_margin_pct"], actual["operating_margin_pct"],
         _pts(actual["operating_margin_pct"], f["operating_margin_pct"]), "pts"),
        ("Diluted EPS ($)", f["eps_diluted"], actual["eps_diluted"], _pct_diff(actual["eps_diluted"], f["eps_diluted"]), "%"),
    ]
    rows = [{"driver": d, "forecast": fc, "actual": a, "variance": v, "unit": u} for d, fc, a, v, u in drivers if fc is not None]
    if f.get("consensus_eps") is not None:
        rows.append({"driver": "Diluted EPS vs consensus ($)", "forecast": f["consensus_eps"], "actual": actual["eps_diluted"],
                     "variance": _pct_diff(actual["eps_diluted"], f["consensus_eps"]), "unit": "%"})
    versus = {"eps_vs_forecast_pct": _pct_diff(actual["eps_diluted"], f["eps_diluted"]),
              "revenue_vs_forecast_pct": _pct_diff(actual["revenue"], f["revenue_musd"])}
    items = [i for i in (release or {}).get("items", []) if i.get("verified")]
    flags = [i for i in items if i["kind"] in ("one_off", "guidance")]
    eps_var = versus["eps_vs_forecast_pct"]
    reasons = ([f"diluted EPS {eps_var:+.1f}% vs your forecast"] if eps_var is not None and abs(eps_var) >= MATERIAL_EPS_PCT else []) \
        + (["the release carries guidance"] if any(i["kind"] == "guidance" for i in flags) else [])
    return {"quarter_end": actual["end"], "status": "reviewed", "forecast": f, "late": late, "actual": actual,
            "variances": rows, "material": bool(reasons), "material_reasons": reasons,
            "kill_rules": [check_rule(r, series[: idx + 1], versus) for r in f["kill_rules"]],
            "release_flags": flags, "release_url": (release or {}).get("url"), "label": LABEL}


# Every forecast quarter of one company, newest first, against `series` from `quarters.series`.
def review(user_id: str, ticker: str, series: list[dict], release: dict | None = None) -> dict:
    groups: dict[str, list] = {}
    for f in forecasts(user_id, ticker):
        groups.setdefault(f["quarter_end"], []).append(f)
    verdicts = {v["forecast_id"]: v for v in _read(user_id, "verdicts.jsonl")}
    out = []
    for i, (_, group) in enumerate(sorted(groups.items(), reverse=True)):
        r = review_quarter(group, series, release if i == 0 else None)  # the latest release only fits the latest quarter
        if r.get("forecast"):
            r["verdict"] = verdicts.get(r["forecast"]["id"])
        out.append(r)
    return {"ticker": ticker.upper(), "reviews": out, "quarters": series, "label": LABEL}


# Forecast accuracy over reviewed quarters: EPS error as % of the forecast, actual minus forecast.
def accuracy(reviews: list[dict]) -> dict:
    errors = [r["variances"] for r in reviews if r.get("status") == "reviewed"]
    eps = [v["variance"] for rows in errors for v in rows if v["driver"] == "Diluted EPS ($)" and v["variance"] is not None]
    if not eps:
        return {"reviewed": 0}
    return {"reviewed": len(eps), "mean_abs_eps_error_pct": round(sum(map(abs, eps)) / len(eps), 2),
            "mean_signed_eps_error_pct": round(sum(eps) / len(eps), 2),
            "within_5pct": sum(abs(e) <= MATERIAL_EPS_PCT for e in eps)}
