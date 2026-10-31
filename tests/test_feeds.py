# tests/test_feeds.py
"""Push feeds (token, checks, 1,000 rows -> compacted file with the right count), pull feeds with a mocked
URL, the private-address guard, and the worker's due list. No network."""
import httpx
import pytest

from wealthos import engine
from wealthos import feeds
from wealthos import workspaces


@pytest.fixture(autouse=True)
def data_root(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "DATA_ROOT", tmp_path / "users")


# 1,000 rows pushed in 10 batches land in one table, survive compaction, and change the data fingerprint as they arrive.
def test_push_feed_compacts_with_right_counts(monkeypatch):
    monkeypatch.setattr(feeds, "COMPACT_AFTER", 4)
    ws = workspaces.create_workspace("u", "Live")["id"]
    feed = feeds.create_feed("u", ws, "Bank Txns", "push")
    assert feed["name"] == "bank_txns" and "token" in feed and "token_hash" not in feed
    with pytest.raises(PermissionError):
        feeds.check_token("u", ws, feed["id"], "wrong")
    feeds.check_token("u", ws, feed["id"], feed["token"])

    fingerprints = set()
    for batch in range(10):
        rows = [{"Order ID": batch * 100 + i, "Amount": 10.0, "city": "Pune"} for i in range(100)]
        feeds.append_rows("u", ws, feed["id"], rows)
        fingerprints.add(engine._provenance_data("u", {"bank_txns"}, ws)[1])
    meta = engine.list_datasets("u", ws)[0]
    assert meta["rows"] == 1000 and len(meta["files"]) <= 5  # merged along the way
    r = engine.run_sql("u", "SELECT COUNT(*) AS n, COUNT(DISTINCT order_id) AS ids, SUM(amount) AS total FROM bank_txns", ws)
    assert r.rows == [{"n": 1000, "ids": 1000, "total": 10000.0}]
    assert len(fingerprints) == 10  # every batch gives a new data fingerprint
    assert len(list((engine.user_dir("u") / "datasets" / "bank_txns").glob("*.parquet"))) <= 5  # merged automatically

    for bad in ([], [1, 2], [{"x": {"nested": 1}}], [{"a": 1}] * 5001, "rows"):
        with pytest.raises(ValueError):
            feeds.append_rows("u", ws, feed["id"], bad)
    feeds.delete_feed("u", ws, feed["id"])
    assert engine.list_datasets("u", ws) == [] and feeds.list_feeds("u", ws) == []


# A feed into the built-in prices table needs its must-have columns, and then the engine sees it.
def test_feed_into_prices_table():
    feed = feeds.create_feed("u", "default", "prices", "push")
    with pytest.raises(ValueError, match="close"):
        feeds.append_rows("u", "default", feed["id"], [{"ts": "2026-10-01", "symbol": "TCS"}])
    feeds.append_rows("u", "default", feed["id"], [{"ts": "2026-10-01", "symbol": "TCS", "close": 4000},
                                                   {"ts": "2026-10-02", "symbol": "TCS", "close": 4040}])
    rows = engine.run_sql("u", "SELECT ts, close FROM prices ORDER BY ts", "default").rows
    assert rows == [{"ts": "2026-10-01", "close": 4000}, {"ts": "2026-10-02", "close": 4040}]


# Pull feeds: only public https URLs; JSON and CSV responses append rows; failures are recorded on the feed.
def test_pull_feed(monkeypatch):
    for bad in ("http://example.com/x", "https://127.0.0.1/x", "https://localhost/x", "https://10.0.0.5/x",
                "https://169.254.169.254/latest/meta-data", "https://[::1]/x", "ftp://example.com"):
        with pytest.raises(ValueError):
            feeds.check_url(bad)
    monkeypatch.setattr(feeds, "check_url", lambda url: None)  # no DNS in tests
    feed = feeds.create_feed("u", "default", "rates", "pull", "https://rates.example.com/today", interval_s=60)
    assert feeds.due_pulls() == [("u", "default", feed["id"])]

    answers = iter([httpx.Response(200, json={"rows": [{"ccy": "USD", "inr": 84.1}]}),
                    httpx.Response(200, headers={"content-type": "text/csv"}, text="ccy,inr\nEUR,91.2\nGBP,109.5\n"),
                    httpx.Response(503)])
    client = httpx.Client(transport=httpx.MockTransport(lambda request: next(answers)))
    assert feeds.pull("u", "default", feed["id"], client)["total_rows"] == 1
    assert feeds.pull("u", "default", feed["id"], client)["total_rows"] == 3
    with pytest.raises(ValueError, match="503"):
        feeds.pull("u", "default", feed["id"], client)
    saved = feeds.list_feeds("u", "default")[0]
    assert saved["rows"] == 3 and "503" in saved["last_error"] and feeds.due_pulls() == []  # just pulled
    assert engine.run_sql("u", "SELECT ROUND(SUM(inr), 1) AS s FROM rates", "default").rows == [{"s": 284.8}]
    with pytest.raises(ValueError):
        feeds.pull("u", "default", feeds.create_feed("u", "default", "orders", "push")["id"])  # push feeds aren't fetched
