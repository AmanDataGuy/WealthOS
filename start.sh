#!/usr/bin/env bash
# start.sh - run WealthOS locally: Postgres (Docker), the API (:8000) and the web app (:5173).
# Run from the project root: bash start.sh   ·   Ctrl+C stops the API and the web app.
set -e
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$ROOT/venv/bin/python"; [ -x "$PY" ] || PY="$ROOT/venv/Scripts/python"

docker compose -f "$ROOT/docker-compose.yml" up -d wealthos-db >/dev/null 2>&1 \
  || echo "Postgres not started (Docker off): sign-in needs it, a dev token works without it."

(cd "$ROOT" && PYTHONIOENCODING=utf-8 "$PY" -m uvicorn api.main:app --port 8000) &
API_PID=$!
(cd "$ROOT/web" && { [ -d node_modules ] || npm install --no-audit --no-fund; } && npm run dev) &
WEB_PID=$!
(cd "$ROOT" && PYTHONIOENCODING=utf-8 "$PY" -m wealthos.worker) &   # pull feeds and evening prices
WORKER_PID=$!

echo "WealthOS: app http://localhost:5173  ·  API docs http://localhost:8000/docs"
trap "kill $API_PID $WEB_PID $WORKER_PID 2>/dev/null; exit 0" INT TERM
wait
