// Panel.tsx - the side panel for the selected tile: title, chart type, what it computes (metrics + split, SQL, or
// text), its period, colour and number format. "Save" sends one update_tile op; the backend checks it.
import { useEffect, useState } from "react";
import { KINDS, Op, Semantic, Tile, TIMES } from "./api";

type Props = { tile: Tile; semantic: Semantic; globalTime: string; onOps: (ops: Op[]) => void; onClose: () => void };

// The side panel for editing the selected tile.
export default function Panel({ tile, semantic, globalTime, onOps, onClose }: Props) {
  const [t, setT] = useState(tile);
  useEffect(() => { setT(tile); }, [tile]);
  const mode = t.kind === "text" ? "text" : t.sql != null ? "sql" : "metric";
  const q = t.query ?? { metrics: [], dimensions: [], time: globalTime, filters: [] };
  const table = semantic.metrics[q.metrics[0]]?.shape;
  const available = Object.entries(semantic.metrics).filter(([, m]) => semantic.columns[m.shape] && (!table || m.shape === table));
  const dims = table ? [...Object.keys(semantic.columns[table] || {}), "month", "year"] : [];
  // Change the tile's query; this clears any SQL or text.
  const setQ = (patch: Partial<typeof q>) => setT({ ...t, query: { ...q, ...patch }, sql: null, text: null });
  // Add the value to the list, or remove it if already there.
  const toggle = (list: string[], v: string) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);

  // Send the changes as one update_tile op.
  function save() {
    let value: Record<string, any> = { title: t.title, kind: t.kind, style: t.style };
    if (mode === "text") value = { ...value, text: t.text || " ", query: null, sql: null, fields: {} };
    else if (mode === "sql") value = { ...value, sql: t.sql, query: null, text: null, fields: {} };
    else {
      const python = q.metrics.some((m) => semantic.metrics[m]?.python);
      const fields = python || !q.metrics.length ? {} : q.dimensions.length ? { x: q.dimensions[0], y: q.metrics[0] } : { value: q.metrics[0] };
      value = { ...value, query: q, sql: null, text: null, fields };
    }
    onOps([{ op: "update_tile", tile_id: tile.id, value }]);
  }

  return (
    <aside className="panel">
      <div className="panel-head"><b>Tile</b><button onClick={onClose}>×</button></div>
      <label>Title<input value={t.title} onChange={(e) => setT({ ...t, title: e.target.value })} /></label>
      <label>Type<select value={t.kind} onChange={(e) => {
        const kind = e.target.value;
        setT(kind === "text" ? { ...t, kind, query: null, sql: null, text: t.text || "Notes" } : { ...t, kind, text: null,
          query: t.kind === "text" && t.sql == null ? q : t.query });
      }}>{KINDS.map((k) => <option key={k}>{k}</option>)}</select></label>

      {mode !== "text" && (
        <div className="row">
          <label><input type="radio" checked={mode === "metric"} onChange={() => setQ({})} /> Metric</label>
          <label><input type="radio" checked={mode === "sql"} onChange={() => setT({ ...t, query: null, sql: t.sql || "SELECT 1 AS n" })} /> SQL</label>
        </div>
      )}
      {mode === "text" && <label>Text<textarea rows={5} value={t.text ?? ""} onChange={(e) => setT({ ...t, text: e.target.value })} /></label>}
      {mode === "sql" && <label>SQL (read-only SELECT)<textarea rows={6} value={t.sql ?? ""} onChange={(e) => setT({ ...t, sql: e.target.value })} /></label>}
      {mode === "metric" && (
        <>
          <fieldset><legend>Metrics{table && ` (table ${table})`}</legend>
            {available.map(([k, m]) => (
              <label key={k} className="check"><input type="checkbox" checked={q.metrics.includes(k)}
                onChange={() => setQ({ metrics: toggle(q.metrics, k), dimensions: q.metrics.length ? q.dimensions : [] })} />{m.name}</label>
            ))}
            {!available.length && <div className="muted">No data in this workspace yet.</div>}
          </fieldset>
          {dims.length > 0 && (
            <fieldset><legend>Split by</legend>
              {dims.map((d) => <label key={d} className="check"><input type="checkbox" checked={q.dimensions.includes(d)}
                onChange={() => setQ({ dimensions: toggle(q.dimensions, d) })} />{d}</label>)}
            </fieldset>
          )}
          <label>Period<select value={q.time === globalTime ? "" : q.time} onChange={(e) => setQ({ time: e.target.value || globalTime })}>
            <option value="">Follow the dashboard ({globalTime})</option>
            {TIMES.filter((x) => x !== globalTime).map((x) => <option key={x}>{x}</option>)}
          </select></label>
        </>
      )}
      <label>Colour<input type="color" value={t.style.color || "#B9B19A"} onChange={(e) => setT({ ...t, style: { ...t.style, color: e.target.value } })} /></label>
      <label>Numbers<select value={t.style.number_format || ""} onChange={(e) => {
        const { number_format, ...rest } = t.style;
        setT({ ...t, style: e.target.value ? { ...rest, number_format: e.target.value } : rest });
      }}>
        <option value="">Automatic</option><option value="inr">₹ 1,23,456</option><option value="inr_lakh">₹ lakh</option>
        <option value="inr_crore">₹ crore</option><option value="pct">%</option><option value="plain">Plain</option>
      </select></label>
      <button className="primary" onClick={save}>Save tile</button>
    </aside>
  );
}
