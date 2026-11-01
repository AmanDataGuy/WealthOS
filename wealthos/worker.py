"""
# worker.py - background jobs, one process next to the API

Daily prices (`prices.py`): once each evening after 19:00 IST it brings every workspace's NSE / mutual fund prices
up to date. A failing price source records its error on itself and never stops the others.

    python -m wealthos.worker          # run forever
    python -m wealthos.worker --once   # one pass (cron, tests)
"""
import logging
import sys
import time

from dotenv import load_dotenv

from wealthos import prices

POLL_SECONDS = 300
log = logging.getLogger("worker")


# Refresh every workspace whose prices are due. Returns how many sources were refreshed.
def run_once() -> int:
    count = 0
    for user_id, workspace_id in prices.due():
        for entry in prices.refresh(user_id, workspace_id):
            count += 1
            log.info("prices %s/%s %s: %s rows to %s %s", user_id, workspace_id, entry["id"], entry.get("rows"),
                     entry.get("last_date"), "; ".join(entry.get("warnings", [])))
    return count


if __name__ == "__main__":
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if "--once" in sys.argv:
        print(run_once())
    else:
        log.info("worker started: checking prices every %ss", POLL_SECONDS)
        while True:
            run_once()
            time.sleep(POLL_SECONDS)
