"""
# worker.py - background jobs, one process next to the API

- Pull feeds (`feeds.py`): every few seconds it fetches each feed whose interval has passed and appends the rows.
- Daily prices (`prices.py`): once each evening after 19:00 IST it brings every workspace's NSE / mutual fund prices
  up to date.
A failing feed or price source records its error on itself and never stops the others.

    python -m wealthos.worker          # run forever
    python -m wealthos.worker --once   # one pass (cron, tests)
"""
import logging
import sys
import time

from dotenv import load_dotenv

from wealthos import feeds
from wealthos import prices

POLL_SECONDS = 5
log = logging.getLogger("worker")


# Fetch every due pull feed once. Returns (fetched, failed).
def run_once() -> tuple[int, int]:
    fetched = failed = 0
    for user_id, workspace_id, feed_id in feeds.due_pulls():
        try:
            result = feeds.pull(user_id, workspace_id, feed_id)
            fetched += 1
            log.info("feed %s/%s/%s: +%s rows", user_id, workspace_id, feed_id, result["rows_added"])
        except Exception as e:  # recorded on the feed by feeds.pull; one bad feed never stops the loop
            failed += 1
            log.warning("feed %s/%s/%s: %s", user_id, workspace_id, feed_id, e)
    for user_id, workspace_id in prices.due():
        for entry in prices.refresh(user_id, workspace_id):
            log.info("prices %s/%s %s: %s rows to %s %s", user_id, workspace_id, entry["id"], entry.get("rows"),
                     entry.get("last_date"), "; ".join(entry.get("warnings", [])))
    return fetched, failed


if __name__ == "__main__":
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if "--once" in sys.argv:
        print(run_once())
    else:
        log.info("worker started: checking pull feeds every %ss", POLL_SECONDS)
        while True:
            run_once()
            time.sleep(POLL_SECONDS)
