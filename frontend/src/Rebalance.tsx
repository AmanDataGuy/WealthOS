// Rebalance.tsx - your holdings' current mix vs the target mix you set: drift per holding (in percentage points) and
// the rupee change that would bring each back to target. Arithmetic on your own targets, never advice.
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
        <Chat u={u} boards={boards} onPinned={() => {}} compact placeholder="Ask about your mix, e.g. which holdings are off target?" />
      </section>
    </div>
  );
}
