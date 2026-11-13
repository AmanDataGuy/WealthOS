// CompanyLoop.tsx - the earnings loop around company mode: compare companies, review results against your locked
// forecasts, and check the trades your own target weights imply against your own limits.
// Every number comes from SEC filings or from what you typed; nothing here picks a stock or a trade.
import { useState } from "react";
import { formatNumber, get, post, safeHref } from "./api";

type Props = { u: string; onError: (m: string) => void };
const num = (v: any, d = 2) => (v == null ? "–" : Number(v).toLocaleString("en-US", { maximumFractionDigits: d }));
const fail = (onError: Props["onError"]) => (e: unknown) => onError((e as Error).message);

// Read a browser-saved value; the page still works when storage is blocked.
function saved(key: string, fallback: string) {
  try { return localStorage.getItem(key) ?? fallback; } catch { return fallback; }
}
function save(key: string, value: string) {
  try { localStorage.setItem(key, value); } catch { /* storage blocked: nothing to keep */ }
}

const COMPARE_ROWS: [string, string, "usd" | "pct" | "x" | "eps"][] = [
  ["revenue_musd", "Revenue ($M)", "usd"], ["revenue_growth", "Revenue growth", "pct"], ["gross_margin", "Gross margin", "pct"],
  ["operating_margin", "Operating margin", "pct"], ["net_margin", "Net margin", "pct"], ["fcf_margin", "Free cash flow margin", "pct"],
  ["roe", "Return on equity", "pct"], ["debt_to_equity", "Debt to equity", "x"], ["cash_conversion", "Cash conversion", "x"],
  ["quarter_revenue_yoy_pct", "Latest quarter revenue YoY", "pct"], ["quarter_operating_margin_pct", "Latest quarter operating margin", "pct"],
  ["eps_diluted", "Diluted EPS ($)", "eps"], ["pe", "P/E at your price", "x"]];

// Two to six companies side by side.
export function Compare({ u, onError }: Props) {
  const [tickers, setTickers] = useState("NVDA, AMD");
  const [prices, setPrices] = useState("");
  const [busy, setBusy] = useState(false);
  const [r, setR] = useState<any>(null);

  async function run() {
    const list = tickers.split(",").map((t) => t.trim()).filter(Boolean);
    const p = Object.fromEntries(prices.split(",").map((s) => s.split("=").map((x) => x.trim()))
      .filter(([k, v]) => k && v && !isNaN(+v)).map(([k, v]) => [k.toUpperCase(), +v]));
    setBusy(true); setR(null);
    try { setR(await post(`/company/${u}/compare`, { tickers: list, prices: p })); } catch (e) { fail(onError)(e); }
    setBusy(false);
  }
  const rows = (r?.rows ?? []).filter((x: any) => !x.error);
  return (
    <div className="card-box">
      <h3>Compare companies</h3>
      <div className="row wrap">
        <input className="grow" value={tickers} onChange={(e) => setTickers(e.target.value)} placeholder="Tickers, comma-separated (2 to 6)" />
        <input value={prices} onChange={(e) => setPrices(e.target.value)} placeholder="Optional prices: NVDA=180, AMD=150" />
        <button className="primary" disabled={busy} onClick={run}>Compare</button>
        {busy && <span className="muted">Reading SEC filings…</span>}
      </div>
      {r && <>
        {r.rows.filter((x: any) => x.error).map((x: any) => <p key={x.ticker} className="muted">{x.ticker}: {x.error}</p>)}
        {rows.length > 0 && <div className="table-wrap"><table>
          <thead><tr><th></th>{rows.map((x: any) => <th key={x.ticker} className="num" title={x.name}>{x.ticker}<br />
            <span className="muted">FY to {x.fiscal_year_end}</span></th>)}</tr></thead>
          <tbody>{COMPARE_ROWS.map(([k, label, kind]) => (
            <tr key={k}><td>{label}</td>{rows.map((x: any) => <td key={x.ticker} className="num">
              {x[k] == null ? "–" : kind === "pct" ? formatNumber(x[k], "pct") : kind === "usd" ? num(x[k], 0) : num(x[k])}</td>)}</tr>))}</tbody>
        </table></div>}
        <p className="muted">{r.note}</p>
      </>}
    </div>
  );
}

const METRICS = ["revenue_yoy_pct", "gross_margin_pct", "operating_margin_pct", "eps_diluted", "eps_vs_forecast_pct", "revenue_vs_forecast_pct"];

// Lock a forecast, then see it against the reported quarter.
export function EarningsReview({ u, onError }: Props) {
  const [ticker, setTicker] = useState(saved("review.ticker", ""));
  const [f, setF] = useState<any>({ quarter_end: "", revenue_musd: "", gross_margin_pct: "", operating_margin_pct: "", eps_diluted: "",
    consensus_eps: "", note: "", rule_metric: "", rule_op: "<", rule_value: "", rule_quarters: "1" });
  const [busy, setBusy] = useState(false);
  const [r, setR] = useState<any>(null);
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.value });

  async function load(withRelease = true) {
    if (!ticker.trim()) return;
    save("review.ticker", ticker);
    setBusy(true);
    try { setR(await get(`/company/${u}/review/${encodeURIComponent(ticker.trim())}?release=${withRelease}`)); } catch (e) { fail(onError)(e); }
    setBusy(false);
  }
  async function lock() {
    const n = (k: string) => (f[k] === "" ? null : +f[k]);
    const rules = f.rule_metric && f.rule_value !== "" ? [{ metric: f.rule_metric, op: f.rule_op, value: +f.rule_value, quarters: +f.rule_quarters }] : [];
    try {
      await post(`/company/${u}/forecasts`, { ticker: ticker.trim(), quarter_end: f.quarter_end, revenue_musd: n("revenue_musd"),
        gross_margin_pct: n("gross_margin_pct"), operating_margin_pct: n("operating_margin_pct"), eps_diluted: n("eps_diluted"),
        consensus_eps: n("consensus_eps"), kill_rules: rules, note: f.note });
      await load(false);
    } catch (e) { fail(onError)(e); }
  }
  async function verdict(id: string, v: string) {
    try { await post(`/company/${u}/verdicts`, { forecast_id: id, verdict: v }); await load(false); } catch (e) { fail(onError)(e); }
  }

  return (
    <div className="card-box">
      <h3>Earnings review</h3>
      <p className="muted">Lock what you expect before a company reports. After its 10-Q is filed, see actual against forecast, driver by driver.
        Only a forecast locked before the filing date counts.</p>
      <div className="row">
        <input value={ticker} onChange={(e) => setTicker(e.target.value)} placeholder="Ticker, e.g. NVDA" />
        <button className="primary" disabled={busy || !ticker.trim()} onClick={() => load()}>Review</button>
        {busy && <span className="muted">Reading SEC filings and the latest release…</span>}
      </div>
      <details>
        <summary>Lock a forecast</summary>
        <div className="row wrap">
          <label>Quarter ends <input type="date" value={f.quarter_end} onChange={set("quarter_end")} /></label>
          <label>Revenue $M <input value={f.revenue_musd} onChange={set("revenue_musd")} /></label>
          <label>Gross margin % <input value={f.gross_margin_pct} onChange={set("gross_margin_pct")} /></label>
          <label>Operating margin % <input value={f.operating_margin_pct} onChange={set("operating_margin_pct")} /></label>
          <label>Diluted EPS $ <input value={f.eps_diluted} onChange={set("eps_diluted")} /></label>
          <label>Consensus EPS $ (optional) <input value={f.consensus_eps} onChange={set("consensus_eps")} /></label>
        </div>
        <div className="row wrap">
          <span>Kill rule (optional):</span>
          <select value={f.rule_metric} onChange={set("rule_metric")}><option value="">none</option>{METRICS.map((m) => <option key={m}>{m}</option>)}</select>
          <select value={f.rule_op} onChange={set("rule_op")}>{["<", "<=", ">", ">="].map((o) => <option key={o}>{o}</option>)}</select>
          <input value={f.rule_value} onChange={set("rule_value")} placeholder="value" />
          <label>quarters in a row <input value={f.rule_quarters} onChange={set("rule_quarters")} /></label>
        </div>
        <div className="row"><input className="grow" value={f.note} onChange={set("note")} placeholder="Thesis in one line" />
          <button disabled={!ticker.trim() || !f.quarter_end} onClick={lock}>Lock forecast</button></div>
      </details>

      {r && <>
        {r.accuracy?.reviewed > 0 && <p>Your EPS forecasts: {r.accuracy.reviewed} reviewed, average miss {formatNumber(r.accuracy.mean_abs_eps_error_pct, "pct")},
          bias {formatNumber(r.accuracy.mean_signed_eps_error_pct, "pct")}, {r.accuracy.within_5pct} within 5%.</p>}
        {r.reviews.length === 0 && <p className="muted">No forecasts locked for {r.ticker} yet.</p>}
        {r.reviews.map((rv: any) => (
          <div key={rv.quarter_end} className="card-box">
            <h4>Quarter ending {rv.quarter_end} · {rv.status}{rv.material ? " · material" : ""}</h4>
            {rv.material_reasons?.map((m: string) => <div key={m}>{m}</div>)}
            {rv.late?.length > 0 && <div className="muted">Locked after the results, not used: {rv.late.join(", ")}</div>}
            {rv.variances && <table>
              <thead><tr><th>Driver</th><th className="num">Forecast</th><th className="num">Actual</th><th className="num">Variance</th></tr></thead>
              <tbody>{rv.variances.map((v: any) => (
                <tr key={v.driver}><td>{v.driver}</td><td className="num">{num(v.forecast)}</td><td className="num">{num(v.actual)}</td>
                  <td className="num">{v.variance == null ? "–" : `${v.variance > 0 ? "+" : ""}${num(v.variance)} ${v.unit}`}</td></tr>))}</tbody>
            </table>}
            {rv.actual?.derived?.length > 0 && <div className="muted">Q4 amounts are the 10-K year minus Q1-Q3.</div>}
            {rv.kill_rules?.map((k: any, i: number) => <div key={i} className={k.hit ? "off-target" : ""}>
              Rule {k.metric} {k.op} {k.value} ({k.quarters} quarter{k.quarters > 1 ? "s" : ""}): {k.hit == null ? "not enough data" : k.hit ? "HIT" : "not hit"}</div>)}
            {rv.release_flags?.length > 0 && <><div>From the <a href={safeHref(rv.release_url)} target="_blank" rel="noreferrer">release</a> (quotes checked):</div>
              <ul>{rv.release_flags.map((i: any, n: number) => <li key={n}>{i.kind}: “{i.quote}”</li>)}</ul></>}
            {rv.forecast && <div className="row">
              <span>Your verdict{rv.verdict ? `: ${rv.verdict.verdict}` : ""}</span>
              {["intact", "weakened", "broken"].map((v) => <button key={v} onClick={() => verdict(rv.forecast.id, v)}>{v}</button>)}
            </div>}
          </div>))}
        <p className="muted">{r.label}</p>
      </>}
    </div>
  );
}

const DEFAULT_BOOK = "NVDA 100 180 Tech\nAMD 150 150 Tech\nJNJ 120 160 Healthcare";

// The trades your target weights imply, checked against your limits. The book stays in this browser.
export function Plan({ u, onError }: Props) {
  const [book, setBook] = useState(saved("plan.book", DEFAULT_BOOK));
  const [cash, setCash] = useState(saved("plan.cash", "5000"));
  const [targets, setTargets] = useState(saved("plan.targets", ""));
  const [lim, setLim] = useState(() => {
    try { return JSON.parse(saved("plan.limits", "")); } catch { return { max_position_pct: "40", max_sector_pct: "60", min_cash_pct: "2", min_trade: "500" }; }
  });
  const [r, setR] = useState<any>(null);

  async function run() {
    save("plan.book", book); save("plan.cash", cash); save("plan.targets", targets); save("plan.limits", JSON.stringify(lim));
    const positions = book.split("\n").map((l) => l.trim().split(/\s+/)).filter((p) => p.length >= 3)
      .map(([ticker, shares, price, ...sector]) => ({ ticker, shares: +shares, price: +price, sector: sector.join(" ") }));
    const t = Object.fromEntries(targets.split(",").map((s) => s.split("=").map((x) => x.trim())).filter(([k, v]) => k && v).map(([k, v]) => [k.toUpperCase(), +v]));
    const n = (k: string) => (lim[k] === "" || lim[k] == null ? null : +lim[k]);
    try {
      setR(await post(`/company/${u}/plan`, { positions, cash: +cash || 0, targets: t, max_position_pct: n("max_position_pct"),
        max_sector_pct: n("max_sector_pct"), min_cash_pct: n("min_cash_pct"), min_trade: n("min_trade") ?? 0 }));
    } catch (e) { fail(onError)(e); }
  }
  const setL = (k: string) => (e: any) => setLim({ ...lim, [k]: e.target.value });
  return (
    <div className="card-box">
      <h3>Rebalance plan within limits</h3>
      <p className="muted">One holding per line: ticker, shares, price, sector. Holdings without a target stay where they are; the rest is cash.</p>
      <textarea rows={5} value={book} onChange={(e) => setBook(e.target.value)} />
      <div className="row wrap">
        <label>Cash <input value={cash} onChange={(e) => setCash(e.target.value)} /></label>
        <input className="grow" value={targets} onChange={(e) => setTargets(e.target.value)} placeholder="Targets %: NVDA=35, AMD=30, JNJ=30" />
      </div>
      <div className="row wrap">
        <label>Max position % <input value={lim.max_position_pct} onChange={setL("max_position_pct")} /></label>
        <label>Max sector % <input value={lim.max_sector_pct} onChange={setL("max_sector_pct")} /></label>
        <label>Min cash % <input value={lim.min_cash_pct} onChange={setL("min_cash_pct")} /></label>
        <label>Min trade <input value={lim.min_trade} onChange={setL("min_trade")} /></label>
        <button className="primary" onClick={run}>Check plan</button>
      </div>
      {r && <>
        <div className="table-wrap"><table>
          <thead><tr><th>Holding</th><th>Sector</th><th className="num">Weight</th><th className="num">Target</th><th className="num">Trade</th>
            <th className="num">Shares</th><th className="num">Weight after</th></tr></thead>
          <tbody>{r.rows.map((x: any) => (
            <tr key={x.ticker}><td>{x.ticker}</td><td>{x.sector}</td><td className="num">{num(x.weight_pct)}%</td>
              <td className="num">{x.target_pct == null ? "–" : `${num(x.target_pct)}%`}</td>
              <td className="num">{x.skipped_small ? "below minimum" : num(x.trade)}</td><td className="num">{num(x.shares)}</td>
              <td className="num">{num(x.weight_after_pct)}%</td></tr>))}
            <tr><td>Cash</td><td></td><td className="num">{num((100 * r.cash) / r.total_value)}%</td><td></td><td></td><td></td>
              <td className="num">{num(r.cash_after_pct)}%</td></tr></tbody>
        </table></div>
        <p>Sectors after: {r.sectors.map((s: any) => `${s.sector} ${num(s.after_pct)}%`).join(" · ")} · sold {num(r.sold_pct)}% of the book</p>
        {r.breaches.length ? <ul className="off-target">{r.breaches.map((b: string) => <li key={b}>{b}</li>)}</ul> : <p>All limits pass.</p>}
        <p className="muted">{r.label}</p>
      </>}
    </div>
  );
}
