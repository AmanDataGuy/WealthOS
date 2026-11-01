// Strategy.tsx - the strategy lab page: write an idea in plain English (read back exactly as it will run, the
// description is written by code, not the AI) or upload a Python file defining weights(close) (run isolated, see
// backend features/sandbox.py); run it on your prices and compare with buy-and-hold. The chat below can backtest too.
// Every result carries the "not advice" label; nothing here places orders.
import { useEffect, useRef, useState } from "react";
import * as echarts from "echarts";
import Chat from "./Chat";
import { CHART, formatNumber, get, post, upload } from "./api";

type Parsed = { spec: any; description: string; generated_by: string; label: string };

const ROWS: [string, string, string][] = [  // [key, label, number format]
  ["final_value", "Final value", "inr"], ["total_return_pct", "Total return", "pct"], ["cagr_pct", "CAGR", "pct"],
  ["volatility_pct", "Volatility (yearly)", "pct"], ["sharpe", "Sharpe ratio", "plain"], ["max_drawdown_pct", "Max drawdown", "pct"],
  ["time_invested_pct", "Time invested", "pct"], ["hit_rate_pct", "Winning days", "pct"], ["turnover_per_year", "Turnover per year", "plain"],
];

const ws = "default";

// The Strategy lab tab: idea box or Python file, plain-English readback, results, past runs and the chat.
export default function Strategy({ u, boards, onError }: { u: string; boards: { id: string; title: string }[]; onError: (m: string) => void }) {
  const [text, setText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [symbols, setSymbols] = useState("");
  const [parsed, setParsed] = useState<Parsed | null>(null);
  const [result, setResult] = useState<any>(null);
  const [runs, setRuns] = useState<any[]>([]);
  const [busy, setBusy] = useState("");

  // Load the past runs.
  const loadRuns = () => get(`/strategy/${u}/runs`).then(setRuns).catch((e) => onError(e.message));
  useEffect(() => { loadRuns(); }, [u]);

  // Run an action with a status message and report any error.
  async function act(label: string, fn: () => Promise<void>) {
    setBusy(label);
    try { await fn(); } catch (e) { onError((e as Error).message); }
    setBusy("");
  }
  // Turn the idea into a strategy the code can run.
  const parse = () => act("Reading your idea…", async () => { setResult(null); setParsed(await post(`/strategy/${u}/${ws}/parse`, { text })); });
  // Run the parsed strategy.
  const run = () => act("Running…", async () => {
    setResult(await post(`/strategy/${u}/${ws}/run`, { spec: parsed!.spec, generated_by: parsed!.generated_by }));
    await loadRuns();
  });
  // Backtest an uploaded Python strategy file.
  const runPython = () => act("Running your strategy…", async () => {
    const form = new FormData();
    form.append("file", file!); form.append("symbols", symbols);
    setParsed(null); setResult(await upload(`/strategy/${u}/${ws}/python`, form));
    await loadRuns();
  });
  // Open a past run.
  const open = (id: string) => act("Loading…", async () => { setParsed(null); setResult(await get(`/strategy/${u}/runs/${id}`)); });

  return (
    <div className="lab">
      <section className="lab-main">
        <h2>Strategy lab</h2>
        <p className="muted">Describe when to buy and when to sell. You will see exactly what will be tested before it runs.</p>
        <textarea rows={3} value={text} onChange={(e) => setText(e.target.value)}
          placeholder="e.g. Buy NIFTYBEES when its 50-day average crosses above the 200-day average, sell when it crosses back below" />
        <div className="row">
          <button className="primary" disabled={!text.trim() || !!busy} onClick={parse}>Read my idea</button>
          {busy && <span className="muted">{busy}</span>}
        </div>

        <div className="card-box">
          <h3>Or upload a Python strategy</h3>
          <p className="muted">A .py file defining <code>weights(close)</code>: daily closes in (one column per stock), the weight to
            hold in each stock out (0 to 1, a day adding up to at most 1). It runs isolated: pandas, numpy and math only,
            no files or network, 30 seconds at most.</p>
          <div className="row wrap">
            <input type="file" accept=".py" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
            <input className="grow" placeholder="Stocks it trades, e.g. INFY, TCS" value={symbols} onChange={(e) => setSymbols(e.target.value)} />
            <button className="primary" disabled={!file || !symbols.trim() || !!busy} onClick={runPython}>Run backtest</button>
          </div>
        </div>

        {parsed && (
          <div className="card-box">
            <h3>This is what will be tested</h3>
            <p>{parsed.description}</p>
            <details><summary className="muted">Rule as data</summary><pre>{JSON.stringify(parsed.spec, null, 2)}</pre></details>
            <div className="row"><button className="primary" disabled={!!busy} onClick={run}>Run on past prices</button></div>
          </div>
        )}

        {result && (
          <div className="card-box">
            <div className="notice">{result.label}</div>
            <p>{result.description}</p>
            <Curve points={result.equity_curve} />
            <table className="metrics">
              <thead><tr><th></th><th className="num">This rule</th><th className="num">Buy and hold</th></tr></thead>
              <tbody>{ROWS.map(([k, label, f]) => (
                <tr key={k}><td>{label}</td><td className="num">{formatNumber(result.metrics[k], f)}</td>
                  <td className="num">{k in result.buy_and_hold ? formatNumber(result.buy_and_hold[k], f) : ""}</td></tr>
              ))}</tbody>
            </table>
            <p className="muted">
              Period {result.run_card.period.start} to {result.run_card.period.end} · rules tried on these stocks and dates: {result.run_card.n_trials}
              {" · "}chance the Sharpe ratio is real (deflated): {result.run_card.deflated_sharpe == null ? "–" : formatNumber(100 * result.run_card.deflated_sharpe, "pct")}
            </p>
            {result.warnings.map((w: string) => <div key={w} className="error">{w}</div>)}
          </div>
        )}
        <Chat u={u} boards={boards} onPinned={() => {}} compact placeholder="Ask to backtest an idea, e.g. INFY when RSI is below 30, sell above 70" />
      </section>

      <aside className="lab-runs">
        <h3>Earlier runs</h3>
        {!runs.length && <p className="muted">None yet.</p>}
        {runs.map((r) => (
          <button key={r.id} className="run-item" onClick={() => open(r.id)}>
            <b>{r.universe.join(", ")}</b>
            <span className="muted">{r.period.start} to {r.period.end}</span>
            <span className="muted">{new Date(r.created_at).toLocaleString()}</span>
          </button>
        ))}
      </aside>
    </div>
  );
}

// Strategy vs buy-and-hold value over time.
function Curve({ points }: { points: { date: string; strategy: number; buy_and_hold: number }[] }) {
  const el = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const chart = echarts.init(el.current!);
    const axis = CHART.axisStyle();
    chart.setOption({
      color: [CHART.colors[1], CHART.colors[0]], textStyle: { color: CHART.text, fontSize: 12 }, tooltip: { ...CHART.tooltip, trigger: "axis" },
      legend: { bottom: 0, textStyle: { color: CHART.text } }, grid: { left: 8, right: 8, top: 8, bottom: 32, containLabel: true },
      xAxis: { type: "category", data: points.map((p) => p.date), ...axis },
      yAxis: { type: "value", scale: true, ...axis, axisLabel: { color: CHART.muted, formatter: (v: number) => "₹" + v.toLocaleString("en-IN") } },
      series: [{ name: "This rule", type: "line", showSymbol: false, data: points.map((p) => p.strategy) },
               { name: "Buy and hold", type: "line", showSymbol: false, data: points.map((p) => p.buy_and_hold) }],
    });
    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(el.current!);
    return () => { ro.disconnect(); chart.dispose(); };
  }, [points]);
  return <div ref={el} className="curve" />;
}
