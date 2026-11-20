"""
# run_production_check.py - runs the real API in production mode, stress-tests it and checks recent features on real data

Starts uvicorn as a subprocess with `WEALTHOS_ENV=production` against a real Postgres (pgvector) and drives it over
HTTP with two throwaway users. Then starts a second server in dev mode (NSE and Python strategies switched on) for the
features production turns off. Writes `evals/results_production_check.json`; every number in it comes from this run.

| Check | What it proves |
|---|---|
| 1 nse_live | real NSE files in both formats (old cmDDMONYYYYbhav, new UDiFF from early 2024), gaps, quality flags; RSI / EMA against a hand-written Wilder loop |
| 2 risk_report | `GET /strategy/{u}/{w}/risk` on those prices: fields finite, risk shares sum to 100%, VaR / CVaR match a recompute; MCP `portfolio_risk` gives the same answer |
| 3 strategy_lab | an RSI rule and a moving-average rule: results, robustness warnings, calendar-year returns |
| 4 sandbox | (dev server) a plain uploaded strategy runs, a shift(-1) one is refused for look-ahead, an endless one is stopped |
| 5 company | compare AAPL / MSFT / NVDA from SEC (no AI step): LTM figures, flags; the AAPL workbook built without the AI release step and recalculated by LibreOffice |
| 6 load | mixed concurrent requests: latency per endpoint, status counts, rate limiting, server memory |
| 7 hostile | other users' data, path traversal, oversized / malformed uploads, bad strategy, bad ticker: 4xx, no 500, no paths |
| 8 gates | production refuses NSE, the sandbox and the free fallback model |

No LLM is ever called: both servers and this process run with `GROQ_API_KEY=` and `OPENROUTER_API_KEY=` empty.

Needs: the docker container `rag-pgvector-check` (started here if stopped; its password is read from `docker inspect`
and passed only through the environment), `WEALTHOS_JWT_SECRET` and `SEC_USER_AGENT` in the repo's `.env`,
network access to nsearchives.nseindia.com and sec.gov, and LibreOffice (`soffice`).
User files go to a temporary folder (deleted at the end); NSE / SEC downloads are cached under `backend/storage/cache`
and `backend/storage/sec_cache` (git-ignored), so a re-run downloads almost nothing.

Run: `cd backend && .venv-rag/bin/python evals/run_production_check.py`
"""
from __future__ import annotations

import json
import math
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path

os.environ.update(GROQ_API_KEY="", OPENROUTER_API_KEY="")  # before any app import: no LLM call from this process
BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import httpx  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from dotenv import dotenv_values  # noqa: E402

from app.data import engine  # noqa: E402

OUT = BACKEND / "evals" / "results_production_check.json"
ENV_FILE = next(p / ".env" for p in BACKEND.parents if (p / ".env").exists())  # a worktree sits inside the main checkout
CONTAINER, DB_PORT = "rag-pgvector-check", 55432
PROD_PORT, DEV_PORT = 8771, 8772
SYMBOLS = ["RELIANCE", "TCS", "INFY", "HDFCBANK", "ITC"]
START, END = date(2023, 6, 1), date(2024, 6, 30)
QTY = {"RELIANCE": 50, "TCS": 20, "INFY": 60, "HDFCBANK": 40, "ITC": 300}  # synthetic quantities, real symbols
UNPRICED = ("GOLDBEES", 100, 6000.0)  # a holding with no prices: must be left out, not crash
NORMAL_GAP_DAYS = 5  # longest calendar gap between trading days a holiday run makes (Fri holiday + weekend + Mon)
RSI_RULE = {"universe": SYMBOLS, "entry": {"op": "lt", "args": [{"op": "rsi", "args": [{"op": "close"}, 14]}, 35]},
            "exit": {"op": "gt", "args": [{"op": "rsi", "args": [{"op": "close"}, 14]}, 65]}}
MA_RULE = {"universe": SYMBOLS, "entry": {"op": "crossover", "args": [{"op": "sma", "args": [{"op": "close"}, 20]},
                                                                      {"op": "sma", "args": [{"op": "close"}, 50]}]},
           "exit": {"op": "crossover", "args": [{"op": "sma", "args": [{"op": "close"}, 50]},
                                                {"op": "sma", "args": [{"op": "close"}, 20]}]}}
PLAIN_PY = ("import pandas as pd\n\ndef weights(close):\n    held = (close.rolling(20).mean() > close.rolling(50).mean()).astype(float)\n"
            "    return held.div(held.sum(axis=1).where(lambda s: s > 0), axis=0).fillna(0)\n")
LOOKAHEAD_PY = "def weights(close):\n    up = (close.shift(-1) > close).astype(float)\n    return up / len(close.columns)\n"
ENDLESS_PY = "def weights(close):\n    n = 0\n    while True:\n        n += 1\n    return close\n"
BAD_PY = "import os\n\ndef weights(close):\n    return close * 0\n"
TMP = Path(tempfile.mkdtemp(prefix="wealthos-prodcheck-"))
LEAK = re.compile(r"/home/|/tmp/|storage/users|wealthos-prodcheck|Traceback", re.I)


# Short HEAD hash, with -dirty when tracked files are modified.
def git_sha() -> str:
    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=BACKEND, capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=BACKEND, capture_output=True,
                           text=True).stdout.strip()
    return sha + ("-dirty" if dirty else "")


# Start the Postgres container if needed and return its URL (the password stays in memory, never printed).
def database_url() -> str:
    subprocess.run(["docker", "start", CONTAINER], capture_output=True, check=True)
    env = json.loads(subprocess.run(["docker", "inspect", CONTAINER, "--format", "{{json .Config.Env}}"],
                                    capture_output=True, text=True, check=True).stdout)
    password = next(e.split("=", 1)[1] for e in env if e.startswith("POSTGRES_PASSWORD="))
    for _ in range(30):  # wait until it accepts connections
        if subprocess.run(["docker", "exec", CONTAINER, "pg_isready", "-U", "wealthos"], capture_output=True).returncode == 0:
            break
        time.sleep(1)
    return f"postgresql://wealthos:{password}@127.0.0.1:{DB_PORT}/wealthos"


# Point user files at the temp folder; NSE / SEC caches and the LibreOffice profile stay in backend/storage.
def use_temp_storage() -> None:
    (TMP / "users").mkdir()
    for name in ("cache", "sec_cache"):
        (BACKEND / "storage" / name).mkdir(parents=True, exist_ok=True)
        (TMP / name).symlink_to(BACKEND / "storage" / name)
    engine.DATA_ROOT = TMP / "users"


LAUNCH = ("import sys, pathlib, uvicorn\nfrom app.data import engine\nengine.DATA_ROOT = pathlib.Path(sys.argv[1])\n"
          "uvicorn.run('app.main:app', host='127.0.0.1', port=int(sys.argv[2]), log_level='warning')\n")


# Start one API server (uvicorn, one process) and wait for /health. Returns the process.
def start_server(port: int, env_extra: dict, log_name: str) -> subprocess.Popen:
    env = {k: v for k, v in os.environ.items() if not k.startswith("WEALTHOS_")} | env_extra  # only the switches given here
    log = open(TMP / log_name, "w")
    proc = subprocess.Popen([sys.executable, "-c", LAUNCH, str(TMP / "users"), str(port)], cwd=BACKEND, env=env,
                            stdout=log, stderr=subprocess.STDOUT)
    for _ in range(120):
        try:
            if httpx.get(f"http://127.0.0.1:{port}/health", timeout=2).status_code == 200:
                return proc
        except httpx.HTTPError:
            pass
        if proc.poll() is not None:
            raise RuntimeError(f"server on {port} exited: see the log {log_name}")
        time.sleep(0.5)
    raise RuntimeError(f"server on {port} did not answer /health")


# Resident memory of a process in MB (Linux /proc).
def rss_mb(pid: int) -> float:
    kb = next(int(line.split()[1]) for line in Path(f"/proc/{pid}/status").read_text().splitlines() if line.startswith("VmRSS"))
    return round(kb / 1024, 1)


# Thin HTTP client for one server; every call returns (status, body, seconds).
class Api:
    def __init__(self, port: int):
        self.http = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=180,
                                 limits=httpx.Limits(max_connections=40, max_keepalive_connections=40))

    # One request; the body is JSON when it parses, else the text.
    def call(self, method: str, path: str, token: str | None = None, **kw) -> tuple[int, object, float]:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        t = time.perf_counter()
        r = self.http.request(method, path, headers=headers, **kw)
        try:
            body = r.json()
        except ValueError:
            body = r.text
        return r.status_code, body, round(time.perf_counter() - t, 3)


# Short form of an error body for the result file.
def detail(body) -> str:
    return str(body.get("detail") if isinstance(body, dict) else body)[:200]


# Every number in a nested answer, so "all finite" is one check.
def numbers(x):
    if isinstance(x, bool) or x is None:
        return
    if isinstance(x, (int, float)):
        yield float(x)
    elif isinstance(x, dict):
        for v in x.values():
            yield from numbers(v)
    elif isinstance(x, list):
        for v in x:
            yield from numbers(v)


# ── 1. Live NSE ───────────────────────────────────────────────────────────────────────────────────────────────

# Fetch the window from NSE through prices.fetch_nse (cached per day), recording which format each new day came in.
def check_nse() -> tuple[dict, list[dict]]:
    from app.data import prices
    os.environ["WEALTHOS_ENABLE_NSE"] = "1"  # this process is not a server; the gate itself is check 8
    formats_file = prices._cache() / "_formats.json"  # which format each cached day came in (kept for re-runs)
    formats = json.loads(formats_file.read_text()) if formats_file.exists() else {}
    real_get, requests = prices.httpx.get, []

    # Count every NSE request and note which URL format answered 200.
    def counting_get(url, **kw):
        r = real_get(url, **kw)
        requests.append(r.status_code)
        if r.status_code == 200:
            day = re.search(r"_(\d{8})_F_|cm(\d{2}[A-Z]{3}\d{4})bhav", url)
            key = day.group(1) or datetime.strptime(day.group(2), "%d%b%Y").strftime("%Y%m%d")
            formats[key] = "udiff" if "BhavCopy_NSE_CM" in url else "old_bhav"
        return r

    prices.httpx.get = counting_get
    t = time.perf_counter()
    try:
        rows, warnings = prices.fetch_nse(SYMBOLS, START, END)
    finally:
        prices.httpx.get = real_get
    seconds = round(time.perf_counter() - t, 1)
    formats_file.write_text(json.dumps(formats, sort_keys=True))
    days = sorted({r["ts"] for r in rows})
    weekdays = [d for d in pd.bdate_range(START, END).date]
    by_format = {"udiff": 0, "old_bhav": 0, "unknown": 0}
    for d in days:
        by_format[formats.get(f"{d:%Y%m%d}", "unknown")] += 1
    first_udiff = min((d for d in days if formats.get(f"{d:%Y%m%d}") == "udiff"), default=None)
    last_old = max((d for d in days if formats.get(f"{d:%Y%m%d}") == "old_bhav"), default=None)
    gaps = sorted(((b - a).days, str(a), str(b)) for a, b in zip(days, days[1:]))[::-1]
    per_symbol = {s: sum(r["symbol"] == s for r in rows) for s in SYMBOLS}
    flags = prices.quality_flags(rows, END)
    rsi_ema = check_indicators(rows)
    ok = (by_format["udiff"] > 0 and by_format["old_bhav"] > 0 and gaps[0][0] <= NORMAL_GAP_DAYS
          and min(per_symbol.values()) == len(days) and rsi_ema["pass"] and len(requests) <= 600)
    return {"pass": ok, "inputs": {"symbols": SYMBOLS, "start": str(START), "end": str(END),
                                   "how": "prices.fetch_nse in this process with WEALTHOS_ENABLE_NSE=1 (production refuses it: check 8)"},
            "seconds": seconds, "nse_requests_this_run": len(requests),
            "nse_requests_by_status": {str(s): requests.count(s) for s in sorted(set(requests))},
            "weekdays_in_window": len(weekdays), "trading_days_with_prices": len(days),
            "holidays_or_missing_weekdays": len(weekdays) - len(days),
            "days_by_format": by_format, "last_old_format_day": str(last_old), "first_udiff_day": str(first_udiff),
            "rows_per_symbol": per_symbol, "longest_gaps_calendar_days": [list(g) for g in gaps[:3]],
            "gap_limit_days": NORMAL_GAP_DAYS, "fetch_warnings": warnings,
            "quality_flags_as_of_end": flags, "indicators": rsi_ema}, rows


# The strategy lab's RSI (Wilder) and EMA against plain loops written here, on real RELIANCE closes.
def check_indicators(rows: list[dict]) -> dict:
    from app.features.strategy import Signal, evaluate
    close = pd.DataFrame([r for r in rows if r["symbol"] == "RELIANCE"]).set_index("ts")[["close"]].sort_index()
    close.index = pd.to_datetime(close.index)
    n, c = 14, close["close"].to_numpy()
    change = np.diff(c)
    gain, loss = np.clip(change, 0, None), np.clip(-change, 0, None)
    ag, al, rsi = gain[:n].mean(), loss[:n].mean(), [np.nan] * len(c)
    rsi[n] = 100 - 100 / (1 + ag / al)
    for i in range(n + 1, len(c)):
        ag, al = (ag * (n - 1) + gain[i - 1]) / n, (al * (n - 1) + loss[i - 1]) / n
        rsi[i] = 100 - 100 / (1 + ag / al)
    ema, k = [np.nan] * len(c), 2 / (n + 1)
    ema[n - 1] = c[:n].mean()
    for i in range(n, len(c)):
        ema[i] = c[i] * k + ema[i - 1] * (1 - k)
    lab_rsi = evaluate(Signal(op="rsi", args=[Signal(op="close"), n]), close)["close"].to_numpy()
    lab_ema = evaluate(Signal(op="ema", args=[Signal(op="close"), n]), close)["close"].to_numpy()
    d_rsi, d_ema = float(np.nanmax(np.abs(lab_rsi - rsi))), float(np.nanmax(np.abs(lab_ema - ema)))
    same_nan = bool((np.isnan(lab_rsi) == np.isnan(rsi)).all() and (np.isnan(lab_ema) == np.isnan(ema)).all())
    return {"pass": d_rsi < 1e-8 and d_ema < 1e-8 and same_nan, "symbol": "RELIANCE", "window": n, "days": len(c),
            "max_abs_diff_rsi": d_rsi, "max_abs_diff_ema": d_ema, "first_values_blank_alike": same_nan,
            "last_rsi": round(float(lab_rsi[-1]), 4), "last_ema": round(float(lab_ema[-1]), 4)}


# ── Data in (production server) ──────────────────────────────────────────────────────────────────────────────

# Upload the fetched prices and a synthetic holdings file (real symbols, last close values) to user A.
def load_user_data(api: Api, a: dict, rows: list[dict]) -> dict:
    df = pd.DataFrame(rows)
    prices_csv = df.rename(columns={"ts": "Date", "symbol": "Symbol", "open": "Open", "high": "High", "low": "Low",
                                    "close": "Close", "volume": "Volume"})[["Date", "Symbol", "Open", "High", "Low",
                                                                           "Close", "Volume"]].to_csv(index=False)
    last = df.sort_values("ts").groupby("symbol")["close"].last()
    holdings = "Instrument,Qty.,Avg. cost,Cur. val\n" + "".join(
        f"{s},{q},{round(float(last[s]) * 0.9, 2)},{round(float(last[s]) * q, 2)}\n" for s, q in QTY.items())
    holdings += f"{UNPRICED[0]},{UNPRICED[1]},55,{UNPRICED[2]}\n"
    out = {}
    for name, text in (("nse_prices.csv", prices_csv), ("holdings.csv", holdings)):
        status, body, secs = api.call("POST", "/sources/upload", a["token"], data={"user_id": a["id"]},
                                      files={"file": (name, text.encode())})
        out[name] = {"status": status, "seconds": secs,
                     "tables": [{"shape": t["shape"], "rows": t["rows"]} for t in body.get("tables", [])] if status == 200 else detail(body)}
    targets = {s: 20.0 for s in QTY}  # GOLDBEES has no target on purpose (drift warns about it)
    out["targets"] = api.call("PUT", f"/rebalance/{a['id']}/targets", a["token"], json={"targets": targets})[0]
    out["values_inr"] = {s: round(float(last[s]) * q, 2) for s, q in QTY.items()} | {UNPRICED[0]: UNPRICED[2]}
    out["pass"] = (out["nse_prices.csv"]["status"] == 200 and out["holdings.csv"]["status"] == 200
                   and out["targets"] == 200 and out["nse_prices.csv"]["tables"][0]["rows"] == len(rows))
    return out


# ── 2. Risk report ───────────────────────────────────────────────────────────────────────────────────────────

# The holdings risk report over HTTP, recomputed here from the fetched prices, and the MCP tool's answer.
def check_risk(api: Api, a: dict, rows: list[dict], values: dict) -> dict:
    status, body, secs = api.call("GET", f"/strategy/{a['id']}/default/risk", a["token"])
    if status != 200:
        return {"pass": False, "status": status, "detail": detail(body)}
    needed = ["label", "total_value", "priced_value", "period", "volatility_pct", "max_drawdown", "worst_month",
              "var_95_1d", "cvar_95_1d", "holdings", "top_correlations", "left_out", "warnings", "provenance"]
    missing = [k for k in needed if k not in body]
    nums = list(numbers({k: body[k] for k in needed if k in body and k != "provenance"}))
    finite = all(math.isfinite(x) for x in nums)
    share_sum = round(sum(h["risk_share_pct"] for h in body["holdings"]), 4)

    close = pd.DataFrame(rows).pivot(index="ts", columns="symbol", values="close")[SYMBOLS].dropna()
    priced = sum(values[s] for s in SYMBOLS)
    daily = close.pct_change().dropna() @ pd.Series({s: values[s] / priced for s in SYMBOLS})
    cut = float(np.quantile(daily, 0.05))
    var, cvar = round(-100 * cut, 2), round(-100 * float(daily[daily <= cut].mean()), 2)

    os.environ["WEALTHOS_TOKEN"] = a["token"]  # the MCP tool checks the same token with the same secret
    os.environ["WEALTHOS_ENABLE_NSE"] = "0"  # as on the production server (the sector split depends on it)
    from app import mcp_server
    mcp = json.loads(json.dumps(mcp_server.portfolio_risk("default"), default=str))
    same_as_mcp = {k: v for k, v in mcp.items() if k != "provenance"} == {k: v for k, v in body.items() if k != "provenance"}
    plausible = 0.2 < body["var_95_1d"]["pct"] < 6 and body["cvar_95_1d"]["pct"] >= body["var_95_1d"]["pct"]
    left = [x["holding"] for x in body["left_out"]]
    ok = (not missing and finite and abs(share_sum - 100) < 0.1 and plausible and left == [UNPRICED[0]]
          and abs(var - body["var_95_1d"]["pct"]) < 0.011 and abs(cvar - body["cvar_95_1d"]["pct"]) < 0.011
          and same_as_mcp)
    return {"pass": ok, "inputs": {"holdings": QTY | {UNPRICED[0]: UNPRICED[1]}, "workspace": "default"},
            "status": status, "seconds": secs, "missing_fields": missing, "numbers_checked": len(nums), "all_finite": finite,
            "risk_share_sum_pct": share_sum, "volatility_pct": body["volatility_pct"], "max_drawdown": body["max_drawdown"],
            "worst_month": body["worst_month"], "var_95_1d": body["var_95_1d"], "cvar_95_1d": body["cvar_95_1d"],
            "recomputed_here": {"var_pct": var, "cvar_pct": cvar}, "var_plausible": plausible, "period": body["period"],
            "left_out": body["left_out"], "warnings": body["warnings"], "mcp_portfolio_risk_identical": same_as_mcp}


# ── 3. Strategy lab ─────────────────────────────────────────────────────────────────────────────────────────

# Run one rule over HTTP and check its yearly returns compound to the total.
def run_rule(api: Api, a: dict, name: str, spec: dict) -> dict:
    status, body, secs = api.call("POST", f"/strategy/{a['id']}/default/run", a["token"], json={"spec": spec})
    if status != 200:
        return {"pass": False, "rule": name, "status": status, "detail": detail(body)}
    years = body["returns_by_year"]
    compounded = 100 * (math.prod(1 + y["strategy_pct"] / 100 for y in years) - 1)
    robust = [w for w in body["warnings"] if "doubled" in w or "last 30%" in w]
    ok = ({y["year"] for y in years} == {2023, 2024} and abs(compounded - body["metrics"]["total_return_pct"]) < 0.05
          and all(math.isfinite(x) for x in numbers(body["metrics"])))
    return {"pass": ok, "rule": name, "spec": spec, "description": body["description"], "status": status, "seconds": secs,
            "metrics": body["metrics"], "buy_and_hold": body["buy_and_hold"], "returns_by_year": years,
            "years_compound_to_total": round(compounded, 2), "robustness_warnings": robust, "warnings": body["warnings"],
            "deflated_sharpe": body["run_card"]["deflated_sharpe"], "n_trials": body["run_card"]["n_trials"]}


# ── 4. Sandbox (dev server) ─────────────────────────────────────────────────────────────────────────────────

# Upload one Python strategy file to a server and time it.
def upload_py(api: Api, a: dict, code: str) -> tuple[int, object, float]:
    return api.call("POST", f"/strategy/{a['id']}/default/python", a["token"], files={"file": ("s.py", code.encode())},
                    data={"symbols": ",".join(SYMBOLS)})


# A plain strategy runs, a look-ahead one is refused, an endless one is stopped, a banned import is refused.
def check_sandbox(dev: Api, a: dict) -> dict:
    out = {}
    for name, code, want in (("plain", PLAIN_PY, 200), ("shift_minus_1", LOOKAHEAD_PY, 400),
                             ("endless_loop", ENDLESS_PY, 400), ("import_os", BAD_PY, 400)):
        status, body, secs = upload_py(dev, a, code)
        out[name] = {"status": status, "seconds": secs, "expected": want,
                     "detail": detail(body) if status != 200 else None,
                     "total_return_pct": body["metrics"]["total_return_pct"] if status == 200 else None}
    msg = lambda k: (out[k]["detail"] or "").lower()
    out["pass"] = (all(v["status"] == v["expected"] for v in out.values()) and "future" in msg("shift_minus_1")
                   and ("longer than" in msg("endless_loop") or "failed" in msg("endless_loop"))
                   and out["endless_loop"]["seconds"] < 60)
    out["note"] = "dev server (WEALTHOS_ENV=dev, WEALTHOS_ENABLE_PY_STRATEGIES=1); production refuses this route (check 8)"
    return out


# ── 5. Company ────────────────────────────────────────────────────────────────────────────────────────────

# Compare three companies over HTTP (no AI step), then build the AAPL workbook without the release step and
# recalculate it with LibreOffice.
def check_company(api: Api, a: dict) -> dict:
    tickers = ["AAPL", "MSFT", "NVDA"]
    status, body, secs = api.call("POST", f"/company/{a['id']}/compare", a["token"], json={"tickers": tickers})
    rows = body.get("rows", []) if status == 200 else []
    compare = {"status": status, "seconds_cold": secs, "rows": [
        {k: r.get(k) for k in ("ticker", "fiscal_year_end", "revenue_musd", "ltm_end", "ltm_quarters", "ltm_revenue_musd",
                               "ltm_net_income_musd", "ltm_note", "error")} | {"flags": [f["flag"] for f in r.get("flags", [])]}
        for r in rows]}
    compare_ok = status == 200 and len(rows) == 3 and all(
        "error" not in r and (r["ltm_revenue_musd"] is not None or r["ltm_note"]) and isinstance(r["flags"], list) for r in rows)
    wb = workbook_check()
    return {"pass": compare_ok and wb["pass"], "inputs": {"tickers": tickers, "release_reading": "off"},
            "compare": compare, "workbook": wb}


# The AAPL model as pipeline.run builds it, minus the AI release step; LibreOffice recalculates every formula cell.
def workbook_check() -> dict:
    from app.company import analysis, pipeline, sec, statements, tieout, workbook
    from app.features import excel
    company = sec.resolve("AAPL")
    filings = sec.filings(company["cik"])
    st = statements.build(sec.company_facts(company["cik"]))
    inputs = analysis.dcf_inputs(st["values"])
    valuation = {"inputs": inputs, "skip": inputs["skip"]} if "skip" in inputs else {"inputs": inputs, **analysis.dcf(inputs)}
    result = {"status": "done", "run_id": "AAPL-prodcheck", "query": "AAPL", "company": company, "filings": filings,
              "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "statements": st,
              "ratios": analysis.ratios(st["values"], len(st["years"])), "valuation": valuation,
              "releases": {"items": [], "error": "release reading switched off for this check (no AI step)"},
              "tieouts": tieout.run(st["values"], st["years"]), "notes": pipeline._notes(st)}
    path = TMP / "aapl" / "AAPL.xlsx"
    expected = workbook.build(result, path)
    python_check = tieout.workbook_check(path, expected)
    t = time.perf_counter()
    import openpyxl
    lo = openpyxl.load_workbook(excel.recalc(path, path.parent), data_only=True)
    lo_seconds = round(time.perf_counter() - t, 1)
    grid = {k: v for k, v in expected.items() if k[0] == "Valuation" and 39 <= int(re.sub(r"\D", "", k[1])) <= 44}
    diffs, bad = [], []
    for (sheet, cell), want in expected.items():
        got = lo[sheet][cell].value
        if not isinstance(got, (int, float)):
            bad.append(f"{sheet}!{cell}: {got!r}")
            continue
        diffs.append(abs(got - want) / max(1.0, abs(want)))
        if diffs[-1] > 1e-6:
            bad.append(f"{sheet}!{cell}: LibreOffice {got:.6g} vs python {want:.6g}")
    grid_bad = [b for b in bad if any(b.startswith(f"Valuation!{c}:") for _, c in grid)]
    centre = valuation.get("sensitivity", [[None] * 5] * 5)[2][2]
    return {"pass": bool(grid) and not bad and python_check["status"] == "pass",
            "valuation_skipped": valuation.get("skip"), "sensitivity_cells": len(grid), "sensitivity_mismatches": grid_bad,
            "formula_cells_checked": len(expected), "mismatches": bad[:10], "max_relative_diff": max(diffs, default=None),
            "centre_value_per_share": None if centre is None else round(centre, 4),
            "value_per_share": valuation.get("value_per_share"), "python_recalc": python_check["status"],
            "libreoffice_seconds": lo_seconds, "accounting_tieouts": {c["check"]: c["status"] for c in result["tieouts"]}}


# ── 6. Load ───────────────────────────────────────────────────────────────────────────────────────────────

# Mixed concurrent requests from one user; per-endpoint latency, status counts and server memory.
def check_load(api: Api, a: dict, pid: int) -> dict:
    u, tok = a["id"], a["token"]
    mix = {"drift": ("GET", f"/rebalance/{u}/default", {}),
           "risk": ("GET", f"/strategy/{u}/default/risk", {}),
           "metric": ("POST", "/query/metric", {"json": {"user_id": u, "workspace_id": "default", "metrics": ["portfolio_value"],
                                                          "dimensions": ["symbol_or_scheme"], "time": "today"}}),
           "compare_cached": ("POST", f"/company/{u}/compare", {"json": {"tickers": ["AAPL", "MSFT"]}}),
           "strategy_run": ("POST", f"/strategy/{u}/default/run", {"json": {"spec": MA_RULE}})}
    names = list(mix)
    out, ok = {"rss_mb_before": rss_mb(pid)}, True
    for total, workers in ((50, 10), (200, 20)):

        # One request of the mix, by its position.
        def one(i):
            name = names[i % len(names)]
            method, path, kw = mix[name]
            status, body, secs = api.call(method, path, tok, **kw)
            return name, status, secs, LEAK.search(json.dumps(body)) is not None

        t = time.perf_counter()
        with ThreadPoolExecutor(workers) as pool:
            results = list(pool.map(one, range(total)))
        wall = round(time.perf_counter() - t, 1)
        per = {}
        for name in names:
            lat = sorted(s for n, _, s, _ in results if n == name)
            per[name] = {"n": len(lat), "p50_s": round(float(np.percentile(lat, 50)), 3),
                         "p95_s": round(float(np.percentile(lat, 95)), 3), "max_s": round(max(lat), 3)}
        statuses = {}
        for _, status, _, _ in results:
            statuses[str(status)] = statuses.get(str(status), 0) + 1
        leaks = sum(leak for *_, leak in results)
        five = sum(s >= 500 for _, s, _, _ in results)
        ok &= five == 0 and leaks == 0 and set(statuses) <= {"200", "429"}
        out[f"{total}_at_{workers}"] = {"wall_seconds": wall, "status_counts": statuses, "per_endpoint": per,
                                        "responses_5xx": five, "responses_with_paths": leaks}
    out["rss_mb_after"] = rss_mb(pid)
    out["pass"] = ok
    return out


# The per-user 300/min bucket and the 10/min-per-IP /auth bucket answer 429 with Retry-After once used up.
def check_rate_limits(api: Api, b: dict) -> dict:

    # One cheap authenticated read.
    def one(_):
        r = api.http.get(f"/workspaces/{b['id']}", headers={"Authorization": f"Bearer {b['token']}"})
        return r.status_code, r.headers.get("retry-after")

    t = time.perf_counter()
    with ThreadPoolExecutor(20) as pool:
        burst = list(pool.map(one, range(330)))
    wall = time.perf_counter() - t
    refill = 300 / 60 * wall  # tokens that came back during the burst
    ok_n, limited = sum(s == 200 for s, _ in burst), sum(s == 429 for s, _ in burst)
    logins = [api.call("POST", "/auth/login", json={"username": f"nobody-{secrets.token_hex(3)}", "password": "x" * 8})[0]
              for _ in range(12)]
    passed = 300 <= ok_n <= 301 + refill and limited == 330 - ok_n and all(r for s, r in burst if s == 429) and 429 in logins
    return {"pass": passed, "default_rule": "300 per minute per user", "burst_requests": 330, "burst_seconds": round(wall, 2),
            "allowed": ok_n, "allowed_ceiling_with_refill": round(300 + refill, 1),
            "limited_429": limited, "retry_after_header_on_every_429": all(r for s, r in burst if s == 429),
            "auth_rule": "10 per minute per IP", "login_attempt_statuses": logins,
            "first_429_at_login_attempt": logins.index(429) + 1 if 429 in logins else None}


# ── 7. Hostile inputs (production) ─────────────────────────────────────────────────────────────────────────

# Each hostile request must get a 4xx with a message, never a 500, and never a server path in the answer.
def check_hostile(api: Api, a: dict, b: dict) -> dict:
    ua, ub, ta, tb = a["id"], b["id"], a["token"], b["token"]
    big = b"Date,Narration,Debit\n" + b"01/04/2026,x,1\n" * (21 * 1024 * 1024 // 15)
    cases = [
        ("A reads B's risk", "GET", f"/strategy/{ub}/default/risk", ta, {}, {403}),
        ("A reads B's drift", "GET", f"/rebalance/{ub}/default", ta, {}, {403}),
        ("B reads A's risk", "GET", f"/strategy/{ua}/default/risk", tb, {}, {403}),
        ("A queries B's metrics", "POST", "/query/metric", ta, {"json": {"user_id": ub, "metrics": ["portfolio_value"]}}, {403}),
        ("no token on risk", "GET", f"/strategy/{ua}/default/risk", None, {}, {401}),
        ("forged token on risk", "GET", f"/strategy/{ua}/default/risk", "eyJhbGciOiJIUzI1NiJ9.e30.x", {}, {401}),
        ("traversal workspace risk", "GET", f"/strategy/{ua}/..%2F..%2F{ub}/risk", ta, {}, {404}),
        ("traversal workspace drift", "GET", f"/rebalance/{ua}/..%2F..%2Fetc", ta, {}, {404}),
        ("dot-dot workspace", "GET", f"/strategy/{ua}/../risk", ta, {}, {404, 405}),
        ("traversal run id", "GET", f"/strategy/{ua}/runs/..%2F..%2F..%2Fetc%2Fpasswd", ta, {}, {404}),
        ("traversal upload workspace", "POST", "/sources/upload", tb,
         {"data": {"user_id": ub, "workspace_id": f"../../{ua}/workspaces/default"}, "files": {"file": ("h.csv", b"Instrument,Qty.\nX,1\n")}}, {404}),
        ("oversized upload (21 MB)", "POST", "/sources/upload", tb, {"data": {"user_id": ub}, "files": {"file": ("big.csv", big)}}, {413}),
        ("upload as another user", "POST", "/sources/upload", tb, {"data": {"user_id": ua}, "files": {"file": ("h.csv", b"Instrument,Qty.\nX,1\n")}}, {403}),
        ("executable upload", "POST", "/sources/upload", tb, {"data": {"user_id": ub}, "files": {"file": ("x.exe", b"MZ")}}, {400}),
        ("malformed holdings", "POST", "/sources/upload", tb,
         {"data": {"user_id": ub}, "files": {"file": ("holdings.csv", b"Instrument,Qty.,Cur. val\nRELIANCE,abc,xyz\n,,\n\"unclosed,1,2\n")}}, {200, 400}),
        ("risk on malformed holdings", "GET", f"/strategy/{ub}/default/risk", tb, {}, {400, 404}),
        ("drift on malformed holdings", "GET", f"/rebalance/{ub}/default", tb, {}, {200, 400, 404}),
        ("invalid strategy spec", "POST", f"/strategy/{ua}/default/run", ta,
         {"json": {"spec": {"universe": ["TCS"], "entry": {"op": "os.system", "args": []}}}}, {400}),
        ("strategy window too long", "POST", f"/strategy/{ua}/default/run", ta,
         {"json": {"spec": {"universe": ["TCS"], "entry": {"op": "sma", "args": [{"op": "close"}, 10 ** 9]}}}}, {400}),
        ("strategy unknown symbol", "POST", f"/strategy/{ua}/default/run", ta,
         {"json": {"spec": {"universe": ["../../etc"], "entry": {"op": "gt", "args": [{"op": "close"}, 1]}}}}, {400}),
        ("invalid python strategy file", "POST", f"/strategy/{ua}/default/python", ta,
         {"files": {"file": ("s.py", BAD_PY.encode())}, "data": {"symbols": "TCS"}}, {400}),
        ("compare with one ticker", "POST", f"/company/{ua}/compare", ta, {"json": {"tickers": ["AAPL"]}}, {422}),
        ("compare with a blank ticker", "POST", f"/company/{ua}/compare", ta, {"json": {"tickers": ["AAPL", " "]}}, {400}),
        ("target over 100%", "PUT", f"/rebalance/{ua}/targets", ta, {"json": {"targets": {"TCS": 150}}}, {400}),
    ]
    out, ok = [], True
    for name, method, path, tok, kw, want in cases:
        status, body, secs = api.call(method, path, tok, **kw)
        leak = LEAK.search(json.dumps(body)) is not None
        good = status in want and not leak
        ok &= good
        out.append({"case": name, "status": status, "expected": sorted(want), "pass": good, "path_leak": leak,
                    "detail": detail(body) if status != 200 else "ok", "seconds": secs})
    status, body, _ = api.call("POST", f"/company/{ua}/compare", ta, json={"tickers": ["ZZZZQQ", "AAPL"]})
    bad_row = next((r for r in body.get("rows", []) if r.get("ticker") == "ZZZZQQ"), {}) if status == 200 else {}
    good = status in (200, 400) and (status == 400 or "error" in bad_row) and not LEAK.search(json.dumps(body))
    ok &= good
    out.append({"case": "compare with an unknown ticker", "status": status, "expected": [200, 400], "pass": good,
                "detail": bad_row.get("error") or detail(body)})
    return {"pass": ok, "cases": out}


# ── 8. Production gates ────────────────────────────────────────────────────────────────────────────────────

# Production must refuse NSE downloads and Python strategies, and must not use a free fallback model.
def check_gates(api: Api, a: dict) -> dict:
    nse = api.call("POST", f"/prices/{a['id']}/default", a["token"], json={"source": "nse", "symbols": ["TCS"]})
    py = upload_py(api, a, PLAIN_PY)
    from app.ai import llm_client
    os.environ["WEALTHOS_ENV"] = "production"  # the same config the production server reads
    free_ok = llm_client._free_fallback_ok()
    docs = api.call("GET", "/docs")[0]
    passed = (nse[0] == 400 and "disabled" in detail(nse[1]) and py[0] == 400 and "switched off" in detail(py[1])
              and not free_ok)
    return {"pass": passed, "nse_fetch": {"status": nse[0], "detail": detail(nse[1])},
            "python_strategy": {"status": py[0], "detail": detail(py[1])},
            "fallback_model": llm_client.OPENROUTER_MODEL, "fallback_is_free": llm_client.OPENROUTER_MODEL.endswith(":free"),
            "free_fallback_allowed_in_production": free_ok,
            "docs_page_status": docs, "cost_cap_without_db": "not checked: these servers run with a database"}


# ── Dev server: the NSE API path end to end on a short window ─────────────────────────────────────────────

# Fetch the last 30 days through POST /prices on the dev server (NSE switched on): rows, last date, quality flags.
def check_nse_api(dev: Api, a: dict) -> dict:
    start = date.fromordinal(date.today().toordinal() - 30)
    status, body, secs = dev.call("POST", f"/prices/{a['id']}/default", a["token"],
                                  json={"source": "nse", "symbols": SYMBOLS, "start": str(start)})
    return {"pass": status == 200 and body["rows"] > 0, "inputs": {"symbols": SYMBOLS, "start": str(start)},
            "status": status, "seconds": secs, "detail": detail(body) if status != 200 else None,
            "rows": body.get("rows") if status == 200 else None, "last_date": body.get("last_date") if status == 200 else None,
            "warnings": body.get("warnings") if status == 200 else None,
            "note": "dev server; the route fetches from `start` to today, so the long window is fetched in-process (check 1)"}


# ── Main ────────────────────────────────────────────────────────────────────────────────────────────────────

# Sign up a throwaway user; returns {id, name, token}.
def signup(api: Api) -> dict:
    name = f"prodcheck-{secrets.token_hex(4)}"
    status, body, _ = api.call("POST", "/auth/signup", json={"username": name, "password": secrets.token_urlsafe(12)})
    if status != 200:
        raise RuntimeError(f"signup failed: {status} {detail(body)}")
    return {"id": body["user_id"], "name": name, "token": body["token"]}


def main() -> None:
    dotenv = dotenv_values(ENV_FILE)
    db_url = database_url()
    use_temp_storage()
    os.environ.update(WEALTHOS_JWT_SECRET=dotenv["WEALTHOS_JWT_SECRET"], SEC_USER_AGENT=dotenv.get("SEC_USER_AGENT") or "")
    base = {"WEALTHOS_DB_URL": db_url, "WEALTHOS_JWT_SECRET": dotenv["WEALTHOS_JWT_SECRET"], "GROQ_API_KEY": "",
            "OPENROUTER_API_KEY": "", "SEC_USER_AGENT": dotenv.get("SEC_USER_AGENT") or "", "WEALTHOS_DEMO_SEED": "0"}
    results = {"run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "git_sha": git_sha(),
               "environment": {"python": sys.version.split()[0], "database": "pgvector/pgvector:pg16 (docker, real Postgres)",
                               "production_server": {"WEALTHOS_ENV": "production", "port": PROD_PORT, "llm_keys": "empty"},
                               "dev_server": {"WEALTHOS_ENV": "dev", "WEALTHOS_ENABLE_NSE": "1",
                                              "WEALTHOS_ENABLE_PY_STRATEGIES": "1", "port": DEV_PORT, "llm_keys": "empty",
                                              "used_for": "sandbox and the NSE fetch route, which production refuses"},
                               "user_files": "a temporary folder, deleted after the run", "uvicorn": "one process each"},
               "checks": {}}
    checks, servers, users, api = results["checks"], [], [], None
    try:
        checks["1_nse_live"], rows = check_nse()
        prod = start_server(PROD_PORT, base | {"WEALTHOS_ENV": "production"}, "prod.log")
        servers.append(prod)
        api = Api(PROD_PORT)
        a, b = signup(api), signup(api)
        users += [a, b]
        checks["data_upload"] = load_user_data(api, a, rows)
        checks["8_production_gates"] = check_gates(api, a)  # after the upload, so the sandbox gate is what refuses
        checks["2_risk_report"] = check_risk(api, a, rows, checks["data_upload"]["values_inr"])
        status, drift, secs = api.call("GET", f"/rebalance/{a['id']}/default", a["token"])
        checks["drift_concentration"] = {
            "pass": status == 200 and drift["concentration"]["top5_share_pct"] < 100 and drift["concentration"]["effective_holdings"] > 1,
            "status": status, "seconds": secs, "concentration": drift.get("concentration"), "warnings": drift.get("warnings")}
        checks["3_strategy_lab"] = {"rsi": run_rule(api, a, "RSI(14) below 35, sell above 65", RSI_RULE),
                                    "moving_average": run_rule(api, a, "20-day SMA crosses above 50-day, sell on the reverse", MA_RULE)}
        checks["3_strategy_lab"]["pass"] = checks["3_strategy_lab"]["rsi"]["pass"] and checks["3_strategy_lab"]["moving_average"]["pass"]
        checks["5_company"] = check_company(api, a)
        checks["6_load"] = check_load(api, a, prod.pid)
        checks["6_rate_limits"] = check_rate_limits(api, b)
        time.sleep(61)  # let user B's bucket refill before the hostile cases
        checks["7_hostile_inputs"] = check_hostile(api, a, b)
        dev = start_server(DEV_PORT, base | {"WEALTHOS_ENV": "dev", "WEALTHOS_ENABLE_NSE": "1",
                                             "WEALTHOS_ENABLE_PY_STRATEGIES": "1"}, "dev.log")
        servers.append(dev)
        checks["4_sandbox"] = check_sandbox(Api(DEV_PORT), a)
        checks["1b_nse_api_dev"] = check_nse_api(Api(DEV_PORT), a)
    finally:
        if api:
            results["cleanup_delete_status"] = {f"user_{n}": api.call("DELETE", f"/account/{u['id']}", u["token"],
                                                                      json={"confirm": u["name"]})[0] for n, u in zip("ab", users)}
        for s in servers:
            s.terminate()
            s.wait(10)
        logs = "".join((TMP / n).read_text() for n in ("prod.log", "dev.log") if (TMP / n).exists())
        results["server_logs"] = {"unhandled_errors": logs.count("unhandled error"), "tracebacks": logs.count("Traceback")}
        shutil.rmtree(TMP, ignore_errors=True)
    results["all_pass"] = all(c.get("pass") for c in checks.values()) and results["server_logs"]["unhandled_errors"] == 0
    OUT.write_text(json.dumps(results, indent=1, default=str) + "\n", encoding="utf-8")
    for name, c in checks.items():
        print(f"{'PASS' if c.get('pass') else 'FAIL'}  {name}")
    print("all_pass:", results["all_pass"], "->", OUT)


if __name__ == "__main__":
    main()
