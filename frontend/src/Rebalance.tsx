// Rebalance.tsx - your holdings' current mix vs the target mix you set: drift per holding (in percentage points) and
// the rupee change that would bring each back to target. Arithmetic on your own targets, never advice.
// A risk panel applies today's holdings to past prices (volatility, drawdown, VaR, risk shares); backward-looking.
// The chat below can answer questions about it ("which holdings are off target?").
import { useEffect, useState } from "react";
import Chat from "./Chat";
import { formatNumber, get, put } from "./api";

type Props = { u: string; boards: { id: string; title: string }[]; onError: (m: string) => void };

export default function Rebalance({ u, boards, onError }: Props) {
  const [data, setData] = useState<any>(null);
  const [missing, setMissing] = useState("");
  const [targets, setTargets] = useState<Record<string, string>>({});
  // Load current vs target; keep the typed targets in step with what's saved.
  const load = async () => {
    try {
      const d = await get(`/rebalance/${u}/default`);
      setData(d); setMissing("");
      setTargets(Object.fromEntries(d.rows.map((r: any) => [r.holding, r.target_pct == null ? "" : String(r.target_pct)])));
    } catch (e) { setMissing((e as Error).message); }
  };
  useEffect(() => { load(); }, [u]);
  const [risk, setRisk] = useState<any>(null);
  const [riskNote, setRiskNote] = useState("");
  // Load the risk report; a refusal (no prices, too little history) is shown as a note.
  useEffect(() => {
    get(`/strategy/${u}/default/risk`).then((r) => { setRisk(r); setRiskNote(""); },
      (e) => { setRisk(null); setRiskNote((e as Error).message); });
  }, [u]);

  // Save the typed target mix (blank = no target).
  async function save() {
    try {
      await put(`/rebalance/${u}/targets`, { targets: Object.fromEntries(
        Object.entries(targets).filter(([, v]) => v.trim()).map(([k, v]) => [k, Number(v)])) });
      await load();
    } catch (e) { onError((e as Error).message); }
  }
  const total = Object.values(targets).reduce((s, v) => s + (Number(v) || 0), 0);

  return (
    <div className="lab">
      <section className="lab-main">
        <h2>Rebalancing</h2>
        <p className="muted">Set the mix you want; see how far each holding has drifted from it. This is arithmetic on your own targets, not advice.</p>
        {missing && <div className="card-box"><p>{missing}</p><p className="muted">Upload a holdings export or CAS from the chat page (+ Query your own data).</p></div>}
        {data && (
          <div className="card-box">
            <div className="row"><b>Portfolio value {formatNumber(data.total_value, "inr")}</b><span className="spacer" />
              <span className="muted">Targets add up to {formatNumber(total, "plain")}%</span>
              <button className="primary" onClick={save}>Save targets</button></div>
            <div className="table-wrap"><table>
              <thead><tr><th>Holding</th><th className="num">Value</th><th className="num">Now</th><th className="num">Target %</th>
                <th className="num">Drift (pts)</th><th className="num">Change to target</th></tr></thead>
              <tbody>{data.rows.map((r: any) => (
                <tr key={r.holding} className={r.off_target ? "off-target" : ""}>
                  <td>{r.holding}</td>
                  <td className="num">{formatNumber(r.value, "inr")}</td>
                  <td className="num">{formatNumber(r.current_pct, "pct")}</td>
                  <td className="num"><input className="pct-input" inputMode="decimal" value={targets[r.holding] ?? ""}
                    onChange={(e) => setTargets({ ...targets, [r.holding]: e.target.value })} /></td>
                  <td className="num">{r.drift_pts == null ? "" : formatNumber(r.drift_pts, "plain")}</td>
                  <td className="num">{r.change_to_target == null ? "" : formatNumber(r.change_to_target, "inr")}</td>
                </tr>
              ))}</tbody>
            </table></div>
            {data.warnings.map((w: string) => <div key={w} className="muted">{w}</div>)}
          </div>
        )}
        {data && (riskNote || risk) && (
          <div className="card-box">
            <h3>Risk of today's holdings</h3>
            {riskNote && <p className="muted">{riskNote}</p>}
            {risk && <>
              <div className="notice">{risk.label}</div>
              <p className="muted">{risk.period.start} to {risk.period.end}, {risk.period.days} days, on {formatNumber(risk.priced_value, "inr")} of holdings with prices</p>
              <div className="table-wrap"><table className="metrics"><tbody>
                <tr><td>Volatility (a year)</td><td className="num">{formatNumber(risk.volatility_pct, "pct")}</td></tr>
                <tr><td>Max drawdown ({risk.max_drawdown.peak} to {risk.max_drawdown.trough}{risk.max_drawdown.recovered ? `, back by ${risk.max_drawdown.recovered}` : ", not yet recovered"})</td>
                  <td className="num">{formatNumber(risk.max_drawdown.pct, "pct")}</td></tr>
                <tr><td>Worst month ({risk.worst_month.month})</td><td className="num">{formatNumber(risk.worst_month.return_pct, "pct")}</td></tr>
                <tr><td>1-day VaR 95%</td><td className="num">{formatNumber(risk.var_95_1d.pct, "pct")} · {formatNumber(risk.var_95_1d.inr, "inr")}</td></tr>
                <tr><td>1-day CVaR 95% (average of the worst 5% of days)</td><td className="num">{formatNumber(risk.cvar_95_1d.pct, "pct")} · {formatNumber(risk.cvar_95_1d.inr, "inr")}</td></tr>
              </tbody></table></div>
              <div className="table-wrap"><table>
                <thead><tr><th>Holding</th><th className="num">Weight</th><th className="num">Share of risk</th></tr></thead>
                <tbody>{risk.holdings.map((h: any) => (
                  <tr key={h.holding}><td>{h.holding}</td><td className="num">{formatNumber(h.weight_pct, "pct")}</td>
                    <td className="num">{formatNumber(h.risk_share_pct, "pct")}</td></tr>
                ))}</tbody>
              </table></div>
              {risk.top_correlations.length > 0 && <p className="muted">Most correlated: {risk.top_correlations.map((c: any) =>
                `${c.a} / ${c.b} ${formatNumber(c.correlation, "plain")}`).join(" · ")}</p>}
              {risk.left_out.map((x: any) => <div key={x.holding} className="muted">Left out: {x.holding} ({formatNumber(x.share_pct, "pct")} of value), {x.reason}</div>)}
              {risk.warnings.map((w: string) => <div key={w} className="muted">{w}</div>)}
            </>}
          </div>
        )}
        <Chat u={u} boards={boards} onPinned={() => {}} compact placeholder="Ask about your mix, e.g. which holdings are off target?" />
      </section>
    </div>
  );
}
