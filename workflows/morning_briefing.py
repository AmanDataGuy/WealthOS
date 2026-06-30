# workflows/morning_briefing.py
"""
WealthOS morning briefing — Temporal cron workflow.

Runs every day at 8 AM for every registered user.
Generates a 5-line WhatsApp/Gmail briefing covering:
  - Watchlist price moves (last 24h)
  - Any spending anomalies flagged
  - Alerts close to trigger price
  - One macro note

Start the briefing cron:
  python -m workflows.morning_briefing start

Send immediately for one user (testing):
  python -m workflows.morning_briefing now 00000000-0000-0000-0000-000000000001
"""
import time
import asyncio
import os
import sys
from datetime import timedelta, date

import httpx
from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.worker import Worker
from temporalio.common import RetryPolicy
from dotenv import load_dotenv

load_dotenv()

TASK_QUEUE   = "wealthos-briefing-queue"
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

RETRY_ONCE = RetryPolicy(maximum_attempts=1)


# ── Activities ────────────────────────────────────────────────────────────────

@activity.defn
async def fetch_briefing_data(user_id: str) -> dict:
    """
    Pull last-24h price changes for the user's watchlist.
    Uses yfinance directly — same as Research Agent.
    """
    import yfinance as yf

    # In Phase 8 this reads the user's real watchlist from Postgres
    # For now using a hardcoded demo watchlist
    watchlist = ["TSLA", "AAPL", "NVDA", "INFY.NS"]

    moves = []
    for ticker in watchlist:
        try:
            hist = yf.Ticker(ticker).history(period="2d")
            if len(hist) >= 2:
                prev  = float(hist["Close"].iloc[-2])
                curr  = float(hist["Close"].iloc[-1])
                pct   = ((curr - prev) / prev) * 100
                moves.append({
                    "ticker": ticker,
                    "price":  round(curr, 2),
                    "change": round(pct, 2),
                })
        except Exception:
            pass

    return {
        "user_id":   user_id,
        "date":      str(date.today()),
        "watchlist": moves,
        # anomalies and alerts would be pulled from Redis/DB in Phase 8
        "anomalies": [],
        "alerts":    [],
    }


@activity.defn
async def generate_briefing(data: dict) -> str:
    """
    Call Groq to generate a 5-line WhatsApp-style morning briefing.
    """
    moves = data.get("watchlist", [])
    if not moves:
        return "Good morning! No watchlist data available today."

    moves_text = "\n".join(
        f"  {m['ticker']}: ${m['price']} ({'+' if m['change'] >= 0 else ''}{m['change']}%)"
        for m in sorted(moves, key=lambda x: abs(x["change"]), reverse=True)
    )

    prompt = f"""You are WealthOS, a personal financial intelligence assistant.
Generate a concise WhatsApp morning briefing for the user. 5 lines max.
Be direct and specific. Use the actual numbers. No filler.

Today: {data['date']}
Watchlist moves (last 24h):
{moves_text}

Format:
Line 1: Good morning greeting + one-line market summary
Line 2-4: Most significant moves with brief context (1 line each)
Line 5: One actionable thought or reminder

Reply with only the 5-line briefing, nothing else."""

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
            json={
                "model": "llama-3.3-70b-versatile",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 200,
                "temperature": 0.4,
            },
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()


@activity.defn
async def send_briefing(user_id: str, briefing: str) -> dict:
    """
    Deliver the briefing via Composio (Gmail + WhatsApp).
    """
    from services.composio_client import send_notification
    results = send_notification(
        user_id=user_id,
        subject=f"WealthOS Morning Briefing — {date.today()}",
        message=briefing,
    )
    print(f"  [briefing] Delivered for {user_id}: {results}")
    return results


# ── Cron Workflow ─────────────────────────────────────────────────────────────

@workflow.defn
class MorningBriefingWorkflow:
    """
    Runs daily at 8 AM via Temporal cron.
    cron_schedule is set when the workflow is started — see start_cron() below.
    """

    @workflow.run
    async def run(self, user_id: str) -> str:
        workflow.logger.info(f"Morning briefing starting for {user_id}")

        data = await workflow.execute_activity(
            fetch_briefing_data, user_id,
            start_to_close_timeout=timedelta(seconds=60),
            retry_policy=RETRY_ONCE,
        )

        briefing = await workflow.execute_activity(
            generate_briefing, data,
            start_to_close_timeout=timedelta(seconds=45),
            retry_policy=RETRY_ONCE,
        )

        await workflow.execute_activity(
            send_briefing,
            args=[user_id, briefing],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RETRY_ONCE,
        )

        workflow.logger.info(f"Morning briefing complete for {user_id}")
        return briefing


# ── Worker for briefing queue ─────────────────────────────────────────────────

async def run_worker():
    client = await Client.connect("localhost:7233")
    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[MorningBriefingWorkflow],
        activities=[fetch_briefing_data, generate_briefing, send_briefing],
    )
    print(f"Briefing worker polling: {TASK_QUEUE}")
    await worker.run()


# ── Start the daily cron ──────────────────────────────────────────────────────

async def start_cron(user_id: str = "00000000-0000-0000-0000-000000000001"):
    """Register the 8 AM daily cron for a user."""
    client = await Client.connect("localhost:7233")
    handle = await client.start_workflow(
        MorningBriefingWorkflow.run,
        user_id,
        id=f"morning-briefing-{user_id}",
        task_queue=TASK_QUEUE,
        cron_schedule="0 8 * * *",   # every day at 8 AM
    )
    print(f"Cron registered — workflow ID: morning-briefing-{user_id}")
    return handle.id


# ── Send immediately (for testing) ───────────────────────────────────────────

async def send_now(user_id: str = "00000000-0000-0000-0000-000000000001") -> str:
    """Trigger one briefing immediately without waiting for 8 AM."""
    client = await Client.connect("localhost:7233")
    result = await client.execute_workflow(
        MorningBriefingWorkflow.run,
        user_id,
        id=f"morning-briefing-now-{user_id}-{int(time.time())}",
        task_queue=TASK_QUEUE,
    )
    print(f"\nBriefing:\n{result}")
    return result


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    cmd     = sys.argv[1] if len(sys.argv) > 1 else "now"
    user_id = sys.argv[2] if len(sys.argv) > 2 else "00000000-0000-0000-0000-000000000001"

    if cmd == "start":
        asyncio.run(start_cron(user_id))
    elif cmd == "worker":
        asyncio.run(run_worker())
    else:
        asyncio.run(send_now(user_id))