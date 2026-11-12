"""
# run_company.py - the company-mode eval: build the model for five real companies and record every tie-out

Live run (SEC + the AI step; needs network and GROQ_API_KEY):
    cd backend && python -m evals.run_company            # AAPL MSFT NVDA JNJ GEV
    cd backend && python -m evals.run_company KO PEP     # any tickers

Writes `evals/company_tieouts.json`: per ticker, each check's years passed / tested and largest difference ($M),
the release figures the AI read and how many were verified, run time and LLM cost; then pass rates over all tickers.
Banks and insurers are left out on purpose (company mode refuses them).
"""
from __future__ import annotations

import json
import logging
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")
from app.ai import llm_client  # noqa: E402  (after .env is loaded)
from app.company import pipeline  # noqa: E402

TICKERS = ["AAPL", "MSFT", "NVDA", "JNJ", "GEV"]
OUT = Path(__file__).parent / "company_tieouts.json"


# Adds up the cost of every LLM call from llm_client's "[llm] ... cost=$x" log lines.
class _Cost(logging.Handler):
    def __init__(self):
        super().__init__(logging.INFO)
        self.usd = 0.0

    # Add one call's cost.
    def emit(self, record):
        m = re.search(r"cost=\$([\d.]+)", record.getMessage())
        self.usd += float(m.group(1)) if m else 0.0


# Build one ticker's model; if the free-tier model was rate-limited, wait a minute and try once more.
def _one(ticker: str, cost: _Cost) -> dict:
    for attempt in (1, 2):
        before, start = cost.usd, time.monotonic()
        r = pipeline.run(ticker, "eval")
        if "unavailable" not in (r["releases"].get("error") or "") or attempt == 2:
            break
        time.sleep(65)
    rel = r["releases"]
    return {"company": r["company"]["name"], "years": r["statements"]["years"], "seconds": round(time.monotonic() - start, 1),
            "llm_cost_usd": round(cost.usd - before, 6),
            "checks": {c["check"]: {k: c[k] for k in ("status", "years_passed", "years_tested", "max_abs_delta")}
                       | ({"failures": c["failures"]} if c.get("failures") else {}) for c in r["tieouts"]},
            "release": {"url": rel.get("url"), "error": rel.get("error"), "verified": rel.get("verified", 0),
                        "unverified": rel.get("unverified", 0),
                        "unverified_items": [{k: i[k] for k in ("name", "value", "quote")} for i in rel["items"] if not i["verified"]]},
            "valuation": r["valuation"].get("skip") or {k: round(r["valuation"][k], 2) for k in ("enterprise_value", "per_share")},
            "notes": r["notes"]}


# Run every ticker and write the result file.
def main(tickers: list[str]) -> dict:
    cost = _Cost()
    logging.getLogger("app.ai.llm_client").addHandler(cost)
    logging.getLogger("app.ai.llm_client").setLevel(logging.INFO)
    runs = {}
    for t in tickers:
        runs[t] = _one(t, cost)
        print(t, {k: f"{c['status']} {c['years_passed']}/{c['years_tested']}" for k, c in runs[t]["checks"].items()},
              "release verified", runs[t]["release"]["verified"], "/", runs[t]["release"]["verified"] + runs[t]["release"]["unverified"])
    checks = sorted({k for r in runs.values() for k in r["checks"]})
    rates = {}
    for k in checks:
        tested = [r["checks"][k] for r in runs.values() if r["checks"].get(k, {}).get("status") != "skipped"]
        rates[k] = {"tickers_passed": sum(c["status"] == "pass" for c in tested), "tickers_tested": len(tested),
                    "years_passed": sum(c["years_passed"] for c in tested), "years_tested": sum(c["years_tested"] for c in tested)}
    verified = sum(r["release"]["verified"] for r in runs.values())
    total = verified + sum(r["release"]["unverified"] for r in runs.values())
    result = {"run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "model": llm_client.GROQ_MODEL,
              "tolerance_usd_millions": 2.0, "tickers": runs, "pass_rates": rates,
              "release_figures": {"verified": verified, "total": total, "verified_rate": round(verified / total, 3) if total else None},
              "llm_cost_usd": round(cost.usd, 6)}
    OUT.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    out = main(sys.argv[1:] or TICKERS)
    print(json.dumps({k: out[k] for k in ("pass_rates", "release_figures", "llm_cost_usd")}, indent=2))
