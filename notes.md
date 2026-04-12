# WealthOS Dev Notes

## Phase 0 — Foundation

### Step 0.3 — Docker Compose

#### What is Docker?
Docker lets you run software in isolated boxes called containers.
Each container has everything it needs to run.
No "it works on my machine" problems.

#### What is docker-compose.yml?
A single file that says "start all these services together and make them talk to each other".
Instead of manually installing PostgreSQL, Redis, Kafka, Qdrant on your machine,
Docker does it all with one command:
docker-compose up

#### Why do we create this FIRST before any code?
Because agents need these services to even run.
If you write agent code first but these aren't running, everything crashes immediately.
Think of it like this:
docker-compose.yml is the FOUNDATION of the building.
You don't start building walls before the foundation is ready.

#### What services does it start and why?

| Service            | Port  | Why                                              |
|--------------------|-------|--------------------------------------------------|
| wealthOS-api       | 8000  | FastAPI backend — the brain of the app           |
| wealthOS-mcp       | 8001  | All 11 MCP tool servers                          |
| wealthOS-frontend  | 3000  | Next.js UI                                       |
| wealthOS-db        | 5432  | PostgreSQL — stores users, analyses, portfolio   |
| wealthOS-qdrant    | 6333  | Vector DB — stores document embeddings for RAG   |
| wealthOS-redis     | 6379  | Caching, rate limiting, price alerts             |
| wealthOS-kafka     | 9092  | Streaming price alerts, morning briefing events  |
| wealthOS-ollama    | 11434 | Local LLM for privacy mode                       |
| wealthOS-temporal  | 7233  | Durable workflows — agents survive crashes       |