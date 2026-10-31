// Tax.tsx - old vs new regime for one financial year (tax.py behind /tax). Salary and deductions come from the form;
// capital gains come from the workspace's tradebook. Every rate is from a cited rules file; educational, not advice.
import { useState } from "react";
import { formatNumber, post } from "./api";

type Props = { u: string; ws: string; onError: (m: string) => void };
const ROWS: [string, string][] = [
  ["gross_income", "Gross income"], ["standard_deduction", "Standard deduction"], ["deductions", "80C / 80D deductions"],
  ["normal_income", "Taxable income (excl. capital gains)"], ["slab_tax", "Tax on slabs"], ["capital_gains_tax", "Tax on capital gains"],
  ["rebate_87a", "Rebate 87A"], ["surcharge", "Surcharge"], ["cess", "Health & education cess (4%)"], ["total_tax", "Total tax"],
];

// The Tax tab: form and old-vs-new regime result.
export default function Tax({ u, ws, onError }: Props) {
  const [form, setForm] = useState({ fy: "2025-26", salary: 1500000, other_income: 0, deductions_80c: 150000, deductions_80d: 0, age: "below_60" });
  const [out, setOut] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  // Make an input handler that stores a number in the form.
  const num = (k: string) => (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [k]: Number(e.target.value) || 0 });

  // Work out the tax from the form.
  async function run() {
    setBusy(true);
    try { setOut(await post(`/tax/${u}/${ws}`, form)); } catch (e) { onError((e as Error).message); }
    setBusy(false);
  }

  return (
    <div className="lab"><div className="lab-main">
      <h2>Tax: old vs <em>new</em> regime</h2>
      <p className="muted">Capital gains are taken from this workspace's tradebook (FIFO, listed equity). Educational estimate, not tax advice.</p>
      <div className="card-box tax-form">
        <label>Financial year<select value={form.fy} onChange={(e) => setForm({ ...form, fy: e.target.value })}>
          <option value="2025-26">2025-26</option><option value="2026-27">2026-27 (rates not yet verified)</option></select></label>
        <label>Salary (gross, yearly)<input type="number" value={form.salary} onChange={num("salary")} /></label>
        <label>Other income (interest, rent …)<input type="number" value={form.other_income} onChange={num("other_income")} /></label>
        <label>80C investments (old regime)<input type="number" value={form.deductions_80c} onChange={num("deductions_80c")} /></label>
        <label>80D health insurance (old regime)<input type="number" value={form.deductions_80d} onChange={num("deductions_80d")} /></label>
        <label>Age<select value={form.age} onChange={(e) => setForm({ ...form, age: e.target.value })}>
          <option value="below_60">Below 60</option><option value="60_to_80">60 to 80</option><option value="above_80">80 or more</option></select></label>
        <div className="row"><button className="primary" disabled={busy} onClick={run}>{busy ? "Calculating…" : "Compare regimes"}</button></div>
      </div>

      {out && (
        <div className="card-box">
          <p><b>The {out.lower} regime costs ₹{formatNumber(out.difference, "plain")} less</b> for FY {out.fy}
            {out.headroom_80c > 0 && out.lower === "new" && <span className="muted"> · under the old regime you still have ₹{formatNumber(out.headroom_80c, "plain")} of 80C room</span>}</p>
          <div className="table-wrap"><table>
            <thead><tr><th></th><th className="num">New regime</th><th className="num">Old regime</th></tr></thead>
            <tbody>{ROWS.map(([k, label]) => (
              <tr key={k} className={k === "total_tax" ? "total" : ""}><td>{label}{k === "surcharge" && ` (${out.new.surcharge_pct}% / ${out.old.surcharge_pct}%)`}</td>
                <td className="num">{formatNumber(out.new[k], "plain")}</td><td className="num">{formatNumber(out.old[k], "plain")}</td></tr>
            ))}</tbody>
          </table></div>
          <div className="muted">Capital gains from your tradebook: short term ₹{formatNumber(out.capital_gains.short_term, "plain")},
            long term ₹{formatNumber(out.capital_gains.long_term, "plain")} (₹{formatNumber(out.new.ltcg_exempt, "plain")} exempt)</div>
          {out.warnings.map((w: string) => <div key={w} className="muted">Note: {w}</div>)}
          <details><summary className="muted">Where the rates come from</summary>
            <ul className="muted">{[...out.sources, ...out.notes].map((s: string) => <li key={s}>{s}</li>)}</ul></details>
          <div className="disclaimer">{out.disclaimer}</div>
        </div>
      )}
    </div></div>
  );
}
