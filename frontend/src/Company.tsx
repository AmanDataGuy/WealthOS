// Company.tsx - company analyst: type a US-listed company or ticker, get an Excel model built from its SEC filings.
// Shows the key lines, the accounting tie-outs (pass / fail), the DCF value and what the one AI step read from the
// earnings release (each figure verified against the release text or flagged). Banks and insurers are refused.
// Sub-tabs add the earnings loop: compare companies, earnings review, rebalance plan (CompanyLoop.tsx).
import { useState } from "react";
import { download, formatNumber, post, safeHref } from "./api";
import { Compare, EarningsReview, Plan } from "./CompanyLoop";

type Props = { u: string; onError: (m: string) => void };
const KEY_LINES: [string, string][] = [["revenue", "Revenue"], ["gross_profit", "Gross profit"], ["operating_income", "Operating income"],
  ["net_income", "Net income"], ["cfo", "Operating cash flow"], ["fcf", "Free cash flow"], ["total_assets", "Total assets"],
  ["total_debt", "Total debt"], ["total_equity", "Total equity"]];
const usd = (v: number | null | undefined) => (v == null ? "–" : "$" + v.toLocaleString("en-US", { maximumFractionDigits: 0 }));

// The Company tab: one input, a run button, then the results and the workbook download.
export default function Company({ u, onError }: Props) {
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [r, setR] = useState<any>(null);
  const [view, setView] = useState("model");

  // Build the model (or get the list of companies to choose from).
  async function build(q = query) {
    setBusy(true); setR(null);
    try { setR(await post(`/company/${u}/build`, { query: q })); } catch (e) { onError((e as Error).message); }
    setBusy(false);
  }
  const years: string[] = r?.statements?.years ?? [];
  const v = r?.valuation;

  return (
    <div className="lab">
      <section className="lab-main">
        <h2>Company analyst</h2>
        <div className="row">{[["model", "Build model"], ["compare", "Compare"], ["review", "Earnings review"], ["plan", "Rebalance plan"]].map(([k, label]) => (
          <button key={k} className={view === k ? "primary" : ""} onClick={() => setView(k)}>{label}</button>))}</div>
        {view === "compare" && <Compare u={u} onError={onError} />}
        {view === "review" && <EarningsReview u={u} onError={onError} />}
        {view === "plan" && <Plan u={u} onError={onError} />}
        {view === "model" && <>
        <p className="muted">A US-listed company's last five fiscal years from its SEC filings, as an Excel model: every reported number
          copied from a 10-K, every derived number a live formula. Not investment advice.</p>
        <div className="row">
          <input className="grow" value={query} onChange={(e) => setQuery(e.target.value)} onKeyDown={(e) => e.key === "Enter" && query.trim() && build()}
            placeholder="Company name or ticker, e.g. Apple or NVDA" />
          <button className="primary" disabled={!query.trim() || busy} onClick={() => build()}>Build model</button>
          {busy && <span className="muted">Reading SEC filings… (up to a minute)</span>}
        </div>

        {r?.status === "choose" && (
          <div className="card-box">
            <p>{r.candidates.length ? "Several companies match. Pick one:" : "No US-listed company matches that name or ticker."}</p>
            <div className="row wrap">{r.candidates.map((c: any) => (
              <button key={c.cik} onClick={() => { setQuery(c.ticker); build(c.ticker); }}>{c.ticker} · {c.name}</button>))}</div>
          </div>
        )}

        {r?.status === "done" && (<>
          <div className="card-box">
            <div className="row"><h3>{r.company.name} ({r.company.ticker})</h3><span className="spacer" />
              <button className="primary" onClick={() => download(`/company/${u}/runs/${encodeURIComponent(r.run_id)}/workbook.xlsx`,
                `${r.company.ticker}-model.xlsx`).catch((e) => onError(e.message))}>Download Excel model</button></div>
            <div className="table-wrap"><table>
              <thead><tr><th>$ millions</th>{years.map((y) => <th key={y} className="num">{y}</th>)}</tr></thead>
              <tbody>{KEY_LINES.map(([k, label]) => (
                <tr key={k}><td>{label}</td>{years.map((y, i) => <td key={y} className="num">{usd(r.statements.values[k]?.[i])}</td>)}</tr>))}</tbody>
            </table></div>
            {r.notes.map((n: string) => <div key={n} className="muted">{n}</div>)}
          </div>

          <div className="card-box">
            <h3>Tie-outs</h3>
            <table>
              <thead><tr><th>Check</th><th>Result</th><th className="num">Largest difference ($M)</th></tr></thead>
              <tbody>{r.tieouts.map((c: any) => (
                <tr key={c.check} className={c.status === "fail" ? "off-target" : ""}>
                  <td>{c.description}</td>
                  <td>{c.status} ({c.years_passed}/{c.years_tested} {c.check === "workbook_recalculates" ? "cells" : "years"})</td>
                  <td className="num">{c.status === "skipped" ? "" : formatNumber(c.max_abs_delta, "plain")}</td>
                </tr>))}</tbody>
            </table>
          </div>

          <div className="card-box">
            <h3>Valuation (DCF)</h3>
            {v.skip ? <p className="muted">{v.skip}</p> : <p>
              Value per share ${formatNumber(v.per_share, "plain")} · enterprise value {usd(v.enterprise_value)}M · growth{" "}
              {formatNumber(100 * v.inputs.growth, "pct")}, discount rate {formatNumber(100 * v.inputs.discount_rate, "pct")}, terminal growth{" "}
              {formatNumber(100 * v.inputs.terminal_growth, "pct")}. Change the blue assumptions in the workbook.</p>}
          </div>

          <div className="card-box">
            <h3>Earnings release (read by AI, checked by code)</h3>
            {r.releases.error ? <p className="muted">{r.releases.error}</p> : <>
              <p className="muted">{r.releases.verified} of {r.releases.items.length} figures were found word for word in the{" "}
                <a href={safeHref(r.releases.url)} target="_blank" rel="noreferrer">release</a>; the rest are flagged and used nowhere.</p>
              <table>
                <thead><tr><th>Item</th><th className="num">Value</th><th>Unit</th><th>Period</th><th>Status</th></tr></thead>
                <tbody>{r.releases.items.map((i: any, n: number) => (
                  <tr key={n} className={i.verified ? "" : "off-target"} title={i.quote}>
                    <td>{i.name}</td><td className="num">{formatNumber(i.value, "plain")}</td><td>{i.unit}</td><td>{i.period}</td>
                    <td>{i.verified ? "verified" : "not found in release"}</td>
                  </tr>))}</tbody>
              </table></>}
          </div>
        </>)}
        </>}
      </section>
    </div>
  );
}
