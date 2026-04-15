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

            # Pull useful metadata from state for filtering in the UI
            metadata = {
                "user_id":      state.get("user_id", "unknown"),
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


def trace_agent(agent_name: str, model: str = ""):
    """
    Same idea as trace_node but for individual agent calls
    (run_risk_agent, run_writer_agent, etc.)

    Usage:
        @trace_agent("writer_agent", model="llama-3.3-70b")
        async def run_writer_agent(...):
            ...
    """
    def decorator(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            if not LANGSMITH_ENABLED:
                return await fn(*args, **kwargs)

            try:
                from langsmith import traceable
            except ImportError:
                return await fn(*args, **kwargs)

            @traceable(
                name=agent_name,
                project_name=LANGSMITH_PROJECT,
                tags=["agent", agent_name],
                metadata={"model": model},
            )
            async def _run(*a, **kw):
                return await fn(*a, **kw)

            return await _run(*args, **kwargs)

        return wrapper
    return decorator


# ── Startup check ─────────────────────────────────────────────────────────────

def verify_langsmith():
    """
    Call once at app startup to confirm credentials are working.
    Prints a clear status line — easy to spot in server logs.
    """
    if not LANGSMITH_ENABLED:
        print("[langsmith] ⚠️  LANGCHAIN_API_KEY not set — tracing disabled")
        return False

    try:
        from langsmith import Client
        client = Client()
        # List projects to confirm the key is valid
        projects = [p.name for p in client.list_projects()]
        if LANGSMITH_PROJECT in projects:
            print(f"[langsmith] ✅ Connected — project '{LANGSMITH_PROJECT}' found")
        else:
            print(f"[langsmith] ✅ Connected — project '{LANGSMITH_PROJECT}' will be created on first trace")
        return True
    except Exception as e:
        print(f"[langsmith] ❌ Connection failed: {e}")
        return False