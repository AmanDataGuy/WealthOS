# observability/__init__.py
"""
Phase 7 — Observability

Three tools, three different questions:
  LangSmith  → what happened in the pipeline and how long did each node take?
  AgentOps   → what did each agent actually do (tools, memory, decisions)?
  W&B Weave  → are the outputs any good? (eval comparison across prompt strategies)

Import from here rather than the individual files:
    from observability import trace_node, init_agentops, weave_op
"""

from observability.langsmith_config import trace_node, trace_agent, verify_langsmith
from observability.agentops_config  import init_agentops, start_session, end_session, track_memory_op, track_action, track_node
from observability.weave_config     import init_weave, weave_op, score_memo, log_eval_result

__all__ = [
    # LangSmith
    "trace_node",
    "trace_agent",
    "verify_langsmith",
    # AgentOps
    "init_agentops",
    "start_session",
    "end_session",
    "track_memory_op",
    "track_action",
    "track_node",
    # Weave
    "init_weave",
    "weave_op",
    "score_memo",
    "log_eval_result",
]