# news_server.py
# Fetches financial news and computes sentiment for stocks.
# Uses NewsAPI as primary source + Firecrawl for Reddit community sentiment.
#
# Tools:
#   search_news           — search recent news articles for a query or ticker
#   get_headlines         — get top N headlines for a ticker
#   get_sentiment         — score overall sentiment for a ticker (positive/negative/neutral)
#   get_reddit_sentiment  — scrape Reddit finance subreddits via Firecrawl (no Reddit API needed)

import os
import re
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

NEWSAPI_KEY       = os.getenv("NEWSAPI_KEY", "")
NEWSAPI_BASE      = "https://newsapi.org/v2/everything"
FIRECRAWL_API_KEY = os.getenv("FIRECRAWL_API_KEY", "")

# Cache durations
TTL_NEWS      = 60 * 30   # 30 minutes
TTL_SENTIMENT = 60 * 30   # 30 minutes
TTL_REDDIT    = 60 * 30   # 30 minutes


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
                "published":   a.get("publishedAt", "")[:10],
            })
        return articles

    except Exception as e:
        logger.error("fetch_articles failed for query '%s': %s", query, e)
        return []


def score_sentiment(articles: list[dict]) -> dict:
    """
    Simple keyword-based sentiment scoring.
    Returns: positive_count, negative_count, neutral_count, overall score and label.

    NOTE: Lightweight rule-based scorer.
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
        "score":    score,
        "label":    label,
    }


def scrape_reddit_firecrawl(ticker: str, subreddit: str) -> list[dict]:
    """
    Scrape a Reddit subreddit search page for a ticker using Firecrawl.
    Parses post titles and URLs from the returned markdown.
    No Reddit API credentials needed.
    """
    if not FIRECRAWL_API_KEY:
        logger.warning("FIRECRAWL_API_KEY not set — skipping Reddit scrape")
        return []

    try:
        from firecrawl import FirecrawlApp
        app = FirecrawlApp(api_key=FIRECRAWL_API_KEY)

        url = (
            f"https://www.reddit.com/r/{subreddit}/search/"
            f"?q={ticker.replace(' ', '+')}&t=week&sort=relevance"
        )

        result = app.scrape_url(url, params={"formats": ["markdown"]})
        markdown = result.get("markdown", "")

        if not markdown:
            return []

        posts = []
        lines = markdown.split("\n")

        for line in lines:
            line = line.strip()

            # Match markdown links: [Post Title](reddit_url)
            match = re.search(
                r'\[([^\]]{15,200})\]\((https://www\.reddit\.com/r/[^\)]+)\)',
                line
            )
            if match:
                title   = match.group(1).strip()
                post_url = match.group(2).strip()

                # Skip navigation / UI links
                skip_patterns = [
                    "search", "subscribe", "log in", "sign up", "cookies",
                    "sort by", "top posts", "new posts", "hot", "reddit premium"
                ]
                if any(p in title.lower() for p in skip_patterns):
                    continue
                if len(title) < 15:
                    continue

                posts.append({
                    "title":     title,
                    "url":       post_url,
                    "subreddit": subreddit,
                    "source":    "firecrawl",
                })

        return posts

    except Exception as e:
        logger.error("Firecrawl Reddit scrape failed for r/%s: %s", subreddit, e)
        return []


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
        "query":      query,
        "days":       days,
        "count":      len(articles),
        "articles":   articles,
        "from_cache": False,
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

    articles = fetch_articles(ticker, days=7, count=count)

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


@mcp.tool()
def get_reddit_sentiment(ticker: str) -> dict:
    """
    Scrape Reddit finance subreddits for posts about a stock ticker
    and compute community sentiment. Uses Firecrawl — no Reddit API needed.

    Searches:
        r/IndiaInvestments  — primary Indian retail investor community
        r/IndiaFinance      — Indian personal finance discussions
        r/stocks            — global stock discussions
        r/investing         — global investing community
        r/SecurityAnalysis  — fundamental analysis discussions

    Args:
        ticker: stock ticker or company name — e.g. "Reliance" or "Infosys"

    Returns:
        posts:               list of post titles + urls found across subreddits
        total_posts:         total posts found
        sentiment:           overall sentiment label across all post titles
        sentiment_score:     float -1.0 to +1.0
        breakdown:           positive / negative / neutral post counts
        subreddits_searched: which subreddits were scraped
        from_cache:          whether result came from cache

    Example: get_reddit_sentiment("Reliance Industries")
    """
    cache_key = f"news:reddit_fc:{ticker.lower().replace(' ', '_')}"
    cached = from_cache(cache_key)
    if cached:
        cached["from_cache"] = True
        return cached

    if not FIRECRAWL_API_KEY:
        return {
            "ticker":        ticker,
            "posts":         [],
            "total_posts":   0,
            "sentiment":     "neutral",
            "sentiment_score": 0.0,
            "breakdown":     {"positive": 0, "negative": 0, "neutral": 0},
            "note":          "FIRECRAWL_API_KEY not set — add it to .env",
            "from_cache":    False,
        }

    subreddits = [
        "IndiaInvestments",
        "IndiaFinance",
        "stocks",
        "investing",
        "SecurityAnalysis",
    ]

    all_posts = []
    for sub in subreddits:
        posts = scrape_reddit_firecrawl(ticker, sub)
        all_posts.extend(posts)
        logger.info("r/%s → %d posts found for '%s'", sub, len(posts), ticker)

    if not all_posts:
        return {
            "ticker":        ticker,
            "posts":         [],
            "total_posts":   0,
            "sentiment":     "neutral",
            "sentiment_score": 0.0,
            "breakdown":     {"positive": 0, "negative": 0, "neutral": 0},
            "note":          "No Reddit posts found — try full company name e.g. 'Reliance Industries'",
            "subreddits_searched": subreddits,
            "from_cache":    False,
        }

    # Deduplicate by title
    seen = set()
    unique_posts = []
    for p in all_posts:
        if p["title"] not in seen:
            seen.add(p["title"])
            unique_posts.append(p)

    # Score sentiment across all post titles
    reddit_articles = [{"title": p["title"], "description": ""} for p in unique_posts]
    sentiment_result = score_sentiment(reddit_articles)

    data = {
        "ticker":            ticker,
        "posts":             unique_posts[:25],   # cap at 25
        "total_posts":       len(unique_posts),
        "sentiment":         sentiment_result["label"],
        "sentiment_score":   sentiment_result["score"],
        "breakdown": {
            "positive": sentiment_result["positive"],
            "negative": sentiment_result["negative"],
            "neutral":  sentiment_result["neutral"],
        },
        "subreddits_searched": subreddits,
        "from_cache":        False,
    }
    to_cache(cache_key, data, TTL_REDDIT)
    return data


# ── Run ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logger.info("news-mcp server starting...")
    mcp.run(transport="stdio")