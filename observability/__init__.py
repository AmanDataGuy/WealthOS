# observability/__init__.py
"""
Observability stack — two tools, two distinct jobs:

  LangSmith  → pipeline tracing (which node ran, how long, what state in/out)
  W&B Weave  → eval quality (LLM-as-judge scoring across prompt strategies)

Import from here rather than the individual files:
    from observability import trace_node, score_memo
"""

from observability.langsmith_config import trace_node, trace_agent, verify_langsmith
from observability.weave_config     import init_weave, score_memo, log_eval_result

__all__ = [
    # LangSmith
    "trace_node",
    "trace_agent",
    "verify_langsmith",
    # W&B Weave (eval-only)
    "init_weave",
    "score_memo",
    "log_eval_result",
]
