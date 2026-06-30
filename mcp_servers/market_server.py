# market_server.py
# Central market intelligence server for WealthOS.
# Covers: individual stock data, macro/market-wide indicators, competitor analysis.
# Tools: get_price, get_financials, get_history, get_info, get_recommendations,
#        get_market_overview, get_currency_rates, get_sector_performance,
#        get_competitors, compare_stocks

import json
import math
import logging
import os

import yfinance as yf
import redis
from mcp.server.fastmcp import FastMCP

# --- Setup ---

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

mcp = FastMCP("market-mcp")

r = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379"), decode_responses=True)

TTL_PRICE      = 60 * 5
TTL_FINANCIALS = 60 * 60
TTL_INFO       = 60 * 60
TTL_HISTORY    = 60 * 5
TTL_RECS       = 60 * 60
TTL_MACRO      = 60 * 15    # 15 minutes for macro/index data
TTL_SECTOR     = 60 * 30    # 30 minutes for sector data


# --- Helpers ---

def safe_float(val):
    try:
        f = float(val)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None

def safe_int(val):
    try:
        return int(val)
    except (TypeError, ValueError):
        return None

def from_cache(key):
    try:
        raw = r.get(key)
        return json.loads(raw) if raw else None
    except Exception:
        return None

def to_cache(key, data, ttl):
    try:
        r.set(key, json.dumps(data, default=str), ex=ttl)
    except Exception:
        pass

def fetch_ticker_info(ticker: str) -> dict:
    """Shared helper to fetch yfinance info dict."""
    return yf.Ticker(ticker).info


# --- Stock Tools ---

@mcp.tool()
def get_price(ticker: str) -> dict:
    """
    Get the latest price snapshot for a stock.
    Returns current price, day high/low, 52 week range, volume, market cap.
    Example: get_price("RELIANCE.NS") or get_price("AAPL")
    """
    key = f"yf:price:{ticker.upper()}"
    cached = from_cache(key)
    if cached:
        cached["from_cache"] = True
        return cached

    try:
        info = fetch_ticker_info(ticker)
        data = {
            "ticker":         ticker,
            "current_price":  safe_float(info.get("currentPrice") or info.get("regularMarketPrice")),
            "previous_close": safe_float(info.get("previousClose")),
            "day_high":       safe_float(info.get("dayHigh")),
            "day_low":        safe_float(info.get("dayLow")),
            "week_52_high":   safe_float(info.get("fiftyTwoWeekHigh")),
            "week_52_low":    safe_float(info.get("fiftyTwoWeekLow")),
            "volume":         safe_int(info.get("volume")),
            "market_cap":     safe_int(info.get("marketCap")),
            "currency":       info.get("currency"),
            "from_cache":     False,
        }
        to_cache(key, data, TTL_PRICE)
        return data

    except Exception as e:
        logger.error("get_price failed for %s: %s", ticker, e)
        return {"ticker": ticker, "error": str(e)}


@mcp.tool()
def get_financials(ticker: str) -> dict:
    """
    Get key financial ratios for a stock.
    Returns P/E, EPS, revenue, margins, debt-to-equity, beta etc.
    Example: get_financials("TCS.NS")
    """
    key = f"yf:financials:{ticker.upper()}"
    cached = from_cache(key)
    if cached:
        cached["from_cache"] = True
        return cached

    try:
        info = fetch_ticker_info(ticker)
        data = {
            "ticker":            ticker,
            "pe_ratio":          safe_float(info.get("trailingPE")),
            "forward_pe":        safe_float(info.get("forwardPE")),
            "eps_trailing":      safe_float(info.get("trailingEps")),
            "eps_forward":       safe_float(info.get("forwardEps")),
            "revenue":           safe_float(info.get("totalRevenue")),
            "profit_margins":    safe_float(info.get("profitMargins")),
            "gross_margins":     safe_float(info.get("grossMargins")),
            "operating_margins": safe_float(info.get("operatingMargins")),
            "debt_to_equity":    safe_float(info.get("debtToEquity")),
            "return_on_equity":  safe_float(info.get("returnOnEquity")),
            "free_cashflow":     safe_float(info.get("freeCashflow")),
            "dividend_yield":    safe_float(info.get("dividendYield")),
            "beta":              safe_float(info.get("beta")),
            "from_cache":        False,
        }
        to_cache(key, data, TTL_FINANCIALS)
        return data

    except Exception as e:
        logger.error("get_financials failed for %s: %s", ticker, e)
        return {"ticker": ticker, "error": str(e)}


@mcp.tool()
def get_history(ticker: str, period: str = "3mo", interval: str = "1d") -> dict:
    """
    Get historical OHLCV price data for a stock.
    period options  : 1d 5d 1mo 3mo 6mo 1y 2y 5y max
    interval options: 1d 1wk 1mo
    Example: get_history("INFY.NS", period="6mo", interval="1d")
    """
    key = f"yf:history:{ticker.upper()}:{period}:{interval}"
    cached = from_cache(key)
    if cached:
        cached["from_cache"] = True
        return cached

    try:
        df = yf.Ticker(ticker).history(period=period, interval=interval)

        if df.empty:
            return {"ticker": ticker, "error": "No data returned"}

        bars = []
        for idx, row in df.iterrows():
            bars.append({
                "date":   str(idx.date()),
                "open":   safe_float(row.get("Open")),
                "high":   safe_float(row.get("High")),
                "low":    safe_float(row.get("Low")),
                "close":  safe_float(row.get("Close")),
                "volume": safe_int(row.get("Volume")),
            })

        data = {
            "ticker":     ticker,
            "period":     period,
            "interval":   interval,
            "bars":       bars,
            "from_cache": False,
        }
        to_cache(key, data, TTL_HISTORY)
        return data

    except Exception as e:
        logger.error("get_history failed for %s: %s", ticker, e)
        return {"ticker": ticker, "error": str(e)}


@mcp.tool()
def get_info(ticker: str) -> dict:
    """
    Get company profile and metadata.
    Returns name, sector, industry, country, employee count, description etc.
    Example: get_info("HDFCBANK.NS")
    """
    key = f"yf:info:{ticker.upper()}"
    cached = from_cache(key)
    if cached:
        cached["from_cache"] = True
        return cached

    try:
        info = fetch_ticker_info(ticker)

        description = info.get("longBusinessSummary", "")
        if len(description) > 500:
            description = description[:500] + "..."

        data = {
            "ticker":           ticker,
            "name":             info.get("longName") or info.get("shortName"),
            "sector":           info.get("sector"),
            "industry":         info.get("industry"),
            "country":          info.get("country"),
            "website":          info.get("website"),
            "employees":        safe_int(info.get("fullTimeEmployees")),
            "description":      description,
            "market_cap":       safe_int(info.get("marketCap")),
            "enterprise_value": safe_int(info.get("enterpriseValue")),
            "from_cache":       False,
        }
        to_cache(key, data, TTL_INFO)
        return data

    except Exception as e:
        logger.error("get_info failed for %s: %s", ticker, e)
        return {"ticker": ticker, "error": str(e)}


@mcp.tool()
def get_recommendations(ticker: str) -> dict:
    """
    Get analyst recommendations and recent rating changes.
    Returns consensus (buy/hold/sell), mean score, upgrades/downgrades.
    Example: get_recommendations("WIPRO.NS")
    """
    key = f"yf:recs:{ticker.upper()}"
    cached = from_cache(key)
    if cached:
        cached["from_cache"] = True
        return cached

    try:
        t    = yf.Ticker(ticker)
        info = t.info

        consensus_map = {
            "strong_buy": "buy",  "buy": "buy",
            "hold": "hold",       "neutral": "hold",
            "sell": "sell",       "strong_sell": "sell",
            "underperform": "sell",
        }
        raw_consensus = info.get("recommendationKey", "")
        consensus     = consensus_map.get(raw_consensus.lower())

        recent_changes = []
        upgrades = downgrades = 0

        try:
            import pandas as pd
            df = t.upgrades_downgrades
            if df is not None and not df.empty:
                cutoff = pd.Timestamp.now(tz="UTC") - pd.DateOffset(months=3)
                if df.index.tz is None:
                    df.index = df.index.tz_localize("UTC")
                recent = df[df.index >= cutoff]
                for idx, row in recent.iterrows():
                    action = str(row.get("Action", "")).lower()
                    recent_changes.append({
                        "date":       str(idx.date()),
                        "firm":       str(row.get("Firm", "")),
                        "to_grade":   str(row.get("ToGrade", "")),
                        "from_grade": str(row.get("FromGrade", "")),
                        "action":     action,
                    })
                    if action in ("upgrade", "init"):
                        upgrades += 1
                    elif action == "downgrade":
                        downgrades += 1
        except Exception:
            pass

        data = {
            "ticker":             ticker,
            "consensus":          consensus,
            "mean_score":         safe_float(info.get("recommendationMean")),
            "number_of_analysts": safe_int(info.get("numberOfAnalystOpinions")),
            "upgrades":           upgrades,
            "downgrades":         downgrades,
            "recent_changes":     recent_changes[:20],
            "from_cache":         False,
        }
        to_cache(key, data, TTL_RECS)
        return data

    except Exception as e:
        logger.error("get_recommendations failed for %s: %s", ticker, e)
        return {"ticker": ticker, "error": str(e)}


# --- Macro / Market-Wide Tools ---

@mcp.tool()
def get_market_overview() -> dict:
    """
    Get live levels for key Indian and global market indices.
    Returns Nifty 50, Sensex, Bank Nifty, Nifty IT, S&P 500, Nasdaq, Dow Jones.
    No arguments needed.
    """
    key = "yf:macro:indices"
    cached = from_cache(key)
    if cached:
        cached["from_cache"] = True
        return cached

    indices = {
        "nifty_50":    "^NSEI",
        "sensex":      "^BSESN",
        "bank_nifty":  "^NSEBANK",
        "nifty_it":    "^CNXIT",
        "sp500":       "^GSPC",
        "nasdaq":      "^IXIC",
        "dow_jones":   "^DJI",
    }

    result = {}
    for name, symbol in indices.items():
        try:
            info = yf.Ticker(symbol).info
            result[name] = {
                "symbol":         symbol,
                "current":        safe_float(info.get("regularMarketPrice")),
                "previous_close": safe_float(info.get("previousClose")),
                "change_pct":     safe_float(info.get("regularMarketChangePercent")),
                "day_high":       safe_float(info.get("dayHigh")),
                "day_low":        safe_float(info.get("dayLow")),
            }
        except Exception as e:
            result[name] = {"symbol": symbol, "error": str(e)}

    data = {"indices": result, "from_cache": False}
    to_cache(key, data, TTL_MACRO)
    return data


@mcp.tool()
def get_currency_rates() -> dict:
    """
    Get live currency exchange rates relevant to Indian investors.
    Returns USD/INR, EUR/INR, GBP/INR, JPY/INR, Gold (INR), Crude Oil (USD).
    No arguments needed.
    """
    key = "yf:macro:currencies"
    cached = from_cache(key)
    if cached:
        cached["from_cache"] = True
        return cached

    pairs = {
        "usd_inr":   "INR=X",
        "eur_inr":   "EURINR=X",
        "gbp_inr":   "GBPINR=X",
        "jpy_inr":   "JPYINR=X",
        "gold_inr":  "GC=F",      # Gold futures in USD — note currency
        "crude_usd": "CL=F",      # Crude oil futures
    }

    result = {}
    for name, symbol in pairs.items():
        try:
            info = yf.Ticker(symbol).info
            result[name] = {
                "symbol":  symbol,
                "rate":    safe_float(info.get("regularMarketPrice")),
                "change_pct": safe_float(info.get("regularMarketChangePercent")),
                "currency": info.get("currency"),
            }
        except Exception as e:
            result[name] = {"symbol": symbol, "error": str(e)}

    data = {"rates": result, "note": "Gold in USD/oz, Crude in USD/barrel", "from_cache": False}
    to_cache(key, data, TTL_MACRO)
    return data


@mcp.tool()
def get_sector_performance() -> dict:
    """
    Get performance of major Indian market sectors using Nifty sectoral indices.
    Returns day change % for IT, Banking, FMCG, Auto, Pharma, Energy, Realty, Metal.
    No arguments needed.
    """
    key = "yf:macro:sectors"
    cached = from_cache(key)
    if cached:
        cached["from_cache"] = True
        return cached

    sectors = {
        "it":      "^CNXIT",
        "banking": "^NSEBANK",
        "fmcg":    "^CNXFMCG",
        "auto":    "^CNXAUTO",
        "pharma":  "^CNXPHARMA",
        "energy":  "^CNXENERGY",
        "realty":  "^CNXREALTY",
        "metal":   "^CNXMETAL",
    }

    result = {}
    for name, symbol in sectors.items():
        try:
            info = yf.Ticker(symbol).info
            change_pct = safe_float(info.get("regularMarketChangePercent"))
            result[name] = {
                "symbol":     symbol,
                "current":    safe_float(info.get("regularMarketPrice")),
                "change_pct": change_pct,
                "trend":      "up" if (change_pct or 0) > 0 else "down",
            }
        except Exception as e:
            result[name] = {"symbol": symbol, "error": str(e)}

    # Sort by change_pct descending
    sorted_sectors = dict(
        sorted(result.items(), key=lambda x: x[1].get("change_pct") or 0, reverse=True)
    )

    data = {
        "sectors":    sorted_sectors,
        "top_sector": next(iter(sorted_sectors)),
        "from_cache": False,
    }
    to_cache(key, data, TTL_SECTOR)
    return data


# --- Competitor / Comparison Tools ---

@mcp.tool()
def get_competitors(ticker: str) -> dict:
    """
    Find sector peers for a given stock using yfinance industry data.
    Returns up to 5 peers with their basic price and valuation data.
    Example: get_competitors("TCS.NS")
    """
    key = f"yf:competitors:{ticker.upper()}"
    cached = from_cache(key)
    if cached:
        cached["from_cache"] = True
        return cached

    # Hardcoded peer map for major Indian stocks — yfinance doesn't expose peer lists directly
    PEER_MAP = {
        # IT
        "TCS.NS":       ["INFY.NS", "WIPRO.NS", "HCLTECH.NS", "TECHM.NS"],
        "INFY.NS":      ["TCS.NS", "WIPRO.NS", "HCLTECH.NS", "TECHM.NS"],
        "WIPRO.NS":     ["TCS.NS", "INFY.NS", "HCLTECH.NS", "TECHM.NS"],
        "HCLTECH.NS":   ["TCS.NS", "INFY.NS", "WIPRO.NS", "TECHM.NS"],
        # Banking
        "HDFCBANK.NS":  ["ICICIBANK.NS", "SBIN.NS", "KOTAKBANK.NS", "AXISBANK.NS"],
        "ICICIBANK.NS": ["HDFCBANK.NS", "SBIN.NS", "KOTAKBANK.NS", "AXISBANK.NS"],
        "SBIN.NS":      ["HDFCBANK.NS", "ICICIBANK.NS", "KOTAKBANK.NS", "AXISBANK.NS"],
        # Energy
        "RELIANCE.NS":  ["ONGC.NS", "IOC.NS", "BPCL.NS", "GAIL.NS"],
        "ONGC.NS":      ["RELIANCE.NS", "IOC.NS", "BPCL.NS", "GAIL.NS"],
        # Auto
        "MARUTI.NS":    ["TATAMOTORS.NS", "M&M.NS", "BAJAJ-AUTO.NS", "HEROMOTOCO.NS"],
        "TATAMOTORS.NS":["MARUTI.NS", "M&M.NS", "BAJAJ-AUTO.NS", "HEROMOTOCO.NS"],
        # Pharma
        "SUNPHARMA.NS": ["DRREDDY.NS", "CIPLA.NS", "DIVISLAB.NS", "APOLLOHOSP.NS"],
        "DRREDDY.NS":   ["SUNPHARMA.NS", "CIPLA.NS", "DIVISLAB.NS"],
    }

    try:
        peers = PEER_MAP.get(ticker.upper(), [])

        # If not in map, fall back to same-sector stocks using yfinance info
        if not peers:
            info = fetch_ticker_info(ticker)
            sector = info.get("sector", "")
            industry = info.get("industry", "")
            return {
                "ticker":   ticker,
                "sector":   sector,
                "industry": industry,
                "peers":    [],
                "note":     f"No peer map entry for {ticker}. Add it to PEER_MAP in market_server.py.",
                "from_cache": False,
            }

        peer_data = []
        for peer in peers:
            try:
                info = fetch_ticker_info(peer)
                peer_data.append({
                    "ticker":        peer,
                    "name":          info.get("longName") or info.get("shortName"),
                    "current_price": safe_float(info.get("currentPrice") or info.get("regularMarketPrice")),
                    "market_cap":    safe_int(info.get("marketCap")),
                    "pe_ratio":      safe_float(info.get("trailingPE")),
                    "change_pct":    safe_float(info.get("regularMarketChangePercent")),
                })
            except Exception:
                peer_data.append({"ticker": peer, "error": "fetch failed"})

        data = {
            "ticker":     ticker,
            "peers":      peer_data,
            "from_cache": False,
        }
        to_cache(key, data, TTL_INFO)
        return data

    except Exception as e:
        logger.error("get_competitors failed for %s: %s", ticker, e)
        return {"ticker": ticker, "error": str(e)}


@mcp.tool()
def compare_stocks(tickers: list[str]) -> dict:
    """
    Side-by-side financial comparison of multiple stocks.
    Pass a list of tickers. Returns price, P/E, margins, ROE, market cap for each.
    Example: compare_stocks(["TCS.NS", "INFY.NS", "WIPRO.NS"])
    """
    key = f"yf:compare:{'_'.join(sorted(t.upper() for t in tickers))}"
    cached = from_cache(key)
    if cached:
        cached["from_cache"] = True
        return cached

    results = []
    for ticker in tickers:
        try:
            info = fetch_ticker_info(ticker)
            results.append({
                "ticker":            ticker,
                "name":              info.get("longName") or info.get("shortName"),
                "current_price":     safe_float(info.get("currentPrice") or info.get("regularMarketPrice")),
                "market_cap":        safe_int(info.get("marketCap")),
                "pe_ratio":          safe_float(info.get("trailingPE")),
                "forward_pe":        safe_float(info.get("forwardPE")),
                "profit_margins":    safe_float(info.get("profitMargins")),
                "return_on_equity":  safe_float(info.get("returnOnEquity")),
                "debt_to_equity":    safe_float(info.get("debtToEquity")),
                "dividend_yield":    safe_float(info.get("dividendYield")),
                "beta":              safe_float(info.get("beta")),
                "week_52_high":      safe_float(info.get("fiftyTwoWeekHigh")),
                "week_52_low":       safe_float(info.get("fiftyTwoWeekLow")),
            })
        except Exception as e:
            results.append({"ticker": ticker, "error": str(e)})

    # Find best in class for key metrics
    valid = [r for r in results if "error" not in r]
    best = {}
    if valid:
        best["lowest_pe"]       = min(valid, key=lambda x: x.get("pe_ratio") or float("inf"))["ticker"]
        best["highest_roe"]     = max(valid, key=lambda x: x.get("return_on_equity") or 0)["ticker"]
        best["best_margins"]    = max(valid, key=lambda x: x.get("profit_margins") or 0)["ticker"]
        best["largest_by_mcap"] = max(valid, key=lambda x: x.get("market_cap") or 0)["ticker"]

    data = {
        "comparison": results,
        "best_in_class": best,
        "from_cache": False,
    }
    to_cache(key, data, TTL_FINANCIALS)
    return data


# --- Technical Analysis Tools ---

@mcp.tool()
def get_technicals(ticker: str, period: str = "3mo") -> dict:
    """
    Compute RSI, MACD, Bollinger Bands, and support/resistance levels from price history.
    period options: 1mo 3mo 6mo 1y
    Example: get_technicals("AAPL", period="3mo")
    """
    key = f"yf:technicals:{ticker.upper()}:{period}"
    cached = from_cache(key)
    if cached:
        cached["from_cache"] = True
        return cached

    try:
        df = yf.Ticker(ticker).history(period=period, interval="1d")
        if df.empty or len(df) < 20:
            return {"ticker": ticker, "error": "Insufficient price history for technical analysis"}

        closes = df["Close"].tolist()
        highs  = df["High"].tolist()
        lows   = df["Low"].tolist()

        # RSI (14-period)
        def _rsi(prices, period=14):
            gains, losses = [], []
            for i in range(1, len(prices)):
                diff = prices[i] - prices[i - 1]
                gains.append(max(diff, 0))
                losses.append(max(-diff, 0))
            if len(gains) < period:
                return None
            avg_gain = sum(gains[:period]) / period
            avg_loss = sum(losses[:period]) / period
            for i in range(period, len(gains)):
                avg_gain = (avg_gain * (period - 1) + gains[i]) / period
                avg_loss = (avg_loss * (period - 1) + losses[i]) / period
            if avg_loss == 0:
                return 100.0
            rs = avg_gain / avg_loss
            return round(100 - (100 / (1 + rs)), 2)

        # MACD (12/26/9)
        def _ema(prices, n):
            k = 2 / (n + 1)
            ema = [prices[0]]
            for p in prices[1:]:
                ema.append(p * k + ema[-1] * (1 - k))
            return ema

        ema12 = _ema(closes, 12)
        ema26 = _ema(closes, 26)
        macd_line  = [round(a - b, 4) for a, b in zip(ema12, ema26)]
        signal_line = _ema(macd_line, 9)
        macd_hist  = [round(m - s, 4) for m, s in zip(macd_line, signal_line)]

        # Bollinger Bands (20-period, 2 std)
        def _bollinger(prices, n=20):
            if len(prices) < n:
                return None, None, None
            sma   = sum(prices[-n:]) / n
            std   = (sum((p - sma) ** 2 for p in prices[-n:]) / n) ** 0.5
            return round(sma + 2 * std, 4), round(sma, 4), round(sma - 2 * std, 4)

        bb_upper, bb_mid, bb_lower = _bollinger(closes)

        # Support / resistance: recent swing highs and lows
        window = min(20, len(closes))
        recent_highs = sorted(highs[-window:], reverse=True)[:3]
        recent_lows  = sorted(lows[-window:])[:3]
        resistance   = round(recent_highs[0], 4) if recent_highs else None
        support      = round(recent_lows[0],  4) if recent_lows  else None

        current_price = closes[-1] if closes else None

        data = {
            "ticker":        ticker,
            "period":        period,
            "current_price": round(current_price, 4) if current_price else None,
            "rsi":           _rsi(closes),
            "macd": {
                "macd_line":   round(macd_line[-1],   4) if macd_line   else None,
                "signal_line": round(signal_line[-1], 4) if signal_line else None,
                "histogram":   round(macd_hist[-1],   4) if macd_hist   else None,
            },
            "bollinger_bands": {
                "upper":  bb_upper,
                "middle": bb_mid,
                "lower":  bb_lower,
            },
            "support":    support,
            "resistance": resistance,
            "bars_used":  len(closes),
            "from_cache": False,
        }
        to_cache(key, data, TTL_HISTORY)
        return data

    except Exception as e:
        logger.error("get_technicals failed for %s: %s", ticker, e)
        return {"ticker": ticker, "error": str(e)}


@mcp.tool()
def get_options_data(ticker: str) -> dict:
    """
    Get options market data including put/call ratio and implied volatility.
    Example: get_options_data("AAPL")
    """
    key = f"yf:options:{ticker.upper()}"
    cached = from_cache(key)
    if cached:
        cached["from_cache"] = True
        return cached

    try:
        t = yf.Ticker(ticker)
        expirations = t.options
        if not expirations:
            return {"ticker": ticker, "error": "No options data available", "put_call_ratio": None}

        # Use the nearest expiration
        nearest = expirations[0]
        chain   = t.option_chain(nearest)

        calls = chain.calls
        puts  = chain.puts

        total_call_vol = int(calls["volume"].fillna(0).sum()) if not calls.empty else 0
        total_put_vol  = int(puts["volume"].fillna(0).sum())  if not puts.empty  else 0

        put_call_ratio = None
        if total_call_vol > 0:
            put_call_ratio = round(total_put_vol / total_call_vol, 3)

        # Average IV for near-the-money options
        avg_call_iv = None
        avg_put_iv  = None
        if not calls.empty and "impliedVolatility" in calls.columns:
            mid_calls   = calls[calls["volume"].fillna(0) > 0]
            avg_call_iv = round(float(mid_calls["impliedVolatility"].mean()), 4) if not mid_calls.empty else None
        if not puts.empty and "impliedVolatility" in puts.columns:
            mid_puts   = puts[puts["volume"].fillna(0) > 0]
            avg_put_iv = round(float(mid_puts["impliedVolatility"].mean()),  4) if not mid_puts.empty  else None

        data = {
            "ticker":           ticker,
            "nearest_expiry":   nearest,
            "total_call_volume": total_call_vol,
            "total_put_volume":  total_put_vol,
            "put_call_ratio":   put_call_ratio,
            "avg_call_iv":      avg_call_iv,
            "avg_put_iv":       avg_put_iv,
            "sentiment": (
                "bearish" if put_call_ratio and put_call_ratio > 1.2 else
                "bullish" if put_call_ratio and put_call_ratio < 0.8 else
                "neutral"
            ),
            "from_cache": False,
        }
        to_cache(key, data, TTL_HISTORY)
        return data

    except Exception as e:
        logger.error("get_options_data failed for %s: %s", ticker, e)
        return {"ticker": ticker, "error": str(e), "put_call_ratio": None}


# --- Run ---

if __name__ == "__main__":
    logger.info("market-mcp server starting...")
    mcp.run(transport="stdio")