// Upload.tsx - "Query your own data", under the chat box: upload files (statements, tradebooks, holdings, prices,
// CAS, filings, notes) or read a Google Sheet. A table that isn't recognised is mapped once (the same layout maps
// itself next time). The list shows everything the assistant can use.
import { useEffect, useState } from "react";
import { del, get, post, upload } from "./api";

const WS = "default";

export default function Upload({ u, onError }: { u: string; onError: (m: string) => void }) {
  const [open, setOpen] = useState(false);
  const [datasets, setDatasets] = useState<any[]>([]);
  const [docs, setDocs] = useState<any[]>([]);
  const [results, setResults] = useState<any[]>([]);
  const [busy, setBusy] = useState("");
  const [password, setPassword] = useState("");
  const [sheet, setSheet] = useState("");
  // What the assistant can use: tables and documents.
  const reload = async () => {
    setDatasets(await get(`/sources/${u}/datasets?workspace_id=${WS}`));
    setDocs(await get(`/research/${u}/${WS}`));
  };
  useEffect(() => { if (open) reload().catch((e) => onError(e.message)); }, [open, u]);

  // Run an action with a status line; errors go to the caller.
  async function act(label: string, fn: () => Promise<void>) {
    setBusy(label);
    try { await fn(); } catch (e) { onError((e as Error).message); }
    setBusy("");
  }
  // Upload files one by one and show what was read from each.
  const sendFiles = (files: File[]) => act(`Reading ${files.length} file${files.length > 1 ? "s" : ""}…`, async () => {
    const read = [];
    for (const file of files) {
      const form = new FormData();
      form.append("user_id", decodeURIComponent(u)); form.append("workspace_id", WS); form.append("file", file);
      if (password) form.append("password", password);
      read.push(await upload("/sources/upload", form));
    }
    setResults(read); await reload();
  });
  // Read a Google Sheet shared by link.
  const sendSheet = () => act("Reading the sheet…", async () => {
    setResults([await post("/sources/sheet", { user_id: decodeURIComponent(u), url: sheet, workspace_id: WS })]);
    setSheet(""); await reload();
  });

  return (
    <div className="upload">
      <button className="upload-toggle" onClick={() => setOpen(!open)}>{open ? "× Close" : "+ Query your own data"}</button>
      {open && (
        <div className="card-box upload-panel">
          <p className="muted">Bank / card statements, tradebooks, holdings, daily prices (CSV, Excel), a CAS or annual report (PDF),
            notes (Markdown, text). Everything stays in your account until you delete it.</p>
          <div className="row wrap">
            <input type="file" multiple accept=".csv,.xlsx,.pdf,.md,.txt"
              onChange={(e) => e.target.files?.length && sendFiles([...e.target.files])} />
            <input type="password" placeholder="PDF password (CAS only)" value={password} onChange={(e) => setPassword(e.target.value)} />
          </div>
          <div className="row">
            <input className="grow" placeholder="Or a Google Sheet link (shared: anyone with the link can view)" value={sheet}
              onChange={(e) => setSheet(e.target.value)} />
            <button disabled={!sheet.trim() || !!busy} onClick={sendSheet}>Read sheet</button>
          </div>
          {busy && <div className="muted">{busy}</div>}
          {results.map((r) => (
            <div key={r.content_hash ?? r.file}>
              <b>{r.file}</b>{r.research && <span className="muted"> · document, {r.research.pages} pages</span>}
              {r.tables.map((t: any) => (
                <div key={t.index}>
                  <span className="muted">{t.shape === "unknown" ? "Not recognised" : t.shape} · {t.rows} rows</span>
                  {t.warnings.map((w: string) => <div key={w} className="muted">{w}</div>)}
                  {t.shape === "unknown" && <Mapper u={u} hash={r.content_hash} index={t.index} onError={onError} onDone={reload} />}
                </div>
              ))}
            </div>
          ))}
          <h3>Your data</h3>
          {!datasets.length && !docs.length && <p className="muted">Nothing yet.</p>}
          {datasets.map((d) => (
            <div key={d.dataset_id} className="row">
              <b>{d.shape}</b><span className="muted grow">{d.source.file} · {d.rows} rows</span>
              <button className="icon" title="Remove this table" onClick={() => confirm(`Remove ${d.source.file} (${d.shape})?`) &&
                act("Removing…", async () => { await del(`/sources/${u}/datasets/${encodeURIComponent(d.dataset_id)}`); await reload(); })}>×</button>
            </div>
          ))}
          {docs.map((d) => (
            <div key={d.doc_id} className="row">
              <b>{d.file}</b><span className="muted grow">{d.doc_type === "other" ? "document" : d.doc_type} · {d.pages} pages</span>
              <button className="icon" title="Remove this document" onClick={() => act("Removing…", async () => {
                await del(`/research/${u}/${WS}/${d.doc_id}`); await reload(); })}>×</button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// Map an unrecognised table: what kind of table it is and which file column is each standard column. The suggestion
// (a saved mapping, name rules, then one AI call) is pre-filled; saving remembers the mapping.
function Mapper({ u, hash, index, onError, onDone }: {
  u: string; hash: string; index: number; onError: (m: string) => void; onDone: () => void;
}) {
  const [info, setInfo] = useState<any>(null);
  const [shape, setShape] = useState("");
  const [map, setMap] = useState<Record<string, string>>({});
  const [saved, setSaved] = useState("");
  const path = `/sources/${u}/${hash}/tables/${index}/mapping`;

  // Load the suggestion.
  async function open() {
    try {
      const r = await get(path);
      setInfo(r); setShape(r.suggestion.shape); setMap(r.suggestion.column_map);
    } catch (e) { onError((e as Error).message); }
  }
  // Save the table with the chosen mapping.
  async function confirm() {
    try {
      const r = await post(path, { shape, column_map: Object.fromEntries(Object.entries(map).filter(([, v]) => v)), workspace_id: WS });
      setSaved(`${r.rows} rows saved as ${r.shape}`); setInfo(null); onDone();
    } catch (e) { onError((e as Error).message); }
  }

  if (saved) return <div className="muted">{saved}. Files with the same columns will map themselves.</div>;
  if (!info) return <div className="row"><span className="muted">Its columns need mapping once.</span><button onClick={open}>Map columns</button></div>;
  const spec = info.shapes[shape];
  const examples = (col: string) => (info.profile?.[col]?.examples ?? []).slice(0, 2).join(", ");
  return (
    <div className="mapper">
      <label>This table is<select value={shape} onChange={(e) => { setShape(e.target.value); setMap({}); }}>
        {Object.keys(info.shapes).map((s) => <option key={s}>{s}</option>)}
      </select></label>
      <table>
        <thead><tr><th>Standard column</th><th>Your column</th></tr></thead>
        <tbody>{spec.columns.map((std: string) => (
          <tr key={std}>
            <td>{std.replace(/_/g, " ")}{spec.required.includes(std) && " *"}</td>
            <td><select value={map[std] ?? ""} onChange={(e) => setMap({ ...map, [std]: e.target.value })}>
              <option value="">Not in this file</option>
              {info.columns.map((c: string) => <option key={c} value={c}>{c}{examples(c) && ` (e.g. ${examples(c)})`}</option>)}
            </select></td>
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
