# harness/risk_policy.py
"""
Deterministic policy gate — runs after the Writer Agent, before a memo
reaches the user. The LLM proposes a verdict; this module has sole authority
to allow, deny, or escalate it. No prompt can talk its way past these checks
because they never touch an LLM — every branch below is a plain comparison
against numbers already computed earlier in the pipeline.

Three checks, each grounded in a field the pipeline already produces:
  1. A Buy sized against money the user doesn't have (invest_amount vs.
     Finance Agent's monthly_surplus).
  2. A Buy at high model-assessed risk that contradicts the user's own
     track record (Risk Agent's risk_score vs. user_risk_profiles history).
  3. A Buy built on data the Data Agent itself flagged as low-confidence.

A denial/escalation is a result, not an exception — callers render
`PolicyResult.reason` as a visible banner, they don't treat it as a failure.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class PolicyDecision(str, Enum):
    ALLOW    = "allow"
    DENY     = "deny"
    ESCALATE = "escalate"


@dataclass
class PolicyResult:
    decision: PolicyDecision
    reason:   Optional[str] = None

    @property
    def blocked(self) -> bool:
        return self.decision != PolicyDecision.ALLOW

    @classmethod
    def allow(cls) -> "PolicyResult":
        return cls(PolicyDecision.ALLOW)

    @classmethod
    def deny(cls, reason: str) -> "PolicyResult":
        return cls(PolicyDecision.DENY, reason)

    @classmethod
    def escalate(cls, reason: str) -> "PolicyResult":
        return cls(PolicyDecision.ESCALATE, reason)


# A Buy sized at more than this multiple of the user's monthly surplus gets
# denied outright — the memo would be telling someone to spend money that
# isn't there. Ratio, not a hard rupee cap, since surplus varies by user.
_MAX_SURPLUS_MULTIPLE = 3.0

# risk_score is 1-10 (see validation/validators.py). 8+ is "high risk" by
# the same threshold the Risk Agent's own prompt uses for its risk_grade.
_HIGH_RISK_SCORE = 8

# A user's historical avg_risk_score below this, with at least 3 prior
# analyses on record, means their track record reads conservative — a
# high-risk Buy against that pattern gets escalated, not auto-approved.
_CONSERVATIVE_AVG_RISK_SCORE = 4.0
_MIN_HISTORY_FOR_PROFILE_CHECK = 3


def validate_recommendation(
    verdict: str,
    risk_score: Optional[int],
    invest_amount: Optional[float],
    monthly_surplus: Optional[float],
    data_confidence: Optional[str] = None,
    user_risk_profile: Optional[dict] = None,
) -> PolicyResult:
    """
    Args mirror what graph/nodes.py already has in hand after writer_node:
    memo.verdict, memo.risk_score, state.invest_amount,
    personal_finance["monthly_surplus"], financial_snapshot["confidence"],
    and the same user_risk_profile dict risk_node/writer_node already fetch
    from Postgres user_risk_profiles.

    The low-confidence-data escalation applies to every verdict, not just
    Buy — was Buy-only, so a Hold or Avoid built on the exact same
    low-confidence data passed through unchecked. Bad data can cause real
    harm either direction: wrongly telling someone to avoid (or hold off on)
    a stock is a missed-opportunity harm, not a lesser one than a bad Buy.
    The surplus-sizing and risk-vs-track-record checks stay Buy-only below —
    they're only meaningful when money is actually being deployed, so a
    Hold/Avoid has nothing for them to contradict.
    """
    if data_confidence == "low":
        return PolicyResult.escalate(
            f"{verdict} verdict built on low-confidence financial data — "
            "route to human review before acting on this memo."
        )

    if verdict != "Buy":
        return PolicyResult.allow()

    if invest_amount and monthly_surplus and monthly_surplus > 0:
        if invest_amount > monthly_surplus * _MAX_SURPLUS_MULTIPLE:
            return PolicyResult.deny(
                f"Suggested investment (₹{invest_amount:,.0f}) exceeds "
                f"{_MAX_SURPLUS_MULTIPLE:.0f}× the user's computed monthly "
                f"surplus (₹{monthly_surplus:,.0f}) — recommendation withheld."
            )

    if risk_score is not None and risk_score >= _HIGH_RISK_SCORE and user_risk_profile:
        total = user_risk_profile.get("total_analyses") or 0
        avg_r = user_risk_profile.get("avg_risk_score")
        if total >= _MIN_HISTORY_FOR_PROFILE_CHECK and avg_r is not None and avg_r < _CONSERVATIVE_AVG_RISK_SCORE:
            return PolicyResult.escalate(
                f"High-risk Buy (risk_score={risk_score}/10) contradicts this "
                f"user's {total}-analysis history (avg risk_score={avg_r:.1f}) — "
                f"route to human review before acting on this memo."
            )

    return PolicyResult.allow()


def demo() -> None:
    """ponytail: smallest runnable check — one assert per branch."""
    # Hold/Avoid pass through untouched when data confidence is fine...
    assert validate_recommendation("Hold", 9, 100_000, 10_000).decision == PolicyDecision.ALLOW

    # ...but a Hold/Avoid built on low-confidence data escalates too, same as Buy.
    r = validate_recommendation("Avoid", 9, 100_000, 10_000, data_confidence="low")
    assert r.decision == PolicyDecision.ESCALATE, r

    # Buy sized way past surplus → deny.
    r = validate_recommendation("Buy", 3, invest_amount=100_000, monthly_surplus=10_000)
    assert r.decision == PolicyDecision.DENY, r

    # Buy within surplus, no other red flags → allow.
    r = validate_recommendation("Buy", 3, invest_amount=15_000, monthly_surplus=10_000)
    assert r.decision == PolicyDecision.ALLOW, r

    # Buy on low-confidence data → escalate.
    r = validate_recommendation("Buy", 3, invest_amount=5_000, monthly_surplus=10_000, data_confidence="low")
    assert r.decision == PolicyDecision.ESCALATE, r

    # High-risk Buy against a conservative track record → escalate.
    profile = {"total_analyses": 5, "avg_risk_score": 2.5}
    r = validate_recommendation("Buy", 9, invest_amount=5_000, monthly_surplus=10_000, user_risk_profile=profile)
    assert r.decision == PolicyDecision.ESCALATE, r

    # High-risk Buy with no track record on file → allow (nothing to contradict).
    r = validate_recommendation("Buy", 9, invest_amount=5_000, monthly_surplus=10_000, user_risk_profile=None)
    assert r.decision == PolicyDecision.ALLOW, r

    print("harness/risk_policy.py self-check: all 7 cases passed")


if __name__ == "__main__":
    demo()
