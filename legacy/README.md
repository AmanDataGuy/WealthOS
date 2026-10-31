# legacy/ - the v1 pipeline, kept for reference only

This is WealthOS v1: a LangGraph pipeline of LLM "agents" with MCP tool servers, Qdrant retrieval and a Streamlit UI.
v2 (the top level of the repo) replaced it with a deterministic engine: your data -> DuckDB -> trusted metrics,
dashboards and a strategy lab, with the AI only planning and explaining.

Nothing outside this folder imports it, it is not installed by `requirements.txt`, and tests don't collect it.
It stays so the old behaviour can still be read or compared (PLAN.md stage 10.3 ablation). Run it from the `main`
branch, where it is wired up with its own dependencies.
