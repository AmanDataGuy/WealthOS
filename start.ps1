# start.ps1 - run WealthOS locally on Windows: Postgres (Docker), the API (:8000) and the web app (:5173).
# Run from the project root: .\start.ps1   ·   Close the three windows (API, web, worker) to stop.
$ROOT = $PSScriptRoot
if (-not $ROOT) { $ROOT = (Get-Location).Path }

docker compose -f "$ROOT\docker-compose.yml" up -d wealthos-db 2>&1 | Out-Null
if (-not $?) { Write-Host "Postgres not started (Docker off): sign-in needs it, a dev token works without it." }

Start-Process powershell -ArgumentList @("-NoExit", "-Command",
    "cd '$ROOT\backend'; `$env:PYTHONIOENCODING='utf-8'; ..\venv\Scripts\python -m uvicorn app.main:app --port 8000")
Start-Process powershell -ArgumentList @("-NoExit", "-Command",
    "cd '$ROOT\frontend'; if (-not (Test-Path node_modules)) { npm install --no-audit --no-fund }; npm run dev")

Start-Process powershell -ArgumentList @("-NoExit", "-Command",
    "cd '$ROOT\backend'; `$env:PYTHONIOENCODING='utf-8'; ..\venv\Scripts\python -m app.worker")   # evening prices

Write-Host "WealthOS: app http://localhost:5173  ·  API docs http://localhost:8000/docs"
