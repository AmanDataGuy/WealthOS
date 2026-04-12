# news_server.py
# Fetches financial news and computes sentiment for stocks.
# Uses NewsAPI as primary source.
#
# Tools:
#   search_news    — search recent news articles for a query or ticker
#   get_headlines  — get top N headlines for a ticker
#   get_sentiment  — score overall sentiment for a ticker (positive/negative/neutral)

import os
import json
import logging
from datetime import datetime, timedelta

import httpx
import redis
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

# ── Load env first ─────────────────────────────────────────────────────────────
load_dotenv()

# ── Setup ─────────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__) 

mcp = FastMCP("news-mcp")

r = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379"), decode_responses=True)

NEWSAPI_KEY  = os.getenv("NEWSAPI_KEY", "")
NEWSAPI_BASE = "https://newsapi.org/v2/everything"

# Cache durations
TTL_NEWS      = 60 * 30   # 30 minutes — news changes but not every second
TTL_SENTIMENT = 60 * 30   # 30 minutes


# ── Helpers ───────────────────────────────────────────────────────────────────

def from_cache(key: str):
    try:
        raw = r.get(key)
        return json.loads(raw) if raw else None
    except Exception:
        return None


def to_cache(key: str, data: dict, ttl: int):
    try:
        r.setex(key, ttl, json.dumps(data, default=str))
    except Exception:
        pass


def fetch_articles(query: str, days: int = 7, count: int = 10) -> list[dict]:
    """
    Fetch articles from NewsAPI for a given query.
    Returns a list of cleaned article dicts.
    """
    if not NEWSAPI_KEY:
        return []

    from_date = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")

    try:
        resp = httpx.get(
            NEWSAPI_BASE,
            params={
                "q":        query,
                "from":     from_date,
                "sortBy":   "publishedAt",
                "pageSize": count,
                "language": "en",
                "apiKey":   NEWSAPI_KEY,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        articles = []
        for a in data.get("articles", []):
            articles.append({
                "title":       a.get("title", ""),
                "description": a.get("description", ""),
                "source":      a.get("source", {}).get("name", ""),
                "url":         a.get("url", ""),
                "published":   a.get("publishedAt", "")[:10],  # date only
            })
        return articles

    except Exception as e:
        logger.error("fetch_articles failed for query '%s': %s", query, e)
        return []


def score_sentiment(articles: list[dict]) -> dict:
    """
    Simple keyword-based sentiment scoring.
    Returns: positive_count, negative_count, neutral_count, overall score and label.

    NOTE: This is a lightweight rule-based scorer.
    In production this gets replaced by a Groq LLM call for better accuracy.
    """
    positive_words = [
        "beat", "beats", "record", "growth", "profit", "upgrade", "raised",
        "strong", "rally", "surge", "gain", "outperform", "buy", "bullish",
        "expansion", "revenue", "exceeds", "positive", "opportunity", "upside"
    ]
    negative_words = [
        "miss", "missed", "loss", "decline", "downgrade", "cut", "weak",
        "fall", "drop", "lawsuit", "investigation", "concern", "risk",
        "sell", "bearish", "layoff", "warning", "disappoints", "below"
    ]

    pos = neg = neu = 0

    for article in articles:
        text = (article.get("title", "") + " " + (article.get("description") or "")).lower()
        pos_hits = sum(1 for w in positive_words if w in text)
        neg_hits = sum(1 for w in negative_words if w in text)

        if pos_hits > neg_hits:
            pos += 1
        elif neg_hits > pos_hits:
            neg += 1
        else:
            neu += 1

    total = pos + neg + neu
    if total == 0:
        return {"positive": 0, "negative": 0, "neutral": 0, "score": 0.0, "label": "neutral"}

    # Score: +1 for positive, -1 for negative, normalized to -1..+1
    score = round((pos - neg) / total, 2)

    if score > 0.2:
        label = "positive"
    elif score < -0.2:
        label = "negative"
    else:
        label = "neutral"

    return {
        "positive": pos,
        "negative": neg,
        "neutral":  neu,
        "score":    score,   # -1.0 (very negative) to +1.0 (very positive)
        "label":    label,
    }


# ── Tools ─────────────────────────────────────────────────────────────────────

@mcp.tool()
def search_news(query: str, days: int = 7, count: int = 10) -> dict:
    """
    Search recent news articles for any query string or ticker.

    Args:
        query: search term — e.g. "Apple earnings" or "AAPL stock"
        days:  how many days back to search (default 7)
        count: max number of articles to return (default 10)

    Example: search_news("Reliance Industries", days=3, count=5)
    """
    cache_key = f"news:search:{query.lower().replace(' ', '_')}:{days}:{count}"
    cached = from_cache(cache_key)
    if cached:
        cached["from_cache"] = True
        return cached

    articles = fetch_articles(query, days=days, count=count)

    data = {
        "query":       query,
        "days":        days,
        "count":       len(articles),
        "articles":    articles,
        "from_cache":  False,
    }
    to_cache(cache_key, data, TTL_NEWS)
    return data


@mcp.tool()
def get_headlines(ticker: str, count: int = 5) -> dict:
    """
    Get the top N recent headlines for a stock ticker.

    Returns title, source, publication date, and article URL.

    Example: get_headlines("TSLA", count=5)
    """
    cache_key = f"news:headlines:{ticker.upper()}:{count}"
    cached = from_cache(cache_key)
    if cached:
        cached["from_cache"] = True
        return cached

    # Search by ticker name for better results
    articles = fetch_articles(ticker, days=7, count=count)

    # Return only the headline-level fields
    headlines = [
        {
            "title":     a["title"],
            "source":    a["source"],
            "published": a["published"],
            "url":       a["url"],
        }
        for a in articles
    ]

    data = {
        "ticker":     ticker,
        "headlines":  headlines,
        "from_cache": False,
    }
    to_cache(cache_key, data, TTL_NEWS)
    return data


@mcp.tool()
def get_sentiment(ticker: str) -> dict:
    """
    Get overall news sentiment for a stock ticker over the last 7 days.

    Returns:
        score: float from -1.0 (very negative) to +1.0 (very positive)
        label: "positive" | "neutral" | "negative"
        article_count: how many articles were analyzed
        breakdown: count of positive / neutral / negative articles

    Example: get_sentiment("AAPL")
    """
    cache_key = f"news:sentiment:{ticker.upper()}"
    cached = from_cache(cache_key)
    if cached:
        cached["from_cache"] = True
        return cached

    articles = fetch_articles(ticker, days=7, count=20)

    if not articles:
        return {
            "ticker":        ticker,
            "score":         0.0,
            "label":         "neutral",
            "article_count": 0,
            "breakdown":     {"positive": 0, "negative": 0, "neutral": 0},
            "note":          "No articles found — check NEWSAPI_KEY or try a different query",
            "from_cache":    False,
        }

    sentiment = score_sentiment(articles)

    data = {
        "ticker":        ticker,
        "score":         sentiment["score"],
        "label":         sentiment["label"],
        "article_count": len(articles),
        "breakdown": {
            "positive": sentiment["positive"],
            "negative": sentiment["negative"],
            "neutral":  sentiment["neutral"],
        },
        "from_cache": False,
    }
    to_cache(cache_key, data, TTL_SENTIMENT)
    return data


# ── Run ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logger.info("news-mcp server starting...")
    mcp.run(transport="stdio")