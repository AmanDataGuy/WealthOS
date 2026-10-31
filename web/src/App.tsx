// App.tsx - the dashboard editor.
//
// Pick a workspace and a dashboard, then build it by hand: drag / resize tiles on a 12-column grid, click a tile to
// edit it in the side panel, add / duplicate / delete (shift-click selects several), change the period, refresh,
// undo / redo, export to Excel, start from a template, or type an instruction for the AI (shown as a preview first).
// Every change is a list of ops sent to POST /dashboards/{user}/{id}/ops with the edit shown on screen, so the backend
// checks it and a stale screen can never overwrite newer work. Numbers only ever come from /refresh.
import { useEffect, useState } from "react";
import GridLayout, { Layout as GridItem, WidthProvider } from "react-grid-layout";
import "react-grid-layout/css/styles.css";
import "react-resizable/css/styles.css";
import { del, download, get, getSession, Op, patch, post, Semantic, Session, setSession, Snapshot, Spec, Tile, TIMES } from "./api";
import Chat from "./Chat";
import Data from "./Data";
import Panel from "./Panel";
import Report from "./Report";
import SemanticBuilder from "./Semantic";
import Strategy from "./Strategy";
import Tax from "./Tax";
import TileView from "./TileView";

const Grid = WidthProvider(GridLayout);
const DATA_OPS = new Set(["add_tile", "update_tile", "duplicate_tile", "set_time"]);  // ops that change what tiles compute

// The whole page: login screen or the editor.
export default function App() {
  const [session, setS] = useState<Session | null>(getSession());
  if (!session) return <Login onDone={(s) => { setSession(s); setS(s); }} />;
  return <Editor session={session} onLogout={() => { setSession(null); setS(null); }} />;
}

// The signed-in page: workspace and dashboard pickers, tabs, and the dashboard grid editor.
function Editor({ session, onLogout }: { session: Session; onLogout: () => void }) {
  const u = encodeURIComponent(session.userId);
  const [workspaces, setWorkspaces] = useState<{ id: string; name: string }[]>([]);
  const [ws, setWs] = useState("default");
  const [semantic, setSemantic] = useState<Semantic>({ columns: {}, metrics: {} });
  const [boards, setBoards] = useState<{ id: string; title: string }[]>([]);
  const [spec, setSpec] = useState<Spec | null>(null);
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [redo, setRedo] = useState<number[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [showTemplates, setShowTemplates] = useState(false);
  const [view, setView] = useState<"data" | "semantic" | "ask" | "dashboards" | "strategy" | "tax" | "report">("ask");

  // Run an action with a busy flag; any backend refusal is shown in the banner.
  async function run(fn: () => Promise<void>) {
    setBusy(true); setError("");
    try { await fn(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }

  // Load the user's workspaces and pick the first one.
  const loadWorkspace = async () => {
    setSemantic(await get(`/semantic/${u}/${ws}`));
    setBoards(await get(`/dashboards/${u}?workspace_id=${ws}`));
  };
  useEffect(() => { run(async () => setWorkspaces(await get(`/workspaces/${u}`))); }, [u]);
  useEffect(() => { setSpec(null); setSnap(null); run(loadWorkspace); }, [u, ws]);
  // Create a workspace and switch to it.
  const newWorkspace = () => run(async () => {
    const name = prompt("Workspace name (e.g. Company MIS)");
    if (!name) return;
    const w = await post(`/workspaces/${u}`, { name });
    setWorkspaces(await get(`/workspaces/${u}`)); setWs(w.id); setView("data");
  });
  // Rename the current workspace (My files keeps its name).
  const renameWorkspace = () => run(async () => {
    const name = prompt("New name", workspaces.find((w) => w.id === ws)?.name);
    if (!name) return;
    await patch(`/workspaces/${u}/${ws}`, { name });
    setWorkspaces(await get(`/workspaces/${u}`));
  });
  // Delete the current workspace with its tables and dashboards, then go back to My files.
  const deleteWorkspace = () => run(async () => {
    const name = workspaces.find((w) => w.id === ws)?.name;
    if (!confirm(`Delete the workspace "${name}" with all its tables and dashboards?`)) return;
    await del(`/workspaces/${u}/${ws}`);
    setWs("default"); setWorkspaces(await get(`/workspaces/${u}`));
  });

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

  // Reload the dashboard list for the current workspace.
  const reloadBoards = async () => setBoards(await get(`/dashboards/${u}?workspace_id=${ws}`));
  // Create a new empty dashboard and open it.
  const newBoard = () => run(async () => {
    const title = prompt("Dashboard name");
    if (!title) return;
    const s: Spec = await post(`/dashboards/${u}`, { title, workspace_id: ws });
    await reloadBoards(); await open(s.id);
  });
  // Delete the open dashboard.
  const deleteBoard = () => run(async () => {
    if (!spec || !confirm(`Delete "${spec.title}" and its whole edit history?`)) return;
    await del(base); setSpec(null); setSnap(null); await reloadBoards();
  });

  // A new tile under the others: a number from the first metric this workspace has data for, or a text box.
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

  return (
    <div className="app">
      <header>
        <b>WealthOS</b>
        <select value={ws} onChange={(e) => setWs(e.target.value)}>
          {workspaces.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
        </select>
        <button onClick={newWorkspace}>New workspace</button>
        {ws !== "default" && <><button onClick={renameWorkspace}>Rename</button><button onClick={deleteWorkspace}>Delete</button></>}
        <span className="sep" />
        <div className="tabs">
          <button className={view === "data" ? "active" : ""} onClick={() => setView("data")}>Data</button>
          <button className={view === "semantic" ? "active" : ""} onClick={() => setView("semantic")}>Semantic</button>
          <button className={view === "ask" ? "active" : ""} onClick={() => setView("ask")}>Ask</button>
          <button className={view === "dashboards" ? "active" : ""} onClick={() => setView("dashboards")}>Dashboards</button>
          <button className={view === "strategy" ? "active" : ""} onClick={() => setView("strategy")}>Strategy lab</button>
          <button className={view === "tax" ? "active" : ""} onClick={() => setView("tax")}>Tax</button>
        </div>
        {view === "dashboards" && (
          <>
            <span className="sep" />
            <select value={spec?.id ?? ""} onChange={(e) => e.target.value && open(e.target.value)}>
              <option value="">Choose a dashboard…</option>
              {boards.map((b) => <option key={b.id} value={b.id}>{b.title}</option>)}
            </select>
            <button onClick={newBoard}>New</button>
            <button onClick={() => setShowTemplates(true)}>From template</button>
          </>
        )}
        <span className="spacer" />
        {busy && <span className="muted">Working…</span>}
        <span className="muted">{session.username || session.userId}</span>
        <button onClick={onLogout}>Sign out</button>
      </header>
      {error && <div className="banner" onClick={() => setError("")}>{error} <small>(click to dismiss)</small></div>}

      {view === "data" && <Data u={u} ws={ws} onError={setError} onChanged={() => run(async () => {
        await loadWorkspace(); setWorkspaces(await get(`/workspaces/${u}`));
      })} />}
      {view === "ask" && <Chat u={u} ws={ws} boards={boards} onPinned={(id) => { setView("dashboards"); open(id); }} />}
      {view === "semantic" && <SemanticBuilder u={u} ws={ws} onError={setError} />}
      {view === "strategy" && <Strategy u={u} ws={ws} onError={setError} />}
      {view === "tax" && <Tax u={u} ws={ws} onError={setError} />}
      {view === "report" && spec && <Report u={u} spec={spec} metrics={semantic.metrics} onError={setError} onClose={() => setView("dashboards")} />}
      {view === "dashboards" && spec && (
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
          <button onClick={() => run(() => download(`${base}/export.xlsx`, `${spec.title}.xlsx`))}>Export Excel</button>
          <button onClick={() => { setSelected([]); setView("report"); }}>Report / PDF</button>
          <button className="danger" onClick={deleteBoard}>Delete dashboard</button>
          <span className="muted">edit {spec.version}{snap && ` · refreshed ${new Date(snap.refreshed_at).toLocaleTimeString()}`}</span>
        </div>
      )}
      {view === "dashboards" && spec && <AiBox base={base} onApply={apply} onError={setError} />}

      {view === "dashboards" && <main>
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
      </main>}
      {showTemplates && <Templates u={u} ws={ws} onClose={() => setShowTemplates(false)}
        onCreated={async (id) => { setShowTemplates(false); await loadWorkspace(); await open(id); }} />}
    </div>
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
function Templates({ u, ws, onClose, onCreated }: { u: string; ws: string; onClose: () => void; onCreated: (id: string) => void }) {
  const [catalog, setCatalog] = useState<any[]>([]);
  const [pick, setPick] = useState<any>(null);
  const [suggestion, setSuggestion] = useState<any>(null);
  const [columns, setColumns] = useState<Record<string, Record<string, unknown>>>({});
  const [error, setError] = useState("");
  useEffect(() => {
    get("/templates").then(setCatalog).catch((e) => setError(e.message));
    get(`/semantic/${u}/${ws}`).then((s) => setColumns(s.columns)).catch((e) => setError(e.message));
  }, [u, ws]);

  // Pick one of the AI's suggested options.
  async function choose(t: any) {
    setPick(t); setError("");
    try { setSuggestion(await get(`/templates/${u}/${ws}/${t.id}/suggest`)); } catch (e) { setError((e as Error).message); }
  }
  // Create a dashboard from the chosen template.
  async function create() {
    try {
      const s = await post(`/templates/${u}/${ws}/${pick.id}/apply`, { table: suggestion.table, mapping: suggestion.mapping });
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

// Sign in with a username and password, or (for local development without the users database) paste a token
// made with: python -c "from api.main import _issue_token; print(_issue_token('me', 'me'))"
function Login({ onDone }: { onDone: (s: Session) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [devUser, setDevUser] = useState("");
  const [devToken, setDevToken] = useState("");
  const [error, setError] = useState("");
  // Log in or register, then remember the session.
  async function auth(path: string) {
    try {
      const r = await post(path, { username, password });
      onDone({ userId: r.user_id, token: r.token, username: r.username });
    } catch (e) { setError((e as Error).message); }
  }
  return (
    <div className="login">
      <h2>WealthOS</h2>
      <input placeholder="Username" value={username} onChange={(e) => setUsername(e.target.value)} />
      <input placeholder="Password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
      <div className="row">
        <button className="primary" onClick={() => auth("/auth/login")}>Sign in</button>
        <button onClick={() => auth("/auth/signup")}>Create account</button>
      </div>
      {error && <div className="error">{error}</div>}
      <details>
        <summary>Local development: use a token</summary>
        <input placeholder="User id" value={devUser} onChange={(e) => setDevUser(e.target.value)} />
        <input placeholder="Token" value={devToken} onChange={(e) => setDevToken(e.target.value)} />
        <button disabled={!devUser || !devToken} onClick={() => onDone({ userId: devUser, token: devToken })}>Continue</button>
      </details>
    </div>
  );
}
