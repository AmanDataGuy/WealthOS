"""
# risk.py - how risky today's holdings would have been on past prices

Takes the latest holdings snapshot (the same values `rebalance.drift` uses) and the workspace's `prices` table
(NSE closes and mutual fund NAVs from `data/prices.py`, or an uploaded prices CSV). Today's weights are applied to
past daily returns, so every number here is **backward-looking, not a forecast**.

- Volatility (yearly), the worst fall from a peak (max drawdown) with its dates, the worst calendar month.
- 1-day historical VaR and CVaR at 95%: the loss only 5% of past days were worse than, and the average of those days,
  as % and as rupees of the priced holdings' value.
- Each holding's share of the portfolio's risk (its contribution to the variance) and the 3 most correlated pairs.
- The same shares by sector, from the Nifty 500 industry list (`data/sectors.py`); holdings not on it are "Unknown".
- A holding is matched to prices by name (case-insensitive). Holdings without at least `MIN_DAYS` daily returns are
  left out and listed with their share of value; too little shared history overall is refused.
- Used by `GET /strategy/{user}/{workspace}/risk`, the MCP tool `portfolio_risk` and the Rebalance page.
"""
import math

import numpy as np
import pandas as pd

from app.data import engine
from app.data.engine import MetricQuery
from app.data.prices import nse_enabled
from app.data.sectors import sector_map
from app.features.strategy import TRADING_DAYS, load_prices

LABEL = "Backward-looking: today's holdings applied to past prices; not a forecast."
MIN_DAYS = 60  # about three months of trading days: fewer makes a 5% tail (VaR) rest on two or three days


# The loss (as a positive fraction) only 5% of the days were worse than, and the average loss on those days.
def var_cvar(returns: pd.Series, level: float = 0.95) -> tuple[float, float]:
    cut = float(np.quantile(returns, 1 - level))
    return -cut, -float(returns[returns <= cut].mean())


# The deepest fall from a running peak of a value path: (fall as a negative fraction, peak date, trough date,
# date it got back to the peak or None).
def drawdown(path: pd.Series) -> tuple[float, pd.Timestamp, pd.Timestamp, pd.Timestamp | None]:
    dd = path / path.cummax() - 1
    trough = dd.idxmin()
    peak = path[:trough].idxmax()
    back = path[trough:][path[trough:] >= path[peak]]
    return float(dd.min()), peak, trough, back.index[0] if len(back) else None


# The risk report for the user's current holdings (see the file header).
def report(user_id: str, workspace_id: str | None = None) -> dict:
    r = engine.run_metric(user_id, MetricQuery(metrics=["portfolio_value"], dimensions=["symbol_or_scheme"], time="today"),
                          workspace_id=workspace_id)
    values = {row["symbol_or_scheme"]: float(row["portfolio_value"] or 0) for row in r.rows if (row["portfolio_value"] or 0) > 0}
    total = sum(values.values())
    if not total:
        raise engine.NotFound("no holdings with a value: upload a holdings export or CAS first")

    con = engine.open_readonly(user_id, workspace_id)
    try:
        have = ({row["symbol"]: int(row["n"]) for row in engine._execute(
            con, "SELECT symbol, COUNT(DISTINCT ts) AS n FROM prices WHERE close IS NOT NULL GROUP BY symbol")}
            if "prices" in engine.schema(con) else {})
    finally:
        con.close()
    by_upper = {s.upper(): s for s in have}
    match = {h: by_upper[h.strip().upper()] for h in values if h.strip().upper() in by_upper}
    left_out = [{"holding": h, "value": round(v, 2), "share_pct": round(100 * v / total, 2),
                 "reason": "no price history" if h not in match else f"only {have[match[h]]} days of prices"}
                for h, v in values.items() if h not in match or have[match[h]] <= MIN_DAYS]
    used = [h for h in values if h in match and have[match[h]] > MIN_DAYS]
    if not used:
        raise ValueError(f"too little price history: no holding has more than {MIN_DAYS} days of prices in this "
                         "workspace. Add prices for your holdings on the Strategy page (NSE symbols or mutual fund scheme codes)")

    close = load_prices(user_id, workspace_id, [match[h] for h in used], None)[0]
    close.columns = used
    returns = close.dropna().pct_change().dropna()  # only days every used holding has a price
    if len(returns) < MIN_DAYS:
        raise ValueError(f"too little price history: the holdings share only {len(returns)} days of returns, "
                         f"at least {MIN_DAYS} are needed")

    priced = sum(values[h] for h in used)
    w = pd.Series({h: values[h] / priced for h in used})
    daily = returns @ w  # today's weights, kept every day
    path = pd.concat([pd.Series([1.0], index=[close.dropna().index[0]]), (1 + daily).cumprod()])
    fall, peak, trough, back = drawdown(path)
    monthly = (1 + daily).groupby(daily.index.to_period("M")).prod() - 1
    var, cvar = var_cvar(daily)
    cov = returns.cov()
    share = w * (cov @ w) / float(w @ cov @ w) if float(w @ cov @ w) > 0 else w * math.nan
    corr = returns.corr()
    pairs = sorted(((corr.loc[a, b], a, b) for i, a in enumerate(used) for b in used[i + 1:]), key=lambda p: -p[0])

    warnings = list(r.warnings)
    known = sector_map()
    sectors = None
    if not known:
        why = "NSE data is switched off on this server" if not nse_enabled() else "the Nifty 500 list could not be downloaded"
        warnings.append(f"sector split skipped: the holdings carry no sector ({why})")
    else:
        split: dict[str, list[float]] = {}
        for h in used:
            s = split.setdefault(known.get(h.strip().upper(), "Unknown"), [0.0, 0.0])
            s[0] += float(w[h])
            s[1] += float(share[h])
        sectors = [{"sector": k, "value_pct": round(100 * v, 2),
                    "risk_share_pct": None if math.isnan(rs) else round(100 * rs, 2)}
                   for k, (v, rs) in sorted(split.items(), key=lambda kv: -kv[1][0])]
        unknown = sum(h.strip().upper() not in known for h in used)
        if unknown:
            warnings.append(f"{unknown} holding(s) not in the Nifty 500 industry list, grouped as Unknown")
    if left_out:
        warnings.append(f"{len(left_out)} holding(s), {round(100 * (total - priced) / total, 2)}% of the value, left out "
                        "for missing or short price history")
    return {"label": LABEL, "total_value": round(total, 2), "priced_value": round(priced, 2),
            "period": {"start": str(path.index[0].date()), "end": str(path.index[-1].date()), "days": len(daily)},
            "volatility_pct": round(100 * float(daily.std()) * math.sqrt(TRADING_DAYS), 2),
            "max_drawdown": {"pct": round(100 * fall, 2), "peak": str(peak.date()), "trough": str(trough.date()),
                             "recovered": str(back.date()) if back is not None else None},
            "worst_month": {"month": str(monthly.idxmin()), "return_pct": round(100 * float(monthly.min()), 2)},
            "var_95_1d": {"pct": round(100 * var, 2), "inr": round(var * priced, 2)},
            "cvar_95_1d": {"pct": round(100 * cvar, 2), "inr": round(cvar * priced, 2)},
            "holdings": [{"holding": h, "value": round(values[h], 2), "weight_pct": round(100 * w[h], 2),
                          "risk_share_pct": None if math.isnan(share[h]) else round(100 * float(share[h]), 2)} for h in used],
            "sectors": sectors,
            "top_correlations": [{"a": a, "b": b, "correlation": round(float(c), 3)} for c, a, b in pairs[:3]],
            "left_out": left_out, "warnings": warnings, "provenance": r.provenance.model_dump()}
