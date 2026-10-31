# observability/langsmith_config.py
"""
LangSmith tracing for WealthOS.

Wraps every LangGraph node with a @traceable decorator so you can see
the full pipeline waterfall in the LangSmith UI — which node ran, how long
it took, what state went in and what came out.

## Setup
Add these to your .env:
    LANGCHAIN_API_KEY=ls__...
    LANGCHAIN_TRACING_V2=true
    LANGCHAIN_PROJECT=WealthOS

## Usage in nodes.py
    from observability.langsmith_config import trace_node

    @trace_node("finance_node")
    async def finance_node(state: WealthOSState) -> dict:
        ...

If LANGCHAIN_API_KEY is not set, the decorator is a silent no-op.
"""

import os
import functools
from dotenv import load_dotenv

load_dotenv()

LANGSMITH_ENABLED = bool(os.getenv("LANGCHAIN_API_KEY"))
LANGSMITH_PROJECT = os.getenv("LANGCHAIN_PROJECT", "WealthOS")


# ── Main decorator ────────────────────────────────────────────────────────────

def trace_node(node_name: str):
    """
    Decorator for LangGraph node functions.

    What it captures in LangSmith:
    - node name (shows up as the span label)
    - user_id and tickers from state (for filtering runs)
    - wall-clock latency
    - any exception that killed the node

    Usage:
        @trace_node("risk_node")
        async def risk_node(state):
            ...
    """
    def decorator(fn):
        @functools.wraps(fn)
        async def wrapper(state: dict, *args, **kwargs):

            # Skip tracing entirely if no API key
            if not LANGSMITH_ENABLED:
                return await fn(state, *args, **kwargs)

            try:
                from langsmith import traceable
            except ImportError:
                print("[langsmith] not installed — pip install langsmith")
                return await fn(state, *args, **kwargs)

            # Pull useful metadata — mask user_id to first 8 chars (PII protection)
            raw_uid = state.get("user_id", "unknown") or "unknown"
            metadata = {
                "user_id":      raw_uid[:8] + "****" if len(raw_uid) > 8 else raw_uid,
                "tickers":      state.get("tickers", []),
                "input_source": state.get("input_source", "text"),
            }

            @traceable(
                name=node_name,
                project_name=LANGSMITH_PROJECT,
                tags=["langgraph", node_name],
                metadata=metadata,
            )
            async def _run(s, *a, **kw):
                return await fn(s, *a, **kw)

            return await _run(state, *args, **kwargs)

        return wrapper
    return decorator


# ── Startup check ─────────────────────────────────────────────────────────────

def verify_langsmith():
    """
    Call once at app startup to confirm credentials are working.
    Prints a clear status line — easy to spot in server logs.

    Found live via stress-testing: these prints used to include ✅/❌/⚠️.
    api/main.py's lifespan() calls this with no try/except (unlike the two
    DB-setup calls next to it, which already degrade gracefully), and stdout
    under a restrictive console encoding (cp1252, the Windows console
    default unless UTF-8 mode is explicitly enabled) raises
    UnicodeEncodeError on those emoji — completely unhandled. A soft,
    expected failure (LangSmith unreachable) was turning into "Application
    startup failed. Exiting.": the server never bound to a port. Plain ASCII
    status words avoid the whole class of risk rather than catching it.
    """
    if not LANGSMITH_ENABLED:
        print("[langsmith] WARNING: LANGCHAIN_API_KEY not set — tracing disabled")
        return False

    try:
        from langsmith import Client
        client = Client()
        # List projects to confirm the key is valid
        projects = [p.name for p in client.list_projects()]
        if LANGSMITH_PROJECT in projects:
            print(f"[langsmith] OK: Connected — project '{LANGSMITH_PROJECT}' found")
        else:
            print(f"[langsmith] OK: Connected — project '{LANGSMITH_PROJECT}' will be created on first trace")
        return True
    except Exception as e:
        print(f"[langsmith] FAILED: Connection failed: {e}")
        return False