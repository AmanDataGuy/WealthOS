# guardrails/validators.py
"""
## WealthOS — Agent Output Validators

Simple validation functions that run between agents in the LangGraph pipeline.
Each function takes a dict, checks it, and either returns it clean or raises
a clear error message that gets logged to state.

No complex framework needed — just Python with clear error messages.
"""

from typing import Optional


# --- Risk Report validator ----------------------------------------------------
# Called after risk_node, before writer_node gets the data.

def validate_risk_report(report: Optional[dict]) -> tuple[bool, str]:
    """
    Validates that the Risk Agent output is sane before Writer Agent sees it.

    Returns (is_valid, error_message).
    If valid, error_message is an empty string.

    ## Checks:
    - risk_score is an integer between 1 and 10
    - recommendation is one of Buy / Hold / Avoid
    - risk_factors list has between 1 and 5 items
    - final_verdict is a non-empty string
    """
    if not report:
        return False, "risk_report is None — risk_node may have failed"

    # Check risk_score
    score = report.get("risk_score")
    if score is None:
        return False, "risk_score is missing from risk_report"
    try:
        score = int(score)
    except (TypeError, ValueError):
        return False, f"risk_score must be an integer, got: {score!r}"
    if not (1 <= score <= 10):
        return False, f"risk_score out of range: {score} (must be 1–10)"

    # Check recommendation
    rec = report.get("recommendation", "")
    if rec not in ("Buy", "Hold", "Avoid"):
        return False, f"recommendation must be Buy/Hold/Avoid, got: {rec!r}"

    # Check risk_factors
    factors = report.get("risk_factors", [])
    if not isinstance(factors, list):
        return False, f"risk_factors must be a list, got: {type(factors).__name__}"
    if not (1 <= len(factors) <= 5):
        return False, f"risk_factors should have 1–5 items, got {len(factors)}"

    # Check final_verdict
    verdict = report.get("final_verdict", "")
    if not verdict or len(str(verdict).strip()) < 10:
        return False, "final_verdict is missing or too short"

    return True, ""


# --- Financial Snapshot validator ---------------------------------------------
# Called after data_node.

def validate_financial_snapshot(snapshot: Optional[dict]) -> tuple[bool, str]:
    """
    Checks that the Data Agent returned usable financial data.

    ## Checks:
    - ticker is present
    - at least one of: income_statement, valuation, cash_flow is populated
    - current_price is a positive number if present
    """
    if not snapshot:
        return False, "financial_snapshot is None — data_node may have failed"

    ticker = snapshot.get("ticker")
    if not ticker:
        return False, "ticker is missing from financial_snapshot"

    # At least one data section must exist
    has_data = any([
        snapshot.get("income_statement"),
        snapshot.get("valuation"),
        snapshot.get("cash_flow"),
    ])
    if not has_data:
        return False, f"financial_snapshot for {ticker} has no income, valuation, or cash flow data"

    # Price sanity check
    val = snapshot.get("valuation") or {}
    price = val.get("current_price")
    if price is not None:
        try:
            price = float(price)
        except (TypeError, ValueError):
            return False, f"current_price is not a number: {price!r}"
        if price <= 0:
            return False, f"current_price must be positive, got {price}"

    return True, ""


# --- Memo validator -----------------------------------------------------------
# Called after writer_node to check the final output before returning to API.

REQUIRED_SECTIONS = [
    "Executive Summary",
    "Financial Snapshot",
    "Valuation Analysis",
    "Risk Assessment",
    "Portfolio Impact",
    "Personal Finance Fit",
    "Final Verdict",
]

def validate_memo(memo: Optional[str]) -> tuple[bool, str]:
    """
    Checks that the Writer Agent produced a complete memo.

    ## Checks:
    - memo is non-empty
    - all 7 required sections are present
    - verdict word (BUY/HOLD/AVOID) appears in first 300 characters
    """
    if not memo or len(memo.strip()) < 100:
        return False, "memo is empty or too short"

    missing = [s for s in REQUIRED_SECTIONS if s not in memo]
    if missing:
        return False, f"memo is missing sections: {', '.join(missing)}"

    verdict_present = any(v in memo[:300].upper() for v in ("BUY", "HOLD", "AVOID"))
    if not verdict_present:
        return False, "memo does not lead with a verdict (BUY/HOLD/AVOID) in the first 300 characters"

    return True, ""


# --- Convenience wrapper ------------------------------------------------------
# Called in graph/nodes.py to validate all outputs in one place.

def validate_all(state: dict) -> tuple[bool, str]:
    """
    Runs all relevant validators against the current graph state.
    Returns (all_valid, first_error_found).
    Skips validators for fields that are None (agent may not have run yet).
    """
    checks = [
        (state.get("financial_snapshot"), validate_financial_snapshot),
        (state.get("risk_report"),        validate_risk_report),
    ]

    for data, validator in checks:
        if data is not None:
            valid, error = validator(data)
            if not valid:
                return False, error

    return True, ""