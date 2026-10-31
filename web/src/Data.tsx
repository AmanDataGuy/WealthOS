// Data.tsx - bring data into a workspace: upload a file, read a Google Sheet, or copy tables from a database,
// and see what tables the workspace has. Every number later shown is computed from these tables.
import { useEffect, useState } from "react";
import { del, download, formatNumber, get, post, upload } from "./api";

type Props = { u: string; ws: string; onError: (m: string) => void; onChanged: () => void };

// Map an unrecognised table: pick what kind of table it is and which file column is each standard column.
// The suggestion (a saved mapping, name rules, then one AI call) is pre-filled; confirming saves the table and
// remembers the mapping, so the next file with the same columns maps itself.
function Mapper({ u, ws, hash, index, onError, onDone }: {
  u: string; ws: string; hash: string; index: number; onError: (m: string) => void; onDone: (r?: any) => void;
}) {
  const [info, setInfo] = useState<any>(null);
  const [shape, setShape] = useState("");
  const [map, setMap] = useState<Record<string, string>>({});
  const [saved, setSaved] = useState("");
  const path = `/sources/${u}/${hash}/tables/${index}/mapping`;

  // Read the file's tables and preview them before importing.
  async function open() {
    try {
      const r = await get(path);
      setInfo(r); setShape(r.suggestion.shape); setMap(r.suggestion.column_map);
    } catch (e) { onError((e as Error).message); }
  }
  // Import the previewed tables.
  async function confirm() {
    try {
      const r = await post(path, { shape, column_map: Object.fromEntries(Object.entries(map).filter(([, v]) => v)), workspace_id: ws });
      setSaved(`${r.rows} rows saved as ${r.shape}`); setInfo(null); onDone();
    } catch (e) { onError((e as Error).message); }
  }

  if (saved) return <div className="muted">{saved}. Files with the same columns will map themselves.</div>;
  if (!info) return <div className="row"><span className="muted">Its columns need mapping before it can be used.</span><button onClick={open}>Map columns</button></div>;
  const spec = info.shapes[shape];
  // Show up to two example values of a column.
  const examples = (col: string) => (info.profile?.[col]?.examples ?? []).slice(0, 2).join(", ");
  return (
    <div className="mapper">
      <label>This table is<select value={shape} onChange={(e) => { setShape(e.target.value); setMap({}); }}>
        {Object.keys(info.shapes).map((s) => <option key={s}>{s}</option>)}
      </select></label>
      <table>
        <thead><tr><th>Standard column</th><th>Your column</th><th>Confidence</th></tr></thead>
        <tbody>{spec.columns.map((std: string) => (
          <tr key={std}>
            <td>{std.replace(/_/g, " ")}{spec.required.includes(std) && " *"}</td>
            <td><select value={map[std] ?? ""} onChange={(e) => setMap({ ...map, [std]: e.target.value })}>
              <option value="">Not in this file</option>
              {info.columns.map((c: string) => <option key={c} value={c}>{c}{examples(c) && ` (e.g. ${examples(c)})`}</option>)}
            </select></td>
            <td className="muted">{map[std] ? (shape === info.suggestion.shape && info.suggestion.column_map[std] === map[std]
              ? info.suggestion.confidence[std] ?? "you chose" : "you chose") : ""}</td>
          </tr>
        ))}</tbody>
      </table>
      <div className="row">
        <span className="muted">* must have{shape === "transactions" && ", plus debit/credit or amount"}</span>
        <span className="spacer" />
        <button onClick={() => setInfo(null)}>Cancel</button>
        <button className="primary" onClick={confirm}>Save table</button>
      </div>
    </div>
  );
}

// Live feeds: a push feed gets a token (shown once) your system uses to POST rows; a pull feed is fetched by the worker.
function Feeds({ u, ws, onError, onChanged }: Props) {
  const [feeds, setFeeds] = useState<any[]>([]);
  const [name, setName] = useState("");
  const [kind, setKind] = useState("push");
  const [url, setUrl] = useState("");
  const [minutes, setMinutes] = useState(5);
  const [created, setCreated] = useState<any>(null);
  // Load the feeds list.
  const load = () => get(`/feeds/${u}/${ws}`).then(setFeeds).catch((e) => onError(e.message));
  useEffect(() => { setCreated(null); load(); }, [u, ws]);

  // Create a new feed.
  async function create() {
    try {
      setCreated(await post(`/feeds/${u}/${ws}`, { name, kind, url: kind === "pull" ? url : null, interval_s: minutes * 60 }));
      setName(""); setUrl(""); await load();
    } catch (e) { onError((e as Error).message); }
  }
  // Run a feed action, then reload the list.
  async function act(fn: () => Promise<unknown>) {
    try { await fn(); await load(); onChanged(); } catch (e) { onError((e as Error).message); }
  }
  // The address a feed's data is pushed to.
  const pushUrl = (id: string) => `${location.origin}/api/feeds/${u}/${ws}/${id}/push`;

  return (
    <div className="card-box">
      <h3>Live feeds</h3>
      <p className="muted">Push: your system sends rows to a URL with a token. Pull: WealthOS fetches an https URL (JSON or CSV) on a schedule.</p>
      <div className="row">
        <input placeholder="Table name, e.g. orders or prices" value={name} onChange={(e) => setName(e.target.value)} />
        <select value={kind} onChange={(e) => setKind(e.target.value)}><option value="push">Push</option><option value="pull">Pull</option></select>
        {kind === "pull" && <>
          <input className="grow" placeholder="https://…" value={url} onChange={(e) => setUrl(e.target.value)} />
          <select value={minutes} onChange={(e) => setMinutes(Number(e.target.value))}>
            {[1, 5, 15, 60, 1440].map((m) => <option key={m} value={m}>every {m < 60 ? `${m} min` : m === 60 ? "hour" : "day"}</option>)}
          </select>
        </>}
        <button className="primary" disabled={!name.trim() || (kind === "pull" && !url.trim())} onClick={create}>Create</button>
      </div>
      {created?.token && (
        <div className="notice-box">
          <b>Copy this token now; it is not shown again.</b>
          <pre>{`curl -X POST ${pushUrl(created.id)} \\
  -H "X-Feed-Token: ${created.token}" -H "Content-Type: application/json" \\
  -d '[{"date": "2026-10-30", "amount": 1200, "city": "Pune"}]'`}</pre>
        </div>
      )}
      {feeds.map((f) => (
        <div key={f.id} className="row">
          <b>{f.name}</b>
          <span className="muted">{f.kind} · {f.rows} rows{f.last_push_at && ` · last push ${new Date(f.last_push_at).toLocaleString()}`}
            {f.last_pull_at && ` · last pull ${new Date(f.last_pull_at).toLocaleString()}`}</span>
          {f.last_error && <span className="error">{f.last_error}</span>}
          <span className="spacer" />
          {f.kind === "pull" && <button onClick={() => act(() => post(`/feeds/${u}/${ws}/${f.id}/pull`))}>Fetch now</button>}
          <button onClick={() => confirm(`Delete the feed "${f.name}" and its rows?`) && act(() => del(`/feeds/${u}/${ws}/${f.id}`))}>Delete</button>
        </div>
      ))}
    </div>
  );
}

// Your own Excel template (excel.py): a 'Data' sheet WealthOS fills with one table, and named output cells.
// LibreOffice recalculates it; the named results show here and the filled workbook can be downloaded.
function ExcelTemplates({ u, ws, tables, onError }: Props & { tables: string[] }) {
  const [templates, setTemplates] = useState<any[]>([]);
  const [file, setFile] = useState<File | null>(null);
  const [table, setTable] = useState("");
  const [busy, setBusy] = useState("");
  // Load the Excel templates.
  const load = () => get(`/excel/${u}/${ws}`).then(setTemplates).catch((e) => onError(e.message));
  useEffect(() => { load(); }, [u, ws]);
  useEffect(() => { if (!tables.includes(table)) setTable(tables[0] ?? ""); }, [tables]);

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
    await upload(`/excel/${u}/${ws}`, form);
    setFile(null);
  });

  return (
    <div className="card-box">
      <h3>Excel templates</h3>
      <p className="muted">Your own workbook: a sheet named <b>Data</b> that WealthOS fills with a table, and the result cells named
        in Excel (Formulas → Define Name). LibreOffice recalculates it; macros, OFFSET / INDIRECT and Excel Tables are refused.</p>
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
            <button disabled={!!busy} onClick={() => act("Recalculating in LibreOffice…", () => post(`/excel/${u}/${ws}/${t.id}/run`))}>Run</button>
            {t.last_run && <button onClick={() => download(`/excel/${u}/${ws}/${t.id}/filled.xlsx`, `${t.name}-filled.xlsx`).catch((e) => onError(e.message))}>Download</button>}
            <button onClick={() => confirm(`Delete the template "${t.name}"?`) && act("Deleting…", () => del(`/excel/${u}/${ws}/${t.id}`))}>Delete</button>
          </div>
          {t.last_run && (
            <div className="table-wrap"><table>
              <tbody>{Object.entries(t.last_run.outputs).map(([k, v]) => (
                <tr key={k}><td>{k}</td><td className="num">{Array.isArray(v) ? v.join(", ") : typeof v === "number" ? formatNumber(v, "plain") : String(v ?? "")}</td></tr>
              ))}</tbody>
            </table>
            <div className="muted">{t.last_run.rows_written} rows written · calculated by LibreOffice on {new Date(t.last_run.at).toLocaleString()}</div>
            {t.last_run.errors.map((e: string) => <div key={e} className="error">{e}</div>)}</div>
          )}
        </div>
      ))}
    </div>
  );
}

// Free daily prices (prices.py): NSE stocks / ETFs by trading symbol, mutual funds by scheme (searched by name).
// Fetched once into the prices table, then refreshed every evening by the worker.
function Prices({ u, ws, onError, onChanged }: Props) {
  const [sources, setSources] = useState<any[]>([]);
  const [kind, setKind] = useState("nse");
  const [symbols, setSymbols] = useState("");
  const [query, setQuery] = useState("");
  const [found, setFound] = useState<{ code: string; name: string }[]>([]);
  const [picked, setPicked] = useState<{ code: string; name: string }[]>([]);
  const [start, setStart] = useState(() => new Date(Date.now() - 365 * 864e5).toISOString().slice(0, 10));
  const [busy, setBusy] = useState("");
  // Load the price sources.
  const load = () => get(`/prices/${u}/${ws}`).then(setSources).catch((e) => onError(e.message));
  useEffect(() => { load(); }, [u, ws]);

  // Run a price action with a status message, then reload.
  async function act(label: string, fn: () => Promise<unknown>) {
    setBusy(label);
    try { await fn(); await load(); onChanged(); } catch (e) { onError((e as Error).message); }
    setBusy("");
  }
  const codes = kind === "nse" ? symbols.split(/[\s,]+/).filter(Boolean) : picked.map((p) => p.code);
  // Fetch prices for the chosen symbols.
  const fetchPrices = () => act("Fetching prices… (first time: about 1 s per trading day)", async () => {
    await post(`/prices/${u}/${ws}`, { source: kind, symbols: codes, start });
    setSymbols(""); setPicked([]); setFound([]);
  });
  // Search for a symbol.
  const search = () => act("Searching…", async () => setFound(await get(`/prices/${u}/${ws}/search?q=${encodeURIComponent(query)}`)));

  return (
    <div className="card-box">
      <h3>Daily prices</h3>
      <p className="muted">Free, official sources: NSE closing prices for stocks and ETFs, AMFI NAVs for mutual funds (via mfapi.in).
        Updated every evening after 7 pm.</p>
      <div className="row wrap">
        <select value={kind} onChange={(e) => setKind(e.target.value)}><option value="nse">NSE stocks / ETFs</option><option value="mf">Mutual funds</option></select>
        {kind === "nse"
          ? <input className="grow" placeholder="Trading symbols, e.g. INFY, TCS, NIFTYBEES" value={symbols} onChange={(e) => setSymbols(e.target.value)} />
          : <><input className="grow" placeholder="Scheme name, e.g. parag parikh flexi cap direct" value={query}
                onChange={(e) => setQuery(e.target.value)} onKeyDown={(e) => e.key === "Enter" && query.trim() && search()} />
              <button disabled={!query.trim()} onClick={search}>Search</button></>}
        <label className="row">From <input type="date" value={start} onChange={(e) => setStart(e.target.value)} /></label>
        <button className="primary" disabled={!codes.length || !!busy} onClick={fetchPrices}>Fetch</button>
      </div>
      {kind === "mf" && (found.length > 0 || picked.length > 0) && (
        <div className="pick-list">
          {picked.map((p) => <button key={p.code} className="chip on" onClick={() => setPicked(picked.filter((x) => x.code !== p.code))}>{p.name} ×</button>)}
          {found.filter((f) => !picked.some((p) => p.code === f.code)).slice(0, 8).map((f) =>
            <button key={f.code} className="chip" onClick={() => setPicked([...picked, f])}>+ {f.name}</button>)}
        </div>
      )}
      {busy && <div className="muted">{busy}</div>}
      {sources.map((s) => (
        <div key={s.id} className="row">
          <b>{s.source === "nse" ? "NSE" : "Mutual funds"}</b>
          <span className="muted grow">{s.symbols.join(", ")} · {s.rows} prices up to {s.last_date}
            {s.warnings?.filter((w: string) => !w.startsWith("NSE closes")).map((w: string) => ` · ${w}`)}</span>
          <button onClick={() => confirm("Stop updating these prices and remove them?") && act("Removing…", () => del(`/prices/${u}/${ws}/${s.id}`))}>Remove</button>
        </div>
      ))}
      {sources.length > 0 && <div className="row"><button disabled={!!busy} onClick={() => act("Updating…", () => post(`/prices/${u}/${ws}/refresh`))}>Update now</button></div>}
    </div>
  );
}

// The Data tab: file, Google Sheet and database imports, and the table list.
export default function Data({ u, ws, onError, onChanged }: Props) {
  const [datasets, setDatasets] = useState<any[]>([]);
  const [docs, setDocs] = useState<any[]>([]);
  const [conn, setConn] = useState<any>(null);
  const [tables, setTables] = useState<string[]>([]);
  const [chosen, setChosen] = useState<string[]>([]);
  const [last, setLast] = useState<any>(null);
  const [busy, setBusy] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [password, setPassword] = useState("");
  const [sheet, setSheet] = useState("");
  const [dbUrl, setDbUrl] = useState("");
  const [sample, setSample] = useState<{ table: string; rows: any[] } | null>(null);

  // The first 20 rows of a table (the same guarded, read-only query path the assistant uses).
  const peek = (table: string) => act("Loading rows…", async () => {
    const r = await post("/query/sql", { user_id: decodeURIComponent(u), workspace_id: ws, sql: `SELECT * FROM "${table}" LIMIT 20` });
    setSample({ table, rows: r.rows });
  });

  // Run an action with a status message and report any error.
  async function act(label: string, fn: () => Promise<void>) {
    setBusy(label);
    try { await fn(); } catch (e) { onError((e as Error).message); }
    setBusy("");
  }
  // Reload the tables.
  const reload = async () => {
    setDatasets(await get(`/sources/${u}/datasets?workspace_id=${ws}`));
    setDocs(await get(`/research/${u}/${ws}`));
    const c = await get(`/connections/${u}/${ws}`);
    setConn(c); setChosen(c?.tables ?? []);
  };
  useEffect(() => { setLast(null); setTables([]); act("Loading…", reload); }, [u, ws]);
  // After an import: show the result, reload and tell the page.
  const done = async (result?: any) => { if (result) setLast(result); await reload(); onChanged(); };

  // Upload the chosen file.
  const sendFile = () => act("Reading the file…", async () => {
    const form = new FormData();
    form.append("user_id", decodeURIComponent(u)); form.append("workspace_id", ws); form.append("file", file!);
    if (password) form.append("password", password);
    await done(await upload("/sources/upload", form));
    setFile(null); setPassword("");
  });
  // Read the Google Sheet.
  const sendSheet = () => act("Reading the sheet…", async () => {
    await done(await post("/sources/sheet", { user_id: decodeURIComponent(u), url: sheet, workspace_id: ws }));
    setSheet("");
  });
  // Connect to the database.
  const connect = () => act("Connecting…", async () => {
    const r = await post(`/connections/${u}/${ws}`, { url: dbUrl });
    setTables(r.available_tables); setDbUrl(""); await done();
  });
  // Connect to an uploaded SQLite file.
  const connectSqlite = (f: File) => act("Connecting…", async () => {
    const form = new FormData(); form.append("file", f);
    const r = await upload(`/connections/${u}/${ws}/sqlite`, form);
    setTables(r.available_tables); await done();
  });
  // List the database's tables.
  const listTables = () => act("Listing tables…", async () => setTables(await get(`/connections/${u}/${ws}/tables`)));
  // Copy (or refresh) the database's tables into the workspace.
  const copy = (refresh: boolean) => act(refresh ? "Refreshing…" : "Copying tables…", async () => {
    const r = await post(`/connections/${u}/${ws}/snapshot`, { tables: refresh ? [] : chosen });
    const failed = Object.entries(r.errors);
    if (failed.length) onError("Some tables could not be copied: " + failed.map(([t, e]) => `${t}: ${e}`).join("; "));
    setTables([]); await done();
  });
  // Disconnect the database.
  const disconnect = () => act("Disconnecting…", async () => {
    if (!confirm("Forget this database and remove the tables copied from it?")) return;
    await del(`/connections/${u}/${ws}`); setTables([]); await done();
  });

  return (
    <div className="lab">
      <section className="lab-main">
        <h2>Data</h2>
        <p className="muted">Everything you add goes into the selected workspace. Numbers are only ever computed from these tables.</p>

        <div className="card-box">
          <h3>Upload a file</h3>
          <p className="muted">Bank or card statement, broker tradebook, holdings, daily prices (CSV / Excel), a CAS or an annual report (PDF).</p>
          <div className="row">
            <input type="file" accept=".csv,.xlsx,.pdf" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
            <input type="password" placeholder="PDF password (CAS only)" value={password} onChange={(e) => setPassword(e.target.value)} />
            <button className="primary" disabled={!file || !!busy} onClick={sendFile}>Upload</button>
          </div>
        </div>

        <div className="card-box">
          <h3>Google Sheet</h3>
          <p className="muted">Share the sheet as "Anyone with the link can view", then paste the link. The tab in the link is read.</p>
          <div className="row">
            <input className="grow" placeholder="https://docs.google.com/spreadsheets/d/…" value={sheet} onChange={(e) => setSheet(e.target.value)} />
            <button className="primary" disabled={!sheet.trim() || !!busy} onClick={sendSheet}>Read sheet</button>
          </div>
        </div>

        <div className="card-box">
          <h3>Database</h3>
          {!conn ? (
            <>
              <p className="muted">Postgres or MySQL connection string. Use a read-only database user; the password is stored encrypted.</p>
              <div className="row">
                <input className="grow" type="password" placeholder="postgresql://readonly:password@host:5432/dbname" value={dbUrl} onChange={(e) => setDbUrl(e.target.value)} />
                <button className="primary" disabled={!dbUrl.trim() || !!busy} onClick={connect}>Connect</button>
              </div>
              <div className="row"><span className="muted">Or a SQLite file:</span>
                <input type="file" accept=".sqlite,.db,.sqlite3" onChange={(e) => e.target.files?.[0] && connectSqlite(e.target.files[0])} /></div>
            </>
          ) : !tables.length ? (
            <>
              <p>Connected: <b>{conn.display}</b>{conn.refreshed_at && <span className="muted"> · last copied {new Date(conn.refreshed_at).toLocaleString()}</span>}</p>
              <div className="row">
                <button onClick={listTables}>Choose tables…</button>
                {conn.tables.length > 0 && <button className="primary" disabled={!!busy} onClick={() => copy(true)}>Refresh copied tables</button>}
                <button onClick={disconnect}>Disconnect</button>
              </div>
            </>
          ) : (
            <>
              <fieldset><legend>Tables to copy</legend>
                {tables.map((t) => <label key={t} className="check"><input type="checkbox" checked={chosen.includes(t)}
                  onChange={() => setChosen(chosen.includes(t) ? chosen.filter((x) => x !== t) : [...chosen, t])} />{t}</label>)}
              </fieldset>
              <div className="row">
                <button className="primary" disabled={!chosen.length || !!busy} onClick={() => copy(false)}>Copy {chosen.length} table(s)</button>
                <button onClick={() => setTables([])}>Cancel</button>
              </div>
            </>
          )}
        </div>

        <Prices u={u} ws={ws} onError={onError} onChanged={done} />
        <ExcelTemplates u={u} ws={ws} onError={onError} onChanged={done} tables={[...new Set(datasets.map((d) => d.shape))]} />
        <Feeds u={u} ws={ws} onError={onError} onChanged={done} />

        {busy && <p className="muted">{busy}</p>}
        {last && (
          <div className="card-box">
            <h3>Read from {last.file}</h3>
            {last.tables.map((t: any) => (
              <div key={t.index}>
                <b>{t.shape === "unknown" ? "Not recognised" : t.shape}</b> · {t.rows} rows
                {t.warnings.map((w: string) => <div key={w} className="muted">{w}</div>)}
                {t.shape === "unknown" && <Mapper u={u} ws={ws} hash={last.content_hash} index={t.index} onError={onError} onDone={done} />}
              </div>
            ))}
          </div>
        )}
      </section>

      <aside className="lab-runs">
        <h3>Tables in this workspace</h3>
        {!datasets.length && <p className="muted">None yet.</p>}
        {datasets.map((d) => (
          <button key={d.dataset_id} className="run-item" onClick={() => peek(d.shape)}>
            <b>{d.shape}</b>
            <span className="muted">{d.source.file} · {d.rows} rows</span>
            {d.warnings.map((w: string) => <span key={w} className="muted">{w}</span>)}
          </button>
        ))}
        <h3 className="side-head">Documents you can ask about</h3>
        {!docs.length && <p className="muted">Upload a filing or annual report (PDF) to ask about its text in Ask.</p>}
        {docs.map((d) => (
          <div key={d.doc_id} className="run-item doc-item">
            <b>{d.file}</b>
            <span className="muted">{d.doc_type === "other" ? "document" : d.doc_type} · {d.pages} pages</span>
            <button className="icon" title="Stop using for research" onClick={() => act("Removing…", async () => {
              await del(`/research/${u}/${ws}/${d.doc_id}`); await reload(); })}>×</button>
          </div>
        ))}
        {sample && (
          <div className="card-box">
            <div className="panel-head"><b>{sample.table}: first rows</b><button onClick={() => setSample(null)}>×</button></div>
            <div className="table-wrap"><table>
              <thead><tr>{Object.keys(sample.rows[0] ?? {}).map((c) => <th key={c}>{c}</th>)}</tr></thead>
              <tbody>{sample.rows.map((r: any, i: number) => <tr key={i}>{Object.values(r).map((v: any, j) => <td key={j}>{String(v ?? "")}</td>)}</tr>)}</tbody>
            </table></div>
          </div>
        )}
      </aside>
    </div>
  );
}
