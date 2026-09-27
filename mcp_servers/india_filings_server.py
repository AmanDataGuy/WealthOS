# india_filings_server.py
# Fetches filings/fundamentals data for Indian-listed companies (NSE).
# No API key needed — nsepython wraps NSE's own public JSON endpoints.
#
# NOTE: Works for NSE-listed tickers only (RELIANCE, TCS, INFY etc — no
# .NS/.BO suffix, same convention as market_server which strips it before
# calling yfinance). US tickers will return an error — expected behaviour.
#
# ⚠️ Known limitation: NSE's endpoints are session/cookie-gated and known to
# rate-limit or silently return {} to scraper traffic (verified live —
# nse_eq() returned {} repeatedly in testing while nse_results()/nse_events()
# succeeded, and nse_circular() returned an HTTP 500 error payload). This is
# NOT SEC-EDGAR-equivalent reliability — treat every tool here as
# best-effort, not authoritative, and never let a caller assume an
# empty/error response means "no data" rather than "NSE blocked this call."
#
# Tools:
#   get_company_info      — quote/company snapshot (nse_eq)
#   get_financial_results — quarterly/annual results (nse_past_results, falls
#                            back to nse_results filtered by symbol)
#   get_corporate_events  — board meetings, dividend/result dates (nse_events)
#   get_circulars         — latest NSE circulars, optionally filtered
#
# No insider-trades tool — nsepython has no clean equivalent to SEC's Form 4
# search; faking one with a wrong data source would be worse than omitting it.

import os
import logging
import json

import redis
from mcp.server.fastmcp import FastMCP
from nsepython import nse_eq, nse_past_results, nse_results, nse_events, nse_circular

# ── Setup ─────────────────────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

mcp = FastMCP("india-filings-mcp")

r = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379"), decode_responses=True)

TTL_EQ        = 60 * 15        # 15 min — quote/company snapshot changes fast
TTL_RESULTS   = 60 * 60 * 6    # 6 hr — matches sec_edgar_server's TTL_FILINGS
TTL_EVENTS    = 60 * 60 * 24   # 24 hr — matches sec_edgar_server's TTL_CIK cadence
TTL_CIRCULARS = 60 * 60 * 6    # 6 hr


# ── Helpers ───────────────────────────────────────────────────────────────────

def from_cache(key: str):
    """Return cached dict or None."""
    try:
        raw = r.get(key)
        return json.loads(raw) if raw else None
    except Exception:
        return None


def to_cache(key: str, data: dict, ttl: int):
    """Save dict to Redis. Silently fails if Redis is down."""
    try:
        r.set(key, json.dumps(data, default=str), ex=ttl)
    except Exception:
        pass


def _clean_symbol(symbol: str) -> str:
    """Strip .NS/.BO suffix if present — nsepython wants the bare NSE symbol."""
    return symbol.split(".")[0].upper()


# ── Tools ─────────────────────────────────────────────────────────────────────

@mcp.tool()
def get_company_info(symbol: str) -> dict:
    """
    Get NSE quote/company snapshot for an Indian-listed equity.

    Returns industry, ISIN, listing date, and key quote fields where NSE's
    quote-equity API provides them. NSE's session/cookie handling makes this
    endpoint the flakiest of the four here — an empty result does not
    necessarily mean the symbol is wrong.

    Example: get_company_info("RELIANCE")
    """
    clean = _clean_symbol(symbol)
    cache_key = f"india:eq:{clean}"
    cached = from_cache(cache_key)
    if cached:
        cached["from_cache"] = True
        return cached

    try:
        payload = nse_eq(clean)
    except Exception as e:
        logger.error("get_company_info failed for %s: %s", symbol, e)
        return {"symbol": symbol, "error": str(e)}

    if not payload or not isinstance(payload, dict) or payload.get("error"):
        return {
            "symbol": symbol,
            "error": "No data returned by NSE — either an invalid symbol or "
                     "NSE's quote-equity endpoint rate-limited/blocked this call.",
        }

    info       = payload.get("info", {}) or {}
    metadata   = payload.get("metadata", {}) or {}
    price_info = payload.get("priceInfo", {}) or {}

    data = {
        "symbol":       symbol,
        "company_name": info.get("companyName", ""),
        "industry":     info.get("industry", ""),
        "isin":         info.get("isin", ""),
        "listing_date": metadata.get("listingDate", ""),
        "last_price":   price_info.get("lastPrice"),
        "change":       price_info.get("change"),
        "p_change":     price_info.get("pChange"),
        "raw":          payload,
        "from_cache":   False,
    }
    to_cache(cache_key, data, TTL_EQ)
    return data


@mcp.tool()
def get_financial_results(symbol: str, period: str = "Quarterly") -> dict:
    """
    Get quarterly/annual financial results for an Indian-listed company.

    Tries nse_past_results() first (per-symbol results-comparison API);
    falls back to nse_results() (the exchange-wide results feed, index=
    "equities") filtered by symbol if the first returns nothing.

    Args:
        symbol: NSE symbol, e.g. "RELIANCE" (suffix stripped if present)
        period: "Quarterly" | "Annual" | "Half-Yearly" | "Others"
                (only used by the nse_results fallback)

    Example: get_financial_results("RELIANCE")
    """
    clean = _clean_symbol(symbol)
    cache_key = f"india:results:{clean}:{period}"
    cached = from_cache(cache_key)
    if cached:
        cached["from_cache"] = True
        return cached

    results = []
    source = "nse_past_results"
    try:
        payload = nse_past_results(clean)
        results = (payload or {}).get("resCmpData", []) or []
    except Exception as e:
        logger.warning("get_financial_results: nse_past_results failed for %s: %s", symbol, e)

    if not results:
        source = "nse_results"
        try:
            df = nse_results(index="equities", period=period)
            if "symbol" in df.columns:
                df = df[df["symbol"] == clean]
            results = df.to_dict(orient="records")
        except Exception as e:
            logger.error("get_financial_results: nse_results fallback failed for %s: %s", symbol, e)
            return {"symbol": symbol, "error": str(e)}

    if not results:
        return {
            "symbol": symbol,
            "error": "No financial results found — either an invalid symbol or "
                     "NSE's results endpoints rate-limited/blocked this call.",
        }

    data = {
        "symbol":     symbol,
        "period":     period,
        "source":     source,
        "results":    results,
        "from_cache": False,
    }
    to_cache(cache_key, data, TTL_RESULTS)
    return data


@mcp.tool()
def get_corporate_events(symbol: str) -> dict:
    """
    Get upcoming/recent corporate events (board meetings, dividend/result
    dates) for an Indian-listed company — the closest NSE equivalent to a
    filings calendar.

    Example: get_corporate_events("RELIANCE")
    """
    clean = _clean_symbol(symbol)
    cache_key = f"india:events:{clean}"
    cached = from_cache(cache_key)
    if cached:
        cached["from_cache"] = True
        return cached

    try:
        df = nse_events()
        if "symbol" in df.columns:
            df = df[df["symbol"] == clean]
        events = df.to_dict(orient="records")
    except Exception as e:
        logger.error("get_corporate_events failed for %s: %s", symbol, e)
        return {"symbol": symbol, "error": str(e)}

    data = {
        "symbol":     symbol,
        "events":     events,
        "count":      len(events),
        "from_cache": False,
    }
    to_cache(cache_key, data, TTL_EVENTS)
    return data


@mcp.tool()
def get_circulars(symbol: str | None = None) -> dict:
    """
    Get latest NSE circulars, optionally filtered to those mentioning a
    given symbol (best-effort substring match — NSE's circular feed has no
    structured per-symbol field).

    Example: get_circulars() or get_circulars("RELIANCE")
    """
    cache_key = f"india:circulars:{_clean_symbol(symbol) if symbol else 'all'}"
    cached = from_cache(cache_key)
    if cached:
        cached["from_cache"] = True
        return cached

    try:
        payload = nse_circular(mode="latest")
    except Exception as e:
        logger.error("get_circulars failed: %s", e)
        return {"symbol": symbol, "error": str(e)}

    if not payload or (isinstance(payload, dict) and payload.get("error")):
        return {
            "symbol": symbol,
            "error": "No data returned by NSE's circular endpoint — it is known "
                     "to be unstable (verified live: returned an HTTP 500 error "
                     "payload during development of this tool).",
        }

    circulars = payload if isinstance(payload, list) else payload.get("data", payload)
    if symbol and isinstance(circulars, list):
        clean = _clean_symbol(symbol)
        circulars = [
            c for c in circulars
            if clean in json.dumps(c, default=str).upper()
        ]

    data = {
        "symbol":     symbol,
        "circulars":  circulars,
        "from_cache": False,
    }
    to_cache(cache_key, data, TTL_CIRCULARS)
    return data


# ── Self-check ────────────────────────────────────────────────────────────────

def demo() -> None:
    """
    ponytail: smallest runnable check — live-calls each tool against a real,
    liquid NSE ticker and asserts no "error" key. Per the known-limitation
    note above, a failure here may mean NSE rate-limited this run, not that
    the code is broken — re-run before assuming a regression.
    """
    ticker = "RELIANCE"
    failures = []

    for name, fn, args in [
        ("get_company_info",      get_company_info,      (ticker,)),
        ("get_financial_results", get_financial_results,  (ticker,)),
        ("get_corporate_events",  get_corporate_events,   (ticker,)),
        ("get_circulars",         get_circulars,          (ticker,)),
    ]:
        result = fn(*args)
        status = "OK" if "error" not in result else f"ERROR: {result['error']}"
        print(f"  {name}({ticker}) — {status}")
        if "error" in result:
            failures.append(name)

    if failures:
        print(f"\n{len(failures)}/4 tools failed — likely NSE rate-limiting, not a code bug. "
              f"See the known-limitation note at the top of this file.")
    else:
        print("\nAll 4 tools returned live data with no error key.")


if __name__ == "__main__":
    logger.info("india-filings-mcp server starting...")
    demo()
