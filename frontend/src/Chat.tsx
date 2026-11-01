// Chat.tsx - ask questions about the workspace's data (agent.py behind /assistant/ask).
// Each answer shows its numbers (rows), how they were computed (SQL, period, source files) and a badge:
// Trusted = a named metric, Exploratory = a checked free query, External = SEC filings / news.
// A clarify question shows its options as buttons (the choice is remembered as a definition), and a data answer
// can be pinned to a dashboard: the tile stores the query, never the numbers.
// Chats are saved per workspace (left list): open one to read it or carry on; "New chat" starts fresh.
import { useEffect, useRef, useState } from "react";
import { del, formatNumber, get, post, safeHref } from "./api";

type Answer = { text: string; badge: string; rows: Record<string, any>[]; provenance: any; warnings: string[];
  plan: any; verified: boolean; clarify: { term: string; options: string[] } | null };
type Turn = { question: string; answer?: Answer; error?: string };
type ChatItem = { id: string; title: string; updated_at: string; turns: number };
type Props = { u: string; ws: string; boards: { id: string; title: string }[]; onPinned: (id: string) => void };

const BADGES: Record<string, string> = { trusted: "Trusted", exploratory: "Exploratory", external: "External source", none: "" };

// The Ask tab: question box, saved chats and the answers.
export default function Chat({ u, ws, boards, onPinned }: Props) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [chatId, setChatId] = useState<string | null>(null);
  const [chats, setChats] = useState<ChatItem[]>([]);
  const [meanings, setMeanings] = useState<{ term: string; value: string }[]>([]);
  const end = useRef<HTMLDivElement>(null);
  // Load the saved chats for this workspace.
  const loadChats = () => get(`/assistant/${u}/chats?workspace_id=${ws}`).then(setChats).catch(() => setChats([]));
  // Load what the user told the assistant words mean (answers to "did you mean ...?").
  const loadMeanings = () => get(`/assistant/${u}/definitions`).then(setMeanings).catch(() => setMeanings([]));
  useEffect(() => { setTurns([]); setChatId(null); loadChats(); loadMeanings(); }, [ws]);
  useEffect(() => { end.current?.scrollIntoView({ behavior: "smooth" }); }, [turns]);  // braces: an effect may only return a cleanup

  // Send a question (optionally defining a term) and add the answer to the chat.
  async function ask(question: string, define?: { term: string; value: string }) {
    const previous = [...turns].reverse().find((t) => t.answer?.plan)?.answer?.plan ?? null;
    setTurns((ts) => [...ts, { question }]); setText(""); setBusy(true);
    let turn: Turn;
    try {
      const answer = await post("/assistant/ask", { user_id: decodeURIComponent(u), question, workspace_id: ws, previous_plan: previous, define, chat_id: chatId });
      turn = { question, answer };
      if (!chatId) setChatId(answer.chat_id);
      loadChats(); if (define) loadMeanings();
    } catch (e) { turn = { question, error: (e as Error).message }; }
    setTurns((ts) => [...ts.slice(0, -1), turn]); setBusy(false);
  }

  // Open a stored chat (its answers are shown as they were; ask again to refresh the numbers).
  async function openChat(id: string) {
    const chat = await get(`/assistant/${u}/chats/${id}`);
    setChatId(id); setTurns(chat.turns);
  }

  // Delete a saved chat.
  async function removeChat(id: string) {
    await del(`/assistant/${u}/chats/${id}`);
    if (id === chatId) { setChatId(null); setTurns([]); }
    loadChats();
  }

  return (
    <div className="chat-page">
    <aside className="chat-side">
      <button className="primary" disabled={busy} onClick={() => { setChatId(null); setTurns([]); }}>New chat</button>
      {chats.map((c) => (
        <div key={c.id} className={`chat-item ${c.id === chatId ? "active" : ""}`}>
          <button className="grow" disabled={busy} title={c.title} onClick={() => openChat(c.id)}>
            <span>{c.title}</span><small>{new Date(c.updated_at).toLocaleDateString()} · {c.turns} question{c.turns === 1 ? "" : "s"}</small>
          </button>
          <button className="icon" title="Delete chat" disabled={busy} onClick={() => removeChat(c.id)}>×</button>
        </div>
      ))}
      {!chats.length && <div className="muted">No saved chats in this workspace yet.</div>}
      {meanings.length > 0 && <>
        <h3 className="side-head">Remembered meanings</h3>
        {meanings.map((m) => (
          <div key={m.term} className="chat-item">
            <span className="grow meaning"><b>{m.term}</b> = {m.value}</span>
            <button className="icon" title="Forget this meaning" onClick={() => del(`/assistant/${u}/definitions/${encodeURIComponent(m.term)}`).then(setMeanings)}>×</button>
          </div>
        ))}
      </>}
    </aside>
    <div className="chat">
      <div className="chat-log">
        {!turns.length && (
          <div className="empty">
            <h2>Ask your money <em>anything.</em></h2>
            <p className="muted">"How much did I spend this financial year?" · "Spending by month" · "Realized gains by stock" · "What was NVIDIA's revenue?"</p>
          </div>
        )}
        {turns.map((t, i) => (
          <div key={i} className="turn">
            <div className="question">{t.question}</div>
            {!t.answer && !t.error && <div className="muted">Working it out…</div>}
            {t.error && <div className="error">{t.error}</div>}
            {t.answer && <AnswerView a={t.answer} question={t.question} u={u} boards={boards} onPinned={onPinned}
              onChoose={(term, value) => ask(t.question, { term, value })} />}
          </div>
        ))}
        <div ref={end} />
      </div>
      <div className="chat-input">
        <input className="grow" placeholder="Ask a question about your data" value={text} disabled={busy}
          onChange={(e) => setText(e.target.value)} onKeyDown={(e) => e.key === "Enter" && text.trim() && ask(text.trim())} />
        <button className="primary" disabled={!text.trim() || busy} onClick={() => ask(text.trim())}>Ask</button>
      </div>
    </div>
    </div>
  );
}

// One answer: its numbers, how they were computed, and the pin-to-dashboard control.
function AnswerView({ a, question, u, boards, onPinned, onChoose }: {
  a: Answer; question: string; u: string; boards: { id: string; title: string }[];
  onPinned: (id: string) => void; onChoose: (term: string, value: string) => void;
}) {
  const [board, setBoard] = useState("");
  const [pinned, setPinned] = useState("");
  const cols = a.rows.length ? Object.keys(a.rows[0]) : [];
  const p = a.provenance;
  const canPin = a.plan && (a.plan.kind === "metric" || a.plan.kind === "sql") && a.rows.length > 0;

  // Pin this answer to a dashboard as a tile.
  async function pin() {
    try {
      await post(`/dashboards/${u}/${board}/pin`, { plan: a.plan, title: question.slice(0, 120) });
      setPinned(boards.find((b) => b.id === board)?.title || "the dashboard");
    } catch (e) { setPinned("failed: " + (e as Error).message); }
  }

  return (
    <div className="answer">
      <div className="answer-head">
        {BADGES[a.badge] && <span className="tag">{BADGES[a.badge]}</span>}
        {a.badge !== "none" && <span className="muted">{a.verified ? "every number checked against the results" : "explanation replaced by the table"}</span>}
      </div>
      <div className="answer-text">{a.text.replace(/\n\n_(.*)_$/s, "")}</div>
      {a.clarify && (
        <div className="row">{a.clarify.options.map((o) => <button key={o} onClick={() => onChoose(a.clarify!.term, o)}>{o}</button>)}</div>
      )}
      {p?.tool === "research" && (
        <ol className="citations">{a.rows.map((c, i) => (
          <li key={i}><b>{c.file}, p. {c.page}</b>{c.section && <span className="muted"> · {c.section}</span>}<q>{c.excerpt}</q></li>
        ))}</ol>
      )}
      {cols.length > 0 && p?.tool !== "research" && (
        <div className="table-wrap answer-table"><table>
          <thead><tr>{cols.map((c) => <th key={c} className={typeof a.rows[0][c] === "number" ? "num" : ""}>{c.replace(/_/g, " ")}</th>)}</tr></thead>
          <tbody>{a.rows.slice(0, 50).map((r, i) => <tr key={i}>{cols.map((c) => typeof r[c] === "number"
            ? <td key={c} className="num">{formatNumber(r[c], "plain")}</td>
            : c === "url" && safeHref(r[c]) ? <td key={c}><a href={safeHref(r[c])} target="_blank" rel="noreferrer">link</a></td>
            : <td key={c}>{String(r[c] ?? "")}</td>)}</tr>)}</tbody>
        </table></div>
      )}
      {a.rows.length > 50 && <div className="muted">Showing 50 of {a.rows.length} rows.</div>}
      {a.warnings.map((w) => <div key={w} className="muted">Note: {w}</div>)}
      {p?.sql && (
        <details><summary className="muted">How this was computed</summary>
          {p.period && <div className="muted">Period {p.period.start} to {p.period.end}</div>}
          <pre>{p.sql}</pre>
          {p.datasets?.length > 0 && <div className="muted">From: {p.datasets.map((d: any) => d.file).join(", ")}</div>}
        </details>
      )}
      {canPin && boards.length > 0 && (
        <div className="row">
          <select value={board} onChange={(e) => setBoard(e.target.value)}>
            <option value="">Add to dashboard…</option>
            {boards.map((b) => <option key={b.id} value={b.id}>{b.title}</option>)}
          </select>
          <button disabled={!board} onClick={pin}>Add</button>
          {pinned && (pinned.startsWith("failed") ? <span className="muted">{pinned}</span>
            : <span className="muted">Added to {pinned}. <a href="#" onClick={(e) => { e.preventDefault(); onPinned(board); }}>Open it</a></span>)}
        </div>
      )}
      <div className="disclaimer">Analytics on your own data, not investment advice (WealthOS is not SEBI-registered).</div>
    </div>
  );
}
