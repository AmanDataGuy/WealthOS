"""
# company.py - company analyst: a US-listed company's SEC filings as a checked Excel model

- `POST /company/{user_id}/build` runs the pipeline (`app/company/pipeline.py`); the workbook route serves the result.
"""
import asyncio
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

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


@router.get("/company/{user_id}/runs/{run_id}/workbook.xlsx", dependencies=USER)
def company_workbook(user_id: str, run_id: str):
    """The Excel model of one run."""
    from app.company.pipeline import workbook_path
    return FileResponse(workbook_path(user_id, run_id), media_type=XLSX, filename=f"{Path(run_id).name}.xlsx")
