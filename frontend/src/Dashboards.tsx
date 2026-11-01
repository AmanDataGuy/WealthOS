// Dashboards.tsx - build dashboards on your data, by hand or with AI, and take them out as Excel.
//
// By hand: drag / resize tiles on a 12-column grid, click a tile to edit it in the side panel, add / duplicate /
// delete (shift-click selects several), change the period, undo / redo. With AI: type an instruction (shown as a
// preview first). Out: Excel (with a provenance sheet) or a printable report. Your own Excel templates (formulas
// and all) are filled with your data and recalculated.
// Every change is a list of ops sent to POST /dashboards/{user}/{id}/ops with the edit shown on screen, so the backend
// checks it and a stale screen can never overwrite newer work. Numbers only ever come from /refresh.
import { useEffect, useState } from "react";
import GridLayout, { Layout as GridItem, WidthProvider } from "react-grid-layout";
import "react-grid-layout/css/styles.css";
import "react-resizable/css/styles.css";
import { del, download, formatNumber, get, Op, post, Semantic, Snapshot, Spec, Tile, TIMES, upload } from "./api";
import Panel from "./Panel";
import Report from "./Report";
import TileView from "./TileView";

const Grid = WidthProvider(GridLayout);
const WS = "default";
const DATA_OPS = new Set(["add_tile", "update_tile", "duplicate_tile", "set_time"]);  // ops that change what tiles compute

type Props = { u: string; boards: { id: string; title: string }[]; reloadBoards: () => Promise<void>;
  openId: string | null; onError: (m: string) => void };

export default function Dashboards({ u, boards, reloadBoards, openId, onError }: Props) {
  const [semantic, setSemantic] = useState<Semantic>({ columns: {}, metrics: {} });
  const [spec, setSpec] = useState<Spec | null>(null);
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [redo, setRedo] = useState<number[]>([]);
  const [busy, setBusy] = useState(false);
  const [modal, setModal] = useState<"" | "templates" | "excel" | "report">("");

  // Run an action with a busy flag; any backend refusal goes to the banner.
  async function run(fn: () => Promise<void>) {
    setBusy(true);
    try { await fn(); } catch (e) { onError((e as Error).message); } finally { setBusy(false); }
  }
  useEffect(() => { run(async () => setSemantic(await get(`/semantic/${u}/${WS}`))); }, [u]);
  useEffect(() => { if (openId) open(openId); }, [openId]);

  const base = spec ? `/dashboards/${u}/${spec.id}` : "";
  // Re-run the dashboard's tiles for the chosen period.
  const refresh = async (s: Spec, time?: string) => setSnap(await post(`/dashboards/${u}/${s.id}/refresh`, { time }));

  // Open a dashboard: its latest spec and last refresh (refreshed now if there is none for this edit).
  const open = (id: string) => run(async () => {
    const s: Spec = await get(`/dashboards/${u}/${id}`);
    setSpec(s); setSelected([]); setRedo([]);
    const last: Snapshot | null = await get(`/dashboards/${u}/${id}/refresh`);
    if (last && last.version === s.version) setSnap(last); else await refresh(s);
  });

  // Save ops as a new edit; refresh when they change what a tile computes.
  const apply = (ops: Op[]) => run(async () => {
    if (!spec || !ops.length) return;
    const s: Spec = await post(`${base}/ops`, { ops, base_version: spec.version });
    setSpec(s); setRedo([]); setSelected((sel) => sel.filter((id) => id in s.tiles));
    if (ops.some((o) => DATA_OPS.has(o.op))) await refresh(s);
  });

  // Undo (no edit number) or redo (the edit undo left), both saved as new edits by the backend.
  const revert = (version?: number) => run(async () => {
    if (!spec) return;
    const s: Spec = await post(`${base}/revert`, { version });
    setRedo(version === undefined ? [spec.version, ...redo] : redo.slice(1));
    setSpec(s); await refresh(s);
  });

  // Create a new empty dashboard and open it.
  const newBoard = () => run(async () => {
    const title = prompt("Dashboard name");
    if (!title) return;
    const s: Spec = await post(`/dashboards/${u}`, { title, workspace_id: WS });
    await reloadBoards(); await open(s.id);
  });
  // Delete the open dashboard.
  const deleteBoard = () => run(async () => {
    if (!spec || !confirm(`Delete "${spec.title}" and its whole edit history?`)) return;
    await del(base); setSpec(null); setSnap(null); await reloadBoards();
  });

  // A new tile under the others: a number from the first metric with data, or a text box.
  function addTile(kind: string) {
    if (!spec) return;
    const y = Math.max(0, ...Object.values(spec.tiles).map((t) => t.layout.y + t.layout.h));
    const metric = Object.entries(semantic.metrics).find(([, m]) => semantic.columns[m.shape] && !m.python)?.[0];
    const layout = { x: 0, y, w: kind === "kpi" ? 3 : 6, h: kind === "kpi" ? 3 : 5 };
    const value = kind === "text" || !metric
      ? { kind: "text", title: "Note", text: "Write something here", layout }
      : { kind, title: semantic.metrics[metric].name, layout, query: { metrics: [metric], dimensions: kind === "kpi" ? [] : ["month"], time: spec.global_time },
          fields: kind === "kpi" ? { value: metric } : { x: "month", y: metric } };
    apply([{ op: "add_tile", value }]);
  }

  const tiles: Tile[] = spec ? Object.values(spec.tiles) : [];
  const layout: GridItem[] = tiles.map((t) => ({ i: t.id, ...t.layout }));
  // After a tile is dragged or resized, send its new place to the backend.
  const moved = (_: GridItem[], before: GridItem, after: GridItem) => {
    if (before.x !== after.x || before.y !== after.y) apply([{ op: "move_tile", tile_id: after.i, value: { x: after.x, y: after.y } }]);
    else if (before.w !== after.w || before.h !== after.h) apply([{ op: "resize_tile", tile_id: after.i, value: { w: after.w, h: after.h } }]);
  };
  const editing = selected.length === 1 && spec ? spec.tiles[selected[0]] : null;

  if (modal === "report" && spec) return <Report u={u} spec={spec} metrics={semantic.metrics} onError={onError} onClose={() => setModal("")} />;
  return (
    <>
      <div className="toolbar">
        <select value={spec?.id ?? ""} onChange={(e) => e.target.value && open(e.target.value)}>
          <option value="">Choose a dashboard…</option>
          {boards.map((b) => <option key={b.id} value={b.id}>{b.title}</option>)}
        </select>
        <button onClick={newBoard}>New</button>
        <button onClick={() => setModal("templates")}>From template</button>
        <button onClick={() => setModal("excel")}>Your Excel templates</button>
        {busy && <span className="muted">Working…</span>}
      </div>
      {spec && (
        <div className="toolbar">
          <input className="title" defaultValue={spec.title} key={spec.id + spec.version}
            onBlur={(e) => e.target.value.trim() && e.target.value !== spec.title && apply([{ op: "rename_dashboard", value: { title: e.target.value } }])} />
          <select value={spec.global_time} onChange={(e) => apply([{ op: "set_time", value: { time: e.target.value } }])}>
            {[...new Set([spec.global_time, ...TIMES])].map((t) => <option key={t}>{t}</option>)}
          </select>
          <button onClick={() => run(() => refresh(spec))}>Refresh</button>
          <button disabled={spec.parent_version == null} onClick={() => revert()}>Undo</button>
          <button disabled={!redo.length} onClick={() => revert(redo[0])}>Redo</button>
          <span className="sep" />
          <button onClick={() => addTile("kpi")}>+ Number</button>
          <button onClick={() => addTile("bar")}>+ Chart</button>
          <button onClick={() => addTile("text")}>+ Text</button>
          <button disabled={!selected.length} onClick={() => apply(selected.map((id) => ({ op: "duplicate_tile", tile_id: id })))}>Duplicate</button>
          <button disabled={!selected.length} onClick={() => apply(selected.map((id) => ({ op: "delete_tile", tile_id: id })))}>Delete</button>
          <span className="sep" />
          <button className="primary" onClick={() => run(() => download(`${base}/export.xlsx`, `${spec.title}.xlsx`))}>Download Excel</button>
          <button onClick={() => { setSelected([]); setModal("report"); }}>Report / PDF</button>
          <button className="danger" onClick={deleteBoard}>Delete dashboard</button>
          <span className="muted">edit {spec.version}{snap && ` · refreshed ${new Date(snap.refreshed_at).toLocaleTimeString()}`}</span>
        </div>
      )}
      {spec && <AiBox base={base} onApply={apply} onError={onError} />}

      <main>
        {spec ? (
          <div className="canvas" onClick={() => setSelected([])}>
            <Grid layout={layout} cols={12} rowHeight={40} margin={[12, 12]} compactType={null} preventCollision
              onDragStop={moved} onResizeStop={moved}>
              {tiles.map((t) => (
                <div key={t.id} className={`tile ${selected.includes(t.id) ? "selected" : ""}`}
                  onClick={(e) => { e.stopPropagation(); setSelected(e.shiftKey ? (selected.includes(t.id) ? selected.filter((x) => x !== t.id) : [...selected, t.id]) : [t.id]); }}>
                  <TileView tile={t} result={snap?.tiles[t.id]} metrics={semantic.metrics} changed={snap?.changes?.[t.id]?.status === "changed"} />
                </div>
              ))}
            </Grid>
            {!tiles.length && <div className="empty">Empty dashboard: add a number, a chart or text above, or ask the AI.</div>}
          </div>
        ) : (
          <div className="empty">Choose a dashboard, create a new one, or start from a template.</div>
        )}
        {editing && spec && <Panel tile={editing} semantic={semantic} globalTime={spec.global_time} onOps={apply} onClose={() => setSelected([])} />}
      </main>
      {modal === "templates" && <Templates u={u} onClose={() => setModal("")}
        onCreated={async (id) => { setModal(""); await reloadBoards(); await open(id); }} />}
      {modal === "excel" && <ExcelTemplates u={u} onError={onError} onClose={() => setModal("")} />}
    </>
  );
}

// "Edit with AI": the instruction becomes a preview (summary + what changes); nothing is saved until Apply.
function AiBox({ base, onApply, onError }: { base: string; onApply: (ops: Op[]) => void; onError: (m: string) => void }) {
  const [text, setText] = useState("");
  const [preview, setPreview] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  // Ask the AI to edit the dashboard from a sentence.
  async function ask() {
    setBusy(true);
    try { setPreview(await post(`${base}/ai-edit`, { instruction: text })); } catch (e) { onError((e as Error).message); }
    setBusy(false);
  }
  const d = preview?.diff;
  return (
    <div className="ai">
      <input placeholder='Edit with AI, e.g. "make revenue a bar chart and add last year"' value={text}
        onChange={(e) => setText(e.target.value)} onKeyDown={(e) => e.key === "Enter" && text && ask()} />
      <button disabled={!text || busy} onClick={ask}>{busy ? "Thinking…" : "Preview"}</button>
      {preview && (
        <div className="preview">
          <b>{preview.summary || "Proposed change"}</b>
          <span className="muted"> · {d.added.length} added, {d.removed.length} removed, {Object.keys(d.changed).length} changed
            {d.dashboard.length ? `, dashboard ${d.dashboard.join(", ")}` : ""}</span>
          <button className="primary" onClick={() => { onApply(preview.ops); setPreview(null); setText(""); }}>Apply</button>
          <button onClick={() => setPreview(null)}>Discard</button>
        </div>
      )}
    </div>
  );
}

// Start from a template: pick one, check the suggested table and columns (change any), create.
function Templates({ u, onClose, onCreated }: { u: string; onClose: () => void; onCreated: (id: string) => void }) {
  const [catalog, setCatalog] = useState<any[]>([]);
  const [pick, setPick] = useState<any>(null);
  const [suggestion, setSuggestion] = useState<any>(null);
  const [columns, setColumns] = useState<Record<string, Record<string, unknown>>>({});
  const [error, setError] = useState("");
  useEffect(() => {
    get("/templates").then(setCatalog).catch((e) => setError(e.message));
    get(`/semantic/${u}/${WS}`).then((s) => setColumns(s.columns)).catch((e) => setError(e.message));
  }, [u]);

  // See which table and columns the template would use.
  async function choose(t: any) {
    setPick(t); setError("");
    try { setSuggestion(await get(`/templates/${u}/${WS}/${t.id}/suggest`)); } catch (e) { setError((e as Error).message); }
  }
  // Create a dashboard from the chosen template.
  async function create() {
    try {
      const s = await post(`/templates/${u}/${WS}/${pick.id}/apply`, { table: suggestion.table, mapping: suggestion.mapping });
      onCreated(s.id);
    } catch (e) { setError((e as Error).message); }
  }
  const cols = suggestion?.table ? Object.keys(columns[suggestion.table] || {}) : [];
  return (
    <div className="modal-back" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="panel-head"><b>Start from a template</b><button onClick={onClose}>×</button></div>
        {error && <div className="error">{error}</div>}
        {!pick ? (
          <div className="cards">
            {catalog.map((t) => (
              <button key={t.id} className="card" onClick={() => choose(t)}>
                <b>{t.title}</b><small>{t.audience}</small><span>{t.description}</span>
              </button>
            ))}
          </div>
        ) : suggestion && (
          <div>
            <h3>{pick.title}</h3>
            {pick.slots.length > 0 && (
              <>
                <label>Table<select value={suggestion.table ?? ""} onChange={(e) => setSuggestion({ ...suggestion, table: e.target.value, mapping: {} })}>
                  <option value="">Choose…</option>
                  {Object.keys(columns).map((t) => <option key={t}>{t}</option>)}
                </select></label>
                {pick.slots.map((s: any) => (
                  <label key={s.name}>{s.description}{s.optional ? " (optional)" : ""}
                    <select value={suggestion.mapping[s.name] ?? ""} onChange={(e) => setSuggestion({ ...suggestion, mapping: { ...suggestion.mapping, [s.name]: e.target.value } })}>
                      <option value="">{s.optional ? "Skip" : "Choose…"}</option>
                      {cols.map((c) => <option key={c}>{c}</option>)}
                    </select></label>
                ))}
              </>
            )}
            {suggestion.missing.length > 0 && !pick.slots.length && <div className="error">Needs {suggestion.missing.join(", ")}</div>}
            <div className="row">
              <button onClick={() => { setPick(null); setSuggestion(null); }}>Back</button>
              <button className="primary" onClick={create}>Create dashboard</button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// Your own Excel template (excel.py): a 'Data' sheet WealthOS fills with one of your tables, and named output cells.
// LibreOffice recalculates it; the named results show here and the filled workbook can be downloaded.
function ExcelTemplates({ u, onError, onClose }: { u: string; onError: (m: string) => void; onClose: () => void }) {
  const [templates, setTemplates] = useState<any[]>([]);
  const [tables, setTables] = useState<string[]>([]);
  const [file, setFile] = useState<File | null>(null);
  const [table, setTable] = useState("");
  const [busy, setBusy] = useState("");
  // Load the templates and the tables they can be filled from.
  const load = async () => {
    setTemplates(await get(`/excel/${u}/${WS}`));
    const shapes = [...new Set<string>((await get(`/sources/${u}/datasets?workspace_id=${WS}`)).map((d: any) => d.shape))];
    setTables(shapes); setTable((t) => t || shapes[0] || "");
  };
  useEffect(() => { load().catch((e) => onError(e.message)); }, [u]);

  // Run a template action with a status message, then reload.
  async function act(label: string, fn: () => Promise<unknown>) {
    setBusy(label);
    try { await fn(); await load(); } catch (e) { onError((e as Error).message); }
    setBusy("");
  }
  // Upload a new Excel template.
  const add = () => act("Checking the template…", async () => {
    const form = new FormData();
    form.append("file", file!); form.append("data_table", table);
    await upload(`/excel/${u}/${WS}`, form);
    setFile(null);
  });

  return (
    <div className="modal-back" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="panel-head"><b>Your Excel templates</b><button onClick={onClose}>×</button></div>
        <p className="muted">Your own workbook with formulas: a sheet named <b>Data</b> that WealthOS fills with one of your tables,
          and the result cells named in Excel (Formulas → Define Name). It is recalculated with your data; macros,
          OFFSET / INDIRECT and Excel Tables are refused.</p>
        <div className="row wrap">
          <input type="file" accept=".xlsx" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          <label className="row">Fill with <select value={table} onChange={(e) => setTable(e.target.value)}>
            {tables.map((t) => <option key={t}>{t}</option>)}</select></label>
          <button className="primary" disabled={!file || !table || !!busy} onClick={add}>Add template</button>
        </div>
        {busy && <div className="muted">{busy}</div>}
        {templates.map((t) => (
          <div key={t.id} className="excel-item">
            <div className="row">
              <b>{t.name}</b><span className="muted grow">filled from {t.data_table} · outputs: {Object.keys(t.outputs).join(", ")}</span>
              <button disabled={!!busy} onClick={() => act("Recalculating…", () => post(`/excel/${u}/${WS}/${t.id}/run`))}>Fill</button>
              {t.last_run && <button onClick={() => download(`/excel/${u}/${WS}/${t.id}/filled.xlsx`, `${t.name}-filled.xlsx`).catch((e) => onError(e.message))}>Download</button>}
              <button onClick={() => confirm(`Delete the template "${t.name}"?`) && act("Deleting…", () => del(`/excel/${u}/${WS}/${t.id}`))}>Delete</button>
            </div>
            {t.last_run && (
              <div className="table-wrap"><table>
                <tbody>{Object.entries(t.last_run.outputs).map(([k, v]) => (
                  <tr key={k}><td>{k}</td><td className="num">{Array.isArray(v) ? v.join(", ") : typeof v === "number" ? formatNumber(v, "plain") : String(v ?? "")}</td></tr>
                ))}</tbody>
              </table>
              {t.last_run.errors.map((e: string) => <div key={e} className="error">{e}</div>)}</div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
