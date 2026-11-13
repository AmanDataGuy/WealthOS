"""
# company.py - company analyst: a US-listed company's SEC filings as a checked Excel model

- `POST /company/{user_id}/build` runs the pipeline (`app/company/pipeline.py`); the workbook route serves the result.
- `compare`: 2-6 companies side by side (`company/compare.py`).
- `forecasts`, `review/{ticker}`, `verdicts`: earnings review against your locked forecasts (`company/review.py`).
- `plan`: the trades your own targets imply, checked against your limits (`features/rebalance.plan_trades`).
"""
import asyncio
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.company.review import Forecast, Verdict

from app.main import USER, XLSX

router = APIRouter()
COMPANY_BUILD_SECONDS = 240


# ── Company analyst: a US-listed company's SEC filings as a checked Excel model ─────────────────────────────

# Which company to model.
class CompanyBody(BaseModel):
    query: str  # a ticker ("NVDA") or a name ("GE Vernova")


@router.post("/company/{user_id}/build", dependencies=USER)
async def company_build(user_id: str, body: CompanyBody):
    """Build the model: statements, ratios, DCF, release figures and tie-outs; or the companies to choose from."""
    from app.company import pipeline
    if not 1 <= len(body.query.strip()) <= 100:
        raise HTTPException(400, "type a company name or ticker")
    # shortcut: after the 240 s answer (408) the thread still finishes the step it is in (one SEC fetch or AI call),
    # then stops without saving; a job queue that reports progress is the upgrade
    deadline = time.monotonic() + COMPANY_BUILD_SECONDS
    return await asyncio.wait_for(asyncio.to_thread(pipeline.run, body.query, user_id, deadline),
                                  timeout=COMPANY_BUILD_SECONDS)


class CompareBody(BaseModel):
    tickers: list[str] = Field(min_length=2, max_length=6)
    prices: dict[str, float] = Field(default_factory=dict)  # optional share price per ticker, typed by the user


@router.post("/company/{user_id}/compare", dependencies=USER)
async def company_compare(user_id: str, body: CompareBody):
    """Two to six companies side by side from their SEC filings (no AI step)."""
    from app.company import compare
    if any(not 1 <= len(t.strip()) <= 100 for t in body.tickers):
        raise HTTPException(400, "type a ticker or name for each company")
    return await asyncio.wait_for(asyncio.to_thread(compare.compare, body.tickers, body.prices), timeout=COMPANY_BUILD_SECONDS)


@router.post("/company/{user_id}/forecasts", dependencies=USER)
def company_forecast_lock(user_id: str, body: Forecast):
    """Lock a forecast for one company quarter (append-only, hashed)."""
    from app.company import review
    return review.lock(user_id, body)


@router.get("/company/{user_id}/forecasts", dependencies=USER)
def company_forecasts(user_id: str):
    """Every locked forecast, oldest first."""
    from app.company import review
    return review.forecasts(user_id)


# Quarters from SEC, plus the latest earnings release read (the one AI step) when it came out after the newest
# forecast quarter ended. Runs in a thread: SEC fetches and the release reader block.
def _review(user_id: str, ticker: str, read_release: bool) -> dict:
    from app.company import quarters, releases, review, sec
    company = sec.resolve(ticker)
    if "candidates" in company:
        raise ValueError("type the exact ticker")
    filings = sec.filings(company["cik"])
    series = quarters.series(sec.company_facts(company["cik"]))
    mine = review.forecasts(user_id, company["ticker"])
    release = None
    if read_release and mine and filings["earnings"]:
        if filings["earnings"][0]["filingDate"] >= max(f["quarter_end"] for f in mine):
            release = releases.read(filings["earnings"][0], company["name"], user_id)
    out = review.review(user_id, company["ticker"], series, release)
    out["accuracy"] = review.accuracy(out["reviews"])
    return out


@router.get("/company/{user_id}/review/{ticker}", dependencies=USER)
async def company_review(user_id: str, ticker: str, release: bool = True):
    """Your locked forecasts for one company against its reported quarters."""
    return await asyncio.wait_for(asyncio.to_thread(_review, user_id, ticker, release), timeout=COMPANY_BUILD_SECONDS)


@router.post("/company/{user_id}/verdicts", dependencies=USER)
def company_verdict(user_id: str, body: Verdict):
    """Record your own thesis verdict on a reviewed forecast."""
    from app.company import review
    return review.record_verdict(user_id, body)


class Position(BaseModel):
    ticker: str = Field(min_length=1, max_length=10)
    shares: float = Field(ge=0)
    price: float = Field(gt=0)
    sector: str = Field("", max_length=60)


class PlanBody(BaseModel):
    positions: list[Position] = Field(min_length=1, max_length=50)
    cash: float = Field(0, ge=0)
    targets: dict[str, float] = Field(default_factory=dict)
    max_position_pct: float | None = Field(None, gt=0, le=100)
    max_sector_pct: float | None = Field(None, gt=0, le=100)
    min_cash_pct: float | None = Field(None, ge=0, le=100)
    min_trade: float = Field(0, ge=0)


@router.post("/company/{user_id}/plan", dependencies=USER)
def company_plan(user_id: str, body: PlanBody):
    """The trades your own target weights imply, checked against your own limits. Nothing is stored."""
    from app.features.rebalance import plan_trades
    limits = body.model_dump(include={"max_position_pct", "max_sector_pct", "min_cash_pct", "min_trade"})
    return plan_trades([p.model_dump() for p in body.positions], body.cash, body.targets, limits)


@router.get("/company/{user_id}/runs/{run_id}/workbook.xlsx", dependencies=USER)
def company_workbook(user_id: str, run_id: str):
    """The Excel model of one run."""
    from app.company.pipeline import workbook_path
    return FileResponse(workbook_path(user_id, run_id), media_type=XLSX, filename=f"{Path(run_id).name}.xlsx")
