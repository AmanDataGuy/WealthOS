# observability/agentops_config.py
"""
AgentOps tracking for WealthOS.

While LangSmith sees the pipeline structure (node A → node B → node C),
AgentOps sees what's happening *inside* each agent — which MCP tools fired,
what Mem0 returned, how long each tool call took.

## Setup
Add to your .env:
    AGENTOPS_API_KEY=...

## What gets tracked automatically (after init_agentops is called)
- Every LLM call made through OpenAI/Groq SDK
- Session start and end times
- Errors and exceptions

## What we track manually
- MCP tool calls         → @track_tool_call
- Mem0 read/write ops    → @track_memory_op
- Any custom action      → track_action(name, inputs, outputs)

## Usage
    # In api/main.py, at startup:
    from observability.agentops_config import init_agentops
    init_agentops()

    # On MCP tool functions:
    from observability.agentops_config import track_tool_call

    @track_tool_call("yfinance", "get_price")
    async def fetch_price(ticker):
        ...

    # On mem0 functions:
    from observability.agentops_config import track_memory_op

    @track_memory_op("read")
    def read_memory(user_id):
        ...
"""

import os
import time
import functools
from dotenv import load_dotenv

load_dotenv()

AGENTOPS_ENABLED = bool(os.getenv("AGENTOPS_API_KEY"))


# ── Startup init ──────────────────────────────────────────────────────────────

def init_agentops():
    """
    Call once at app startup (in api/main.py).
    After this, every LLM call through OpenAI/Groq SDK is captured automatically.
    """
    if not AGENTOPS_ENABLED:
        print("[agentops] ⚠️  AGENTOPS_API_KEY not set — tracking disabled")
        return

    try:
        import agentops
        agentops.init(
            api_key=os.getenv("AGENTOPS_API_KEY"),
            default_tags=["WealthOS", "production"],
            instrument_llm_calls=True,
            auto_start_session=True,
            skip_auto_end_session=False,
        )
        # Patch litellm so AgentOps sees all LLM calls regardless of provider
        import litellm
        litellm.success_callback = ["agentops"]
        litellm.failure_callback = ["agentops"]
        print("[agentops] ✅ Initialized — LLM calls will be tracked automatically")
    except ImportError:
        print("[agentops] not installed — pip install agentops")
    except Exception as e:
        print(f"[agentops] ❌ Init failed: {e}")


# ── Session helpers ───────────────────────────────────────────────────────────

def start_session(user_id: str, ticker: str) -> str:
    """
    Start a named AgentOps session for one analysis run.
    Returns the session id (or empty string if disabled).

    Call this at the start of the /analyze endpoint so all
    tool calls in that run are grouped under one session.

    Example:
        session_id = start_session("test-user", "TSLA")
    """
    if not AGENTOPS_ENABLED:
        return ""

    try:
        import agentops
        session = agentops.start_session(tags=[f"user:{user_id}", f"ticker:{ticker}"])
        print(f"[agentops] 🟢 Session started — {user_id} / {ticker}")
        return str(session.session_id) if session else ""
    except Exception as e:
        print(f"[agentops] ⚠️  Could not start session: {e}")
        return ""


def end_session(success: bool = True):
    """
    End the current AgentOps session.
    Call at the end of /analyze after the memo is written.
    """
    if not AGENTOPS_ENABLED:
        return

    try:
        import agentops
        status = "Success" if success else "Fail"
        agentops.end_session(end_state=status)
        print(f"[agentops] 🔴 Session ended — {status}")
    except Exception as e:
        print(f"[agentops] ⚠️  Could not end session: {e}")


# ── Decorators ────────────────────────────────────────────────────────────────

def track_node(node_name: str):
    """Manually record each node as an AgentOps action."""
    def decorator(fn):
        @wraps(fn)
        async def wrapper(state):
            if not AGENTOPS_ENABLED:
                return await fn(state)
            try:
                import agentops
                agentops.record(agentops.ActionEvent(
                    action_type=node_name,
                    params={"ticker": state.get("tickers", []), "user_id": state.get("user_id")},
                ))
            except Exception:
                pass
            return await fn(state)
        return wrapper
    return decorator

def track_memory_op(operation: str):
    """
    Decorator for Mem0 read/write functions.

    operation: "read" or "write"

    Usage:
        @track_memory_op("read")
        def read_memory(user_id):
            ...

        @track_memory_op("write")
        def write_memory(user_id, state):
            ...
    """
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            if not AGENTOPS_ENABLED:
                return fn(*args, **kwargs)

            start = time.perf_counter()
            user_id = args[0] if args else "unknown"

            try:
                result = fn(*args, **kwargs)
                elapsed = round((time.perf_counter() - start) * 1000)

                # For reads, log how many memories came back
                memory_count = len(result.split("\n")) if result and operation == "read" else None

                _record_action(
                    name=f"mem0.{operation}_memory",
                    inputs={"user_id": user_id},
                    outputs={
                        "latency_ms": elapsed,
                        "memories_found": memory_count,
                        "has_memories": bool(result) if operation == "read" else None,
                    },
                    status="success",
                )
                return result
            except Exception as e:
                _record_action(
                    name=f"mem0.{operation}_memory",
                    inputs={"user_id": user_id},
                    outputs={"error": str(e)},
                    status="fail",
                )
                raise

        return wrapper
    return decorator


# ── Manual action tracker ─────────────────────────────────────────────────────

def track_action(name: str, inputs: dict, outputs: dict):
    """
    Manually record any action that doesn't fit a decorator pattern.

    Usage:
        track_action(
            name="browser_use.navigate",
            inputs={"url": "https://moneycontrol.com/TSLA"},
            outputs={"extracted_text": "...", "latency_ms": 4200},
        )
    """
    if not AGENTOPS_ENABLED:
        return
    _record_action(name=name, inputs=inputs, outputs=outputs, status="success")


# ── Internal helper ───────────────────────────────────────────────────────────

def _record_action(name: str, inputs: dict, outputs: dict, status: str):
    """Wraps agentops.record_action — handles import and errors silently."""
    try:
        import agentops
        agentops.record(agentops.ActionEvent(
            action_type=name,
            params=inputs,
            returns=outputs,
        ))
    except Exception:
        # Never let observability break the pipeline
        pass