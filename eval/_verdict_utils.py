# eval/_verdict_utils.py
"""
Shared verdict/risk-score extraction — used by both DeepEval (Phase 2) and
reliability eval (Phase 3), which both need to parse the same memo output.
"""

import re


def extract_verdict(memo: str) -> str | None:
    """
    Parse BUY / HOLD / AVOID from a memo string.

    Only scans the first 300 chars (the Executive Summary, where the writer
    agent is instructed to lead with the verdict — same window guardrails/
    validators.py's validate_memo() already uses). Scanning the full memo
    body risks matching an incidental "buy" mentioned deep in nuanced prose
    (e.g. "a small allocation to buy" inside an overall AVOID verdict).
    """
    head = memo[:300]
    # Bold marker, e.g. "**BUY**" or "**BUY (Systematic)**" — word can have trailing text
    match = re.search(r"\*\*(BUY|HOLD|AVOID)\b", head)
    if match:
        return match.group(1)
    match = re.search(r"Final Verdict[:\s*#\-]+([A-Z]+)", head)
    if match and match.group(1) in ("BUY", "HOLD", "AVOID"):
        return match.group(1)
    for word in ("AVOID", "HOLD", "BUY"):
        if re.search(rf"\b{word}\b", head):
            return word
    return None


def extract_risk_score(context: str) -> int | None:
    """Parse 'risk_score: N' from a context string."""
    match = re.search(r"risk_score[:\s]+(\d+)", context)
    return int(match.group(1)) if match else None
