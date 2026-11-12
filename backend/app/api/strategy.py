"""
# strategy.py - free daily prices, the strategy lab (backtests) and rebalancing; educational, never advice

- Routes under `/prices`, `/strategy` and `/rebalance`.
"""
import asyncio
from datetime import date
from typing import Literal, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from app.main import USER, USER_WS

router = APIRouter()


# ── Free daily prices: NSE stocks / ETFs and mutual fund NAVs ─────────────────────────────────────────────

# Which prices to fetch and from when.
class PriceBody(BaseModel):
    source: Literal["nse", "mf"]   # NSE trading symbols, or mutual fund scheme codes (from /search)
    symbols: list[str]
    start: Optional[date] = None   # default: one year ago


@router.get("/prices/{user_id}/{workspace_id}", dependencies=USER_WS)
def price_sources(user_id: str, workspace_id: str):
    """The workspace's price sources and how far they go."""
    from app.data.prices import list_sources
    return list_sources(user_id, workspace_id)


@router.post("/prices/{user_id}/{workspace_id}", dependencies=USER_WS)
def price_add(user_id: str, workspace_id: str, body: PriceBody):
    """Fetch daily prices into the `prices` table; they are updated every evening."""
    from app.data.prices import add
    return add(user_id, workspace_id, body.source, body.symbols, body.start)


@router.post("/prices/{user_id}/{workspace_id}/refresh", dependencies=USER_WS)
def price_refresh(user_id: str, workspace_id: str):
    """Bring every price source up to today now."""
    from app.data.prices import refresh
    return refresh(user_id, workspace_id)


@router.get("/prices/{user_id}/{workspace_id}/search", dependencies=USER_WS)
def price_search_mf(user_id: str, workspace_id: str, q: str):
    """Mutual fund schemes whose name matches."""
    from app.data.prices import search_mf
    return search_mf(q)


@router.delete("/prices/{user_id}/{workspace_id}/{source_id}", dependencies=USER_WS)
def price_remove(user_id: str, workspace_id: str, source_id: str):
    """Stop a price source and remove its prices."""
    from app.data.prices import remove
    remove(user_id, workspace_id, source_id)
    return {"deleted": source_id}


# ── Strategy lab and rebalancing (both educational, never advice) ───────────────────────────────────────────────────

# A trading idea in plain English.
class StrategyTextBody(BaseModel):
    text: str  # e.g. "buy NIFTYBEES when the 50-day average crosses above the 200-day"


# A checked trading rule to test.
class StrategyRunBody(BaseModel):
    spec: dict                          # the checked rule from /parse (or written by hand)
    generated_by: Optional[str] = None  # the AI model that wrote it (adds a "it may know the future" warning)


@router.post("/strategy/{user_id}/{workspace_id}/parse", dependencies=USER_WS)
async def strategy_parse(user_id: str, workspace_id: str, body: StrategyTextBody):
    """Turn a plain-English idea into a checked rule and read it back; nothing runs yet."""
    from app.features.strategy import parse
    return await parse(user_id, workspace_id, body.text)


@router.post("/strategy/{user_id}/{workspace_id}/run", dependencies=USER_WS)
def strategy_run(user_id: str, workspace_id: str, body: StrategyRunBody):
    """Test a rule on the workspace's past prices against buy-and-hold."""
    from app.features.strategy import run
    return run(user_id, workspace_id, body.spec, body.generated_by)


@router.get("/strategy/{user_id}/runs", dependencies=USER)
def strategy_runs(user_id: str):
    """Earlier runs, newest first."""
    from app.features.strategy import list_runs
    return list_runs(user_id)


@router.get("/strategy/{user_id}/runs/{run_id}", dependencies=USER)
def strategy_get_run(user_id: str, run_id: str):
    """One earlier run in full."""
    from app.features.strategy import get_run
    return get_run(user_id, run_id)


@router.post("/strategy/{user_id}/{workspace_id}/python", dependencies=USER_WS)
async def strategy_python(user_id: str, workspace_id: str, file: UploadFile = File(...), symbols: str = Form(...),
                          capital: float = Form(100000), start: Optional[date] = Form(None), end: Optional[date] = Form(None)):
    """Backtest an uploaded .py file defining weights(close) (features/sandbox.py says what it may contain)."""
    from app.features.strategy import run_python
    code = (await file.read(100_001)).decode("utf-8", "replace")
    if len(code) > 100_000:
        raise HTTPException(400, "the strategy file is too large (100 KB at most)")
    return await asyncio.to_thread(run_python, user_id, workspace_id, code, symbols.split(","), capital, start=start, end=end)


# Target weights per holding (% of the portfolio, adding up to 100).
class TargetsBody(BaseModel):
    targets: dict[str, float]


@router.get("/rebalance/{user_id}/{workspace_id}", dependencies=USER_WS)
def rebalance_drift(user_id: str, workspace_id: str):
    """Each holding's current vs target weight, its drift and the change that would bring it back to target."""
    from app.features.rebalance import drift
    return drift(user_id, workspace_id)


@router.put("/rebalance/{user_id}/targets", dependencies=USER)
def rebalance_targets(user_id: str, body: TargetsBody):
    """Save the target mix."""
    from app.features.rebalance import save_targets
    return save_targets(user_id, body.targets)
