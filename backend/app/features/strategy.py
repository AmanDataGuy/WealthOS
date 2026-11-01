"""
# strategy.py - the strategy lab: write a trading rule, see how it would have done on past prices

**Historical simulation, not advice.** Nothing here places orders.

## 1. A rule is a small tree of signals (`Signal`), never code
`{"op": "crossover", "args": [{"op": "sma", "args": [{"op": "close"}, 50]}, {"op": "sma", "args": [{"op": "close"}, 200]}]}`
= "the 50-day average crosses above the 200-day average". Allowed ops:

| op | args | meaning |
|---|---|---|
| `close` | - | daily close |
| `const` | number | a fixed number |
| `sma` / `ts_mean`, `ema`, `ts_std`, `rsi` | signal, days | average, exponential average, volatility, RSI (Wilder) |
| `delta`, `lag` | signal, days | change over n days; value n days ago (n >= 1, so never the future) |
| `rank` | signal | rank across the universe that day (0..1) |
| `gt`, `lt`, `crossover` | a, b | a above b; a below b; a crosses above b today |
| `and`, `or` | a, b | both / either |
| `mul` | a, b | a times b (e.g. 0.95 × the 20-day average, for "5% below it") |

Trees are at most 8 deep, windows 1..400 days, and unknown keys are refused (`extra="forbid"`).

## 2. The simulation (`run`)
- Daily bars from the workspace's `prices` table. Signals use data up to day t's **close**; the trade is filled at
  day t+1's **open** (close if there is no open column, with a warning). So a rule can never trade on what it hasn't seen.
- Holding: from an `entry` day until an `exit` day (or, with no exit, only while the entry is true).
  `rebalance` weekly / monthly only acts on the first trading day of the week / month.
- Sizing: `equal_weight` splits the money across the stocks currently held; `fixed_amount` gives each stock
  1/N of the capital (the rest stays in cash).
- Costs: Indian equity charges per rupee traded (`COSTS`). Money not invested earns nothing.
- Results: CAGR, volatility, Sharpe (risk-free 6.5% a year unless `WEALTHOS_RISK_FREE_RATE` says otherwise), max
  drawdown, turnover, hit rate, time invested, and the same for **buy-and-hold** of the same stocks.
- Prices must be split-adjusted: a one-day move beyond -45% / +80% on prices not marked adjusted stops the run.

## 3. Run card (`strategy_runs/<id>.json`)
Spec hash, data fingerprint, engine id, which AI wrote the rule (if any), and the **deflated Sharpe ratio**: the
chance the Sharpe is real after counting how many rules this user already tried on the same stocks and period.
If an AI wrote the rule and the test period lies within the AI's training data, a warning says so.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import uuid
from datetime import date, datetime, timezone
from statistics import NormalDist
from typing import Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from app.data import engine

LABEL = "Historical simulation, not advice. Past performance does not predict future results."
ENGINE_VERSION = "1"
MAX_DEPTH = 8
MAX_WINDOW = 400
TRADING_DAYS = 252
RISK_FREE = float(os.getenv("WEALTHOS_RISK_FREE_RATE", "0.065"))  # yearly, INR (roughly the 91-day T-bill)
MODEL_CUTOFF = date.fromisoformat(os.getenv("LLM_KNOWLEDGE_CUTOFF", "2024-12-31"))  # what the planning AI may "know"

# Charges per rupee traded, NSE equity (2025 schedule). Re-verify against current exchange / SEBI circulars.
# STT 0.1% both sides (delivery) or 0.025% on sells (intraday); stamp duty 0.015% / 0.003% on buys;
# exchange fee 0.00297% and SEBI fee 0.0001% both sides, plus 18% GST on those two (and on brokerage).
# Brokerage: 0 for delivery (discount brokers), 0.03% for intraday. Not included: DP charges per sell (~₹15).
_FEES = (0.0000297 + 0.000001) * 1.18
COSTS = {
    "india_equity_delivery": {"buy": 0.001 + 0.00015 + _FEES, "sell": 0.001 + _FEES},
    "india_equity_intraday": {"buy": 0.00003 + _FEES + 0.0003 * 1.18, "sell": 0.00025 + _FEES + 0.0003 * 1.18},
    "none": {"buy": 0.0, "sell": 0.0},
}


# ── The rule ────────────────────────────────────────────────────────────────────────────

# One node of a signal tree.
class Signal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal["close", "sma", "ema", "rsi", "delta", "lag", "ts_mean", "ts_std", "rank", "crossover",
                "gt", "lt", "and", "or", "const", "mul"]
    args: list[Signal | float | int] = []


# A whole strategy. Unknown keys are an error, not silently ignored.
class StrategySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    universe: list[str] = Field(min_length=1, max_length=50)
    entry: Signal
    exit: Signal | None = None
    rebalance: Literal["daily", "weekly", "monthly"] = "daily"
    sizing: Literal["equal_weight", "fixed_amount"] = "equal_weight"
    capital: float = Field(100_000, gt=0)
    costs: Literal["india_equity_delivery", "india_equity_intraday", "none"] = "india_equity_delivery"
    start: date | None = None
    end: date | None = None


WINDOW_OPS = {"sma", "ema", "rsi", "delta", "lag", "ts_mean", "ts_std"}


# The value of a signal on a close-price panel (dates x symbols): a DataFrame, or a float for constants.
def evaluate(sig: Signal, close: pd.DataFrame, depth: int = 0):
    if depth >= MAX_DEPTH:
        raise ValueError(f"the rule is nested more than {MAX_DEPTH} levels deep")
    op, args = sig.op, sig.args

    # Fail unless the signal has exactly n arguments.
    def need(n: int):
        if len(args) != n:
            raise ValueError(f"'{op}' takes {n} argument(s), got {len(args)}")

    # Evaluate a nested signal, or read a plain number.
    def value(a):
        return evaluate(a, close, depth + 1) if isinstance(a, Signal) else float(a)

    # Like value, but the result must be a price table.
    def series(a) -> pd.DataFrame:
        v = value(a)
        if not isinstance(v, pd.DataFrame):
            raise ValueError(f"'{op}' needs a price series, not the number {v:g}")
        return v

    if op == "close":
        need(0)
        return close
    if op == "const":
        need(1)
        if isinstance(args[0], Signal):
            raise ValueError("'const' takes a number")
        return float(args[0])
    if op in WINDOW_OPS:
        need(2)
        n = args[1]
        if isinstance(n, Signal) or n != int(n) or not 1 <= n <= MAX_WINDOW:
            raise ValueError(f"'{op}' needs a whole number of days between 1 and {MAX_WINDOW}, got {n}")
        x, n = series(args[0]), int(n)
        if op in ("sma", "ts_mean"):
            return x.rolling(n).mean()
        if op == "ts_std":
            return x.rolling(n).std()
        if op == "ema":
            return x.ewm(span=n, adjust=False, min_periods=n).mean()
        if op == "delta":
            return x - x.shift(n)
        if op == "lag":
            return x.shift(n)
        change = x.diff()  # rsi, Wilder smoothing
        gain = change.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
        loss = (-change.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
        return (100 - 100 / (1 + gain / loss)).where(loss > 0, 100.0).where(gain.notna())
    if op == "rank":
        need(1)
        return series(args[0]).rank(axis=1, pct=True)
    need(2)  # gt / lt / crossover / and / or / mul
    if op == "mul":
        a, b = value(args[0]), value(args[1])
        return a * b
    a, b = _frame(value(args[0]), close), _frame(value(args[1]), close)
    if op == "gt":
        return a > b
    if op == "lt":
        return a < b
    if op == "crossover":
        return (a > b) & (a.shift(1) <= b.shift(1))
    a, b = a.fillna(0).astype(bool), b.fillna(0).astype(bool)
    return (a & b) if op == "and" else (a | b)


# A number spread over the panel's shape, so every op can work on frames.
def _frame(v, close: pd.DataFrame) -> pd.DataFrame:
    return v if isinstance(v, pd.DataFrame) else pd.DataFrame(v, index=close.index, columns=close.columns)


# The rule in plain English, written by code (so it says exactly what will run).
def describe_signal(sig: Signal | float | int) -> str:
    if not isinstance(sig, Signal):
        return f"{sig:g}"
    a, d = sig.args, describe_signal
    words = {
        "close": lambda: "the price", "const": lambda: d(a[0]),
        "sma": lambda: f"the {a[1]}-day average of {d(a[0])}", "ts_mean": lambda: f"the {a[1]}-day average of {d(a[0])}",
        "ema": lambda: f"the {a[1]}-day exponential average of {d(a[0])}", "ts_std": lambda: f"the {a[1]}-day volatility of {d(a[0])}",
        "rsi": lambda: f"the {a[1]}-day RSI of {d(a[0])}", "delta": lambda: f"the {a[1]}-day change in {d(a[0])}",
        "lag": lambda: f"{d(a[0])} {a[1]} days earlier", "rank": lambda: f"the rank of {d(a[0])} among the stocks",
        "gt": lambda: f"{d(a[0])} is above {d(a[1])}", "lt": lambda: f"{d(a[0])} is below {d(a[1])}",
        "crossover": lambda: f"{d(a[0])} crosses above {d(a[1])}",
        "and": lambda: f"({d(a[0])}) and ({d(a[1])})", "or": lambda: f"({d(a[0])}) or ({d(a[1])})",
        "mul": lambda: f"{d(a[1])} × {d(a[0])}" if not isinstance(a[1], Signal) else f"{d(a[0])} × {d(a[1])}",
    }
    try:
        return words[sig.op]()
    except IndexError:
        return f"{sig.op}(?)"


# 500000 -> "5,00,000" (Indian grouping: the last three digits, then pairs).
def _indian(n: float) -> str:
    s = str(int(round(n)))
    head, tail = s[:-3], s[-3:]
    while len(head) > 2:
        tail, head = f"{head[-2:]},{tail}", head[:-2]
    return f"{head},{tail}" if head else tail


# The whole strategy in plain English.
def describe(spec: StrategySpec) -> str:
    when = f"from {spec.start or 'the first price'} to {spec.end or 'the last price'}"
    exit_ = f"sell when {describe_signal(spec.exit)}" if spec.exit else "sell when that is no longer true"
    return (f"Buy {', '.join(spec.universe)} when {describe_signal(spec.entry)}; {exit_}. Checked {spec.rebalance}, "
            f"{spec.sizing.replace('_', ' ')}, ₹{_indian(spec.capital)} to start, "
            f"{'no charges' if spec.costs == 'none' else spec.costs.replace('_', ' ') + ' charges'}, {when}.")


# ── Prices ──────────────────────────────────────────────────────────────────────────────

# Close (and open) panels for the universe from the workspace's prices table, plus warnings and the data fingerprint.
def load_prices(user_id: str, workspace_id: str | None, universe: list[str], end: date | None):
    con = engine.open_readonly(user_id, workspace_id)
    try:
        cols = engine.schema(con).get("prices")
        if not cols:
            raise engine.NotFound("no price data in this workspace: upload daily prices (Date, Close ...) first")
        have = {r["symbol"] for r in engine._execute(con, "SELECT DISTINCT symbol FROM prices")}
        missing = sorted(set(universe) - have)
        if missing:
            raise ValueError(f"no prices for {missing}; available: {sorted(have)[:30]}")
        extra = [c for c in ("open", "adjusted") if c in cols]
        sql = (f"SELECT ts, symbol, close{''.join(', ' + c for c in extra)} FROM prices "
               f"WHERE symbol IN ({', '.join('?' for _ in universe)}) AND close IS NOT NULL"
               + (" AND ts <= ?" if end else "") + " ORDER BY ts")
        rows = pd.DataFrame(engine._execute(con, sql, list(universe) + ([end] if end else [])))
    finally:
        con.close()
    rows["ts"] = pd.to_datetime(rows["ts"])
    rows = rows.drop_duplicates(["ts", "symbol"], keep="last")
    close = rows.pivot(index="ts", columns="symbol", values="close")[list(universe)].astype(float)
    warnings = []
    opens = rows.pivot(index="ts", columns="symbol", values="open")[list(universe)].astype(float) if "open" in rows else None
    if opens is None or opens.isna().any().any():
        warnings.append("no opening prices for some days: trades are filled at the next day's close instead")
        opens = None
    adjusted = rows.groupby("symbol")["adjusted"].apply(lambda s: bool(s.fillna(False).all())).to_dict() if "adjusted" in rows else {}
    for sym in universe:
        if adjusted.get(sym, False):
            continue
        move = close[sym].dropna().pct_change()
        jump = move[(move < -0.45) | (move > 0.8)]
        if len(jump):
            raise ValueError(f"{sym} moved {jump.iloc[0]:+.0%} on {jump.index[0].date()}: that looks like a split in "
                             "unadjusted prices. Upload adjusted prices (an Adj Close column) and run again.")
    if not all(adjusted.get(s, False) for s in universe):
        warnings.append("prices are not marked as adjusted for splits and dividends; dividends are not counted")
    _, fingerprint = engine._provenance_data(user_id, {"prices"}, workspace_id)
    return close, opens, warnings, fingerprint


# ── The simulation ──────────────────────────────────────────────────────────────────────

# Simulate a strategy on price panels. Returns daily net returns, the exposure per stock, the amount traded per
# day (as a share of the money), and the buy-and-hold value per rupee, all on the same days.
def simulate(spec: StrategySpec, close: pd.DataFrame, opens: pd.DataFrame | None):
    return simulate_weights(target_weights(spec, close), close, opens, spec.costs, spec.start, spec.end)


# The weight a rule wants in each stock at each day's close (0..1, rows sum to at most 1).
def target_weights(spec: StrategySpec, close: pd.DataFrame) -> pd.DataFrame:
    entry = _frame(evaluate(spec.entry, close), close).fillna(0).astype(bool)
    if spec.exit is not None:
        exit_ = _frame(evaluate(spec.exit, close), close).fillna(0).astype(bool)
        held = pd.DataFrame(np.where(entry, 1.0, np.where(exit_, 0.0, np.nan)), index=close.index, columns=close.columns)
        held = held.ffill().fillna(0.0)
    else:
        held = entry.astype(float)
    if spec.rebalance != "daily":  # only act on the first trading day of each week / month
        period = close.index.to_period("W" if spec.rebalance == "weekly" else "M")
        first = pd.Series(~pd.Series(period).duplicated().to_numpy(), index=close.index)
        held = held.where(first, np.nan).ffill().fillna(0.0)
    if spec.sizing == "equal_weight":
        return held.div(held.sum(axis=1).replace(0, np.nan), axis=0).fillna(0.0)
    return held / len(spec.universe)


# Trade target weights (decided at a day's close, filled at the next day's open) with Indian costs.
def simulate_weights(weights: pd.DataFrame, close: pd.DataFrame, opens: pd.DataFrame | None, costs: str,
                     start: date | None = None, end: date | None = None):
    fill = opens if opens is not None else close
    ret = (fill.shift(-1) / fill - 1).fillna(0.0)  # day t's fill price to day t+1's fill price
    exposure = weights.shift(1).fillna(0.0)        # decided at day t-1's close, filled at day t
    start, end = pd.Timestamp(start or close.index[0]), pd.Timestamp(end or close.index[-1])
    window = (close.index >= start) & (close.index <= end)
    exposure, ret, fill = exposure[window].iloc[:-1], ret[window].iloc[:-1], fill[window]
    if len(exposure) < 2:
        raise ValueError("not enough price history in the chosen period (need at least 3 days)")
    trades = exposure.diff()
    trades.iloc[0] = exposure.iloc[0]  # ponytail: weights reset to target daily; drift trades aren't charged
    buys, sells = trades.clip(lower=0).sum(axis=1), (-trades).clip(lower=0).sum(axis=1)
    rate = COSTS[costs]
    net = (exposure * ret).sum(axis=1) - buys * rate["buy"] - sells * rate["sell"]
    growth = (fill / fill.iloc[0]).mean(axis=1).iloc[1:].to_numpy()  # buy-and-hold, equal amounts on day one
    bench = pd.Series(growth * (1 - rate["buy"]), index=net.index)
    return net, exposure, buys + sells, bench


# Growth, risk and drawdown of an equity curve that started at `start` (and its daily returns).
def stats(start: float, equity: pd.Series, daily: pd.Series, exposure: pd.DataFrame | None = None,
          traded: pd.Series | None = None) -> dict:
    years = len(daily) / TRADING_DAYS
    std = daily.std()
    path = pd.concat([pd.Series([start]), equity.reset_index(drop=True)])
    out = {"final_value": float(equity.iloc[-1]),
           "total_return_pct": 100 * (equity.iloc[-1] / start - 1),
           "cagr_pct": 100 * ((equity.iloc[-1] / start) ** (1 / years) - 1) if years > 0 and equity.iloc[-1] > 0 else None,
           "volatility_pct": 100 * std * math.sqrt(TRADING_DAYS),
           "sharpe": (daily.mean() - RISK_FREE / TRADING_DAYS) / std * math.sqrt(TRADING_DAYS) if std > 0 else None,
           "max_drawdown_pct": 100 * float((path / path.cummax() - 1).min())}
    if exposure is not None:
        invested = exposure.sum(axis=1) > 0
        out |= {"time_invested_pct": 100 * float(invested.mean()),
                "hit_rate_pct": 100 * float((daily[invested] > 0).mean()) if invested.any() else None,
                "turnover_per_year": float(traded.sum() / years) if years > 0 else None}
    return {k: None if v is None or not math.isfinite(v) else round(float(v), 2) for k, v in out.items()}


# ── Run card + deflated Sharpe ──────────────────────────────────────────────────────────

# Folder where saved strategy runs live.
def _runs_dir(user_id: str):
    return engine.user_dir(user_id) / "strategy_runs"


# Earlier runs of this user (newest first): their run cards.
def list_runs(user_id: str) -> list[dict]:
    cards = [json.loads(p.read_text(encoding="utf-8"))["run_card"] for p in _runs_dir(user_id).glob("*.json")]
    return sorted(cards, key=lambda c: c["created_at"], reverse=True)


# The probability that a (daily, excess) Sharpe ratio is really above zero after trying `n_trials` rules
# (Bailey & López de Prado's deflated Sharpe ratio). `trial_srs` = daily Sharpes of the earlier tries.
def deflated_sharpe(daily: pd.Series, n_trials: int, trial_srs: list[float]) -> float | None:
    t, std = len(daily), daily.std()
    if t < 3 or not std > 0:
        return None
    sr = daily.mean() / std
    skew, kurt = float(daily.skew()), float(daily.kurt()) + 3
    var_sr = max((1 - skew * sr + (kurt - 1) / 4 * sr ** 2) / (t - 1), 1e-12)
    sr0 = 0.0
    if n_trials > 1:
        spread = float(np.var(trial_srs, ddof=1)) if len(trial_srs) >= 2 else var_sr
        z, euler = NormalDist().inv_cdf, 0.5772156649
        sr0 = math.sqrt(spread) * ((1 - euler) * z(1 - 1 / n_trials) + euler * z(1 - 1 / (n_trials * math.e)))
    return round(NormalDist().cdf((sr - sr0) / math.sqrt(var_sr)), 4)


# ── What the API calls ──────────────────────────────────────────────────────────────────

# Backtest a spec on the workspace's prices, save the run, and return results + run card (always with the label).
def run(user_id: str, workspace_id: str | None, spec: StrategySpec | dict, generated_by: str | None = None) -> dict:
    spec = spec if isinstance(spec, StrategySpec) else StrategySpec(**spec)
    spec = spec.model_copy(update={"universe": list(dict.fromkeys(s.strip().upper() for s in spec.universe))})
    close, opens, warnings, fingerprint = load_prices(user_id, workspace_id, spec.universe, spec.end)
    sim = simulate(spec, close, opens)
    return _score_and_save(user_id, workspace_id, sim, spec.universe, spec.capital, warnings, fingerprint,
                           describe(spec), json.loads(spec.model_dump_json()), spec.model_dump_json(), generated_by)


# Backtest an uploaded Python strategy (see sandbox.py for what the file may contain and how it is isolated).
def run_python(user_id: str, workspace_id: str | None, code: str, universe: list[str], capital: float = 100000,
               costs: str = "india_equity_delivery", start: date | None = None, end: date | None = None) -> dict:
    from app.features import sandbox
    universe = list(dict.fromkeys(s.strip().upper() for s in universe if s.strip()))
    if not universe or len(universe) > 20:
        raise ValueError("give 1 to 20 symbols for the strategy to trade")
    if costs not in COSTS:
        raise ValueError(f"costs must be one of {sorted(COSTS)}")
    close, opens, warnings, fingerprint = load_prices(user_id, workspace_id, universe, end)
    weights = sandbox.run_weights(code, close)
    sim = simulate_weights(weights, close, opens, costs, start, end)
    spec = {"kind": "python", "universe": universe, "capital": capital, "costs": costs,
            "start": str(start) if start else None, "end": str(end) if end else None, "code": code}
    description = f"Your Python strategy on {', '.join(universe)}: target weights from weights(close), traded at the next open."
    return _score_and_save(user_id, workspace_id, sim, universe, capital, warnings, fingerprint, description, spec,
                           code + json.dumps(spec, sort_keys=True), None)


# Metrics, deflated Sharpe and run card for one simulation; saved under strategy_runs/<id>.json.
def _score_and_save(user_id, workspace_id, sim, universe, capital, warnings, fingerprint, description, spec_json,
                    spec_text, generated_by) -> dict:
    net, exposure, traded, bench = sim
    equity, bench_equity = capital * (1 + net).cumprod(), capital * bench
    period = {"start": str(net.index[0].date()), "end": str(net.index[-1].date())}

    same = [c for c in list_runs(user_id) if c["universe"] == sorted(universe) and c["period"] == period]
    excess = net - RISK_FREE / TRADING_DAYS
    dsr = deflated_sharpe(excess, len(same) + 1, [c["daily_sharpe"] for c in same if c.get("daily_sharpe") is not None])
    if generated_by and date.fromisoformat(period["start"]) <= MODEL_CUTOFF:
        warnings.append(f"the AI that wrote this rule learned from data up to {MODEL_CUTOFF}; on a period before that it "
                        "may already 'know' what happened, so the result can look better than it really is")
    if same:
        warnings.append(f"you have tested {len(same) + 1} rules on these stocks and dates; the deflated Sharpe allows for that")
    std = excess.std()
    card = {"id": uuid.uuid4().hex[:12], "workspace_id": workspace_id, "universe": sorted(universe), "period": period,
            "spec_hash": hashlib.sha256(spec_text.encode()).hexdigest()[:16], "data_version": fingerprint,
            "engine_version": ENGINE_VERSION, "generated_by": generated_by, "n_trials": len(same) + 1,
            "daily_sharpe": round(float(excess.mean() / std), 6) if std > 0 else None, "deflated_sharpe": dsr,
            "created_at": datetime.now(timezone.utc).isoformat()}  # microseconds, so "newest first" never ties
    step = max(1, len(equity) // 500)  # at most ~500 points for the chart
    curve = [{"date": str(d.date()), "strategy": round(float(e), 2), "buy_and_hold": round(float(b), 2)}
             for d, e, b in list(zip(equity.index, equity, bench_equity))[::step]]
    result = {"label": LABEL, "description": description, "spec": spec_json,
              "metrics": stats(capital, equity, net, exposure, traded),
              "buy_and_hold": stats(capital, bench_equity, bench_equity.pct_change().fillna(bench.iloc[0] - 1)),
              "equity_curve": curve, "warnings": warnings, "run_card": card}
    _runs_dir(user_id).mkdir(parents=True, exist_ok=True)
    (_runs_dir(user_id) / f"{card['id']}.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return result


# One saved run, in full.
def get_run(user_id: str, run_id: str) -> dict:
    path = _runs_dir(user_id) / f"{engine.safe_id(run_id)}.json"
    if not path.exists():
        raise engine.NotFound("run not found")
    return json.loads(path.read_text(encoding="utf-8"))


STRATEGY_WRITER = f"""You turn a plain-English trading idea into a StrategySpec (JSON). You never predict prices or
give advice; you only translate the rule. Signals are trees of ops:
close (no args); const [number]; sma|ts_mean|ema|ts_std|rsi|delta|lag [signal, days]; rank [signal];
gt|lt|crossover|and|or|mul [a, b] where a and b are signals or numbers (mul = a times b).
Examples: 50-day average crosses above 200-day = {{"op":"crossover","args":[{{"op":"sma","args":[{{"op":"close"}},50]}},{{"op":"sma","args":[{{"op":"close"}},200]}}]}};
RSI below 30 = {{"op":"lt","args":[{{"op":"rsi","args":[{{"op":"close"}},14]}},30]}};
price 5% below its 20-day average = {{"op":"lt","args":[{{"op":"close"}},{{"op":"mul","args":[{{"op":"sma","args":[{{"op":"close"}},20]}},0.95]}}]}}.
Rules:
- universe = exactly the stocks the user names (from "symbols"); use all symbols only if the user asks for all of them.
- "crosses back below" X = crossover with the two sides swapped. Windows are 1-{MAX_WINDOW} days; lag and delta only look back.
- Put a separate sell rule in "exit". Dates are YYYY-MM-DD. "5 lakh" = 500000, "1 crore" = 10000000."""


# Turn a plain-English idea into a checked spec + its plain-English reading (shown before anything runs).
async def parse(user_id: str, workspace_id: str | None, text: str) -> dict:
    import asyncio

    from app.ai.llm_client import GROQ_MODEL, SERVED_MODEL, call_json

    # The symbols that have prices in the workspace.
    def symbols() -> list[str]:
        con = engine.open_readonly(user_id, workspace_id)
        try:
            if "prices" not in engine.schema(con):
                raise engine.NotFound("no price data in this workspace: upload daily prices first")
            return sorted(r["symbol"] for r in engine._execute(con, "SELECT DISTINCT symbol FROM prices"))
        finally:
            con.close()

    have = await asyncio.to_thread(symbols)
    spec = await call_json(STRATEGY_WRITER, f"symbols: {have}\nIdea: {text.strip()[:1500]}", StrategySpec,
                           max_tokens=1200, user_id=user_id)
    if spec is None:
        raise ValueError("couldn't turn that into a rule; try saying when to buy and when to sell")
    named = [s for s in have if re.search(rf"(?<![A-Za-z0-9]){re.escape(s)}(?![A-Za-z0-9])", text, re.I)]
    spec = spec.model_copy(update={"universe": named or [s.strip().upper() for s in spec.universe]})  # the stocks named win
    unknown = sorted(set(spec.universe) - set(have))
    if unknown:
        raise ValueError(f"no prices for {unknown}; available: {have[:30]}")
    probe = pd.DataFrame({s: [1.0] for s in spec.universe})
    for sig in (spec.entry, spec.exit):
        if sig is not None:
            evaluate(sig, probe)  # argument / window checks before the rule is shown
    return {"spec": json.loads(spec.model_dump_json()), "description": describe(spec), "label": LABEL,
            "generated_by": SERVED_MODEL.get() or GROQ_MODEL}  # the fallback's name when Groq didn't answer
