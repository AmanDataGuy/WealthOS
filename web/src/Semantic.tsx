// Semantic.tsx - the Semantic Builder: what the workspace's columns mean, how its tables join, and its
// own metrics. The server checks everything against the real tables; a metric is test-run before it is saved, and
// then its value for this financial year is shown so you can sanity-check it.
import { useEffect, useState } from "react";
import { del, formatNumber, get, post, put } from "./api";

type Col = { type: string; role: string; description: string; saved: boolean };
type Model = { columns: Record<string, Record<string, Col>>; relationships: { left: string; right: string; kind: string }[];
  workspace_metrics: Record<string, any> };

const ROLES = ["date", "measure", "dimension", "id"];
const BLANK = { key: "", name: "", table: "", date: "", sql: "", unit: "INR", time_semantics: "flow" };

// The Semantic tab: column meanings, joins and metrics.
export default function Semantic({ u, ws, onError }: { u: string; ws: string; onError: (m: string) => void }) {
  const [model, setModel] = useState<Model | null>(null);
  const [edits, setEdits] = useState<Record<string, Record<string, Col>>>({});
  const [rel, setRel] = useState({ left: "", right: "", kind: "many_to_one" });
  const [metric, setMetric] = useState(BLANK);
  const [tested, setTested] = useState("");
  const base = `/semantic/${u}/${ws}`;

  // Load the semantic model and drop unsaved edits.
  const load = () => get(base).then((m) => { setModel(m); setEdits({}); }).catch((e) => onError(e.message));
  useEffect(() => { setMetric(BLANK); setTested(""); load(); }, [u, ws]);
  // Run an action, reload the model and report any error.
  const act = async (fn: () => Promise<unknown>) => { try { await fn(); await load(); } catch (e) { onError((e as Error).message); } };

  if (!model) return <div className="empty">Loading…</div>;
  const tables = Object.keys(model.columns);
  if (!tables.length) return <div className="empty">This workspace has no tables yet. Add data in the Data tab first.</div>;
  const allCols = tables.flatMap((t) => Object.keys(model.columns[t]).map((c) => `${t}.${c}`));
  // A column's meaning, with unsaved edits applied.
  const col = (t: string, c: string) => edits[t]?.[c] ?? model.columns[t][c];
  // Record an unsaved edit to a column.
  const setCol = (t: string, c: string, patch: Partial<Col>) =>
    setEdits({ ...edits, [t]: { ...(edits[t] ?? {}), [c]: { ...col(t, c), ...patch } } });
  const dateCols = metric.table ? Object.entries(model.columns[metric.table] ?? {}).filter(([, v]) => v.role === "date").map(([k]) => k) : [];

  // Save (the server parses, guards and test-runs the metric first), then show its value this FY.
  async function saveMetric() {
    setTested("");
    const { key, ...spec } = metric;
    await put(`${base}/metrics/${key}`, spec);
    const r = await post("/query/metric", { user_id: decodeURIComponent(u), workspace_id: ws, metrics: [key], time: "fy" });
    setTested(`${spec.name || key} this financial year: ${formatNumber(r.rows[0]?.[key], undefined, spec.unit)}`);
  }

  return (
    <div className="lab">
      <section className="lab-main">
        <h2>Semantic Builder</h2>
        <p className="muted">Tell WealthOS what your columns mean, how tables join, and how your own metrics are calculated. The assistant and dashboards use these.</p>

        {tables.map((t) => (
          <div key={t} className="card-box">
            <h3>{t}</h3>
            <table>
              <thead><tr><th>Column</th><th>Type</th><th>Meaning</th><th>Description</th></tr></thead>
              <tbody>{Object.keys(model.columns[t]).map((c) => (
                <tr key={c}>
                  <td>{c}</td><td className="muted">{col(t, c).type}</td>
                  <td><select value={col(t, c).role} onChange={(e) => setCol(t, c, { role: e.target.value })}>
                    {ROLES.map((r) => <option key={r}>{r}</option>)}</select></td>
                  <td><input className="grow" value={col(t, c).description} placeholder={col(t, c).saved ? "" : "suggested, not saved yet"}
                    onChange={(e) => setCol(t, c, { description: e.target.value })} /></td>
                </tr>))}</tbody>
            </table>
            <div className="row"><span className="spacer" />
              <button className="primary" onClick={() => act(() => put(`${base}/columns`, {
                columns: { [t]: Object.fromEntries(Object.keys(model.columns[t]).map((c) => [c, { role: col(t, c).role, description: col(t, c).description }])) } }))}>
                Save meanings</button></div>
          </div>
        ))}

        <div className="card-box">
          <h3>Relationships</h3>
          {model.relationships.map((r, i) => (
            <div key={i} className="row"><span>{r.left} → {r.right}</span><span className="muted">{r.kind.replace(/_/g, " ")}</span>
              <span className="spacer" />
              <button onClick={() => act(() => put(`${base}/relationships`, { relationships: model.relationships.filter((_, j) => j !== i) }))}>Remove</button>
            </div>))}
          <div className="row">
            <select value={rel.left} onChange={(e) => setRel({ ...rel, left: e.target.value })}>
              <option value="">From column…</option>{allCols.map((c) => <option key={c}>{c}</option>)}</select>
            <span>→</span>
            <select value={rel.right} onChange={(e) => setRel({ ...rel, right: e.target.value })}>
              <option value="">To column…</option>{allCols.map((c) => <option key={c}>{c}</option>)}</select>
            <select value={rel.kind} onChange={(e) => setRel({ ...rel, kind: e.target.value })}>
              {["many_to_one", "one_to_one", "one_to_many"].map((k) => <option key={k} value={k}>{k.replace(/_/g, " ")}</option>)}</select>
            <button disabled={!rel.left || !rel.right} onClick={() => act(async () => {
              await put(`${base}/relationships`, { relationships: [...model.relationships, rel] });
              setRel({ left: "", right: "", kind: "many_to_one" });
            })}>Add</button>
          </div>
        </div>

        <div className="card-box">
          <h3>Metrics</h3>
          {Object.entries(model.workspace_metrics).map(([k, m]) => (
            <div key={k} className="row"><b>{m.name}</b><span className="muted">{k} = {m.sql} on {m.shape} by {m.date}</span>
              <span className="spacer" />
              <button onClick={() => setMetric({ key: k, name: m.name, table: m.shape, date: m.date, sql: m.sql, unit: m.unit, time_semantics: m.time_semantics })}>Edit</button>
              <button onClick={() => act(() => del(`${base}/metrics/${k}`))}>Delete</button>
            </div>))}
          <div className="row wrap">
            <input placeholder="id, e.g. gross_sales" value={metric.key} onChange={(e) => setMetric({ ...metric, key: e.target.value })} />
            <input placeholder="Name, e.g. Gross sales" value={metric.name} onChange={(e) => setMetric({ ...metric, name: e.target.value })} />
            <select value={metric.table} onChange={(e) => setMetric({ ...metric, table: e.target.value, date: "" })}>
              <option value="">Table…</option>{tables.map((t) => <option key={t}>{t}</option>)}</select>
            <select value={metric.date} onChange={(e) => setMetric({ ...metric, date: e.target.value })}>
              <option value="">Date column…</option>{dateCols.map((c) => <option key={c}>{c}</option>)}</select>
          </div>
          <div className="row wrap">
            <input className="grow" placeholder="SQL, e.g. SUM(amount)" value={metric.sql} onChange={(e) => setMetric({ ...metric, sql: e.target.value })} />
            <select value={metric.unit} onChange={(e) => setMetric({ ...metric, unit: e.target.value })}>
              {["INR", "count", "pct", "ratio"].map((x) => <option key={x}>{x}</option>)}</select>
            <select value={metric.time_semantics} onChange={(e) => setMetric({ ...metric, time_semantics: e.target.value })}>
              <option value="flow">adds up over a period</option><option value="point_in_time">latest value</option></select>
            <button className="primary" disabled={!metric.key || !metric.table || !metric.date || !metric.sql}
              onClick={() => act(saveMetric)}>Save and test</button>
          </div>
          {tested && <div className="notice">{tested}</div>}
        </div>
      </section>
    </div>
  );
}
