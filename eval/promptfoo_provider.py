#!/usr/bin/env python
# eval/promptfoo_provider.py
"""
## Promptfoo Provider — WealthOS Phase 4

Python provider called by Promptfoo for each test case in eval/promptfoo.yaml.
Sends the test input to the WealthOS API and returns the memo as output.

---

### How Promptfoo calls this

Promptfoo invokes `call_api(prompt, options, context)` for each test case.
`context["vars"]` contains the `ticker` and `query` variables defined in the YAML.

### Config

    # Default: localhost
    promptfoo eval --config eval/promptfoo.yaml

    # EC2 deployment
    API_URL=http://13.203.227.73:8000 promptfoo eval --config eval/promptfoo.yaml
"""

import os
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

API_URL = os.getenv("API_URL", "http://localhost:8000")


def call_api(prompt: str, options: dict, context: dict) -> dict:
    """
    Entry point called by Promptfoo for each test case.

    Args:
        prompt:  Rendered prompt string (from promptfoo.yaml `prompts:`).
        options: Promptfoo options dict.
        context: Contains `vars` dict with ticker and query variables.

    Returns:
        dict with `output` (the memo text) and optionally `error`.
    """
    import requests

    vars_  = context.get("vars", {})
    ticker = str(vars_.get("ticker", "AAPL"))[:50]    # cap to prevent resource exhaustion
    query  = str(vars_.get("query",  prompt))[:1000]

    payload = {
        "ticker":             ticker,
        "query":              query,
        "user_id":            "00000000-0000-0000-0000-000000000001",
        "investment_horizon": "long",
    }

    try:
        resp = requests.post(
            f"{API_URL}/analyze",
            json=payload,
            timeout=180,
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()
        data   = resp.json()
        output = data.get("memo") or data.get("message") or json.dumps(data)
        return {"output": output}

    except requests.exceptions.ConnectionError:
        return {
            "output": "ERROR: Could not connect to WealthOS API",
            "error":  f"Connection refused at {API_URL}. Is the server running?",
        }
    except requests.exceptions.Timeout:
        return {
            "output": "ERROR: Request timed out after 180s",
            "error":  "Pipeline took too long — possible hang or cold start",
        }
    except requests.exceptions.HTTPError as exc:
        return {
            "output": f"ERROR: HTTP {exc.response.status_code}",
            "error":  str(exc),
        }
    except Exception as exc:
        return {
            "output": f"ERROR: {exc}",
            "error":  str(exc),
        }
