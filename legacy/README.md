# legacy/ - the original pipeline, kept for reference only

This is the original WealthOS: a LangGraph pipeline of LLM "agents" with MCP tool servers, Qdrant retrieval and a Streamlit UI.
The current app (the top level of the repo) replaced it with a deterministic engine: your data -> DuckDB -> trusted metrics,
dashboards and a strategy lab, with the AI only planning and explaining.

Nothing outside this folder imports it, it is not installed by `requirements.txt`, and tests don't collect it.
It stays so the old behaviour can still be read or compared (e.g. an ablation). To run it, check out the
commit before this folder was created, where it is wired up with its own dependencies.
