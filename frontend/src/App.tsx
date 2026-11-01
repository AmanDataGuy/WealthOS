// App.tsx - sign in, then four pages under one top bar: Chat (home: ask anything, upload your data), Dashboards,
// Strategy lab and Rebalancing. The chat's tools reach the rest (tax, prices, backtests, rebalancing), so every page
// talks to the same assistant.
import { useEffect, useState } from "react";
import { del, download, get, getSession, post, Session, setSession } from "./api";
import Chat from "./Chat";
import Dashboards from "./Dashboards";
import Rebalance from "./Rebalance";
import Strategy from "./Strategy";

type View = "chat" | "dashboards" | "strategy" | "rebalance";
const VIEWS: [View, string][] = [["chat", "Chat"], ["dashboards", "Dashboards"], ["strategy", "Strategy lab"], ["rebalance", "Rebalancing"]];

// The whole page: login screen or the app.
export default function App() {
  const [session, setS] = useState<Session | null>(getSession());
  if (!session) return <Login onDone={(s) => { setSession(s); setS(s); }} />;
  return <Shell session={session} onLogout={() => { setSession(null); setS(null); }} />;
}

// The signed-in app: top bar, account menu and the current page.
function Shell({ session, onLogout }: { session: Session; onLogout: () => void }) {
  const u = encodeURIComponent(session.userId);
  const [view, setView] = useState<View>("chat");
  const [boards, setBoards] = useState<{ id: string; title: string }[]>([]);
  const [openBoard, setOpenBoard] = useState<string | null>(null);
  const [error, setError] = useState("");
  const reloadBoards = async () => setBoards(await get(`/dashboards/${u}?workspace_id=default`));
  useEffect(() => { reloadBoards().catch((e) => setError(e.message)); }, [u]);

  // Take everything you stored as a zip.
  const exportAccount = () => download(`/account/${u}/export`, "wealthos-export.zip").catch((e) => setError(e.message));
  // Delete the account and all its data, after typing the username.
  const deleteAccount = async () => {
    const typed = prompt("This deletes your account and everything you uploaded, for good. Type your username to confirm.");
    if (!typed) return;
    try { await del(`/account/${u}`, { confirm: typed }); onLogout(); } catch (e) { setError((e as Error).message); }
  };

  return (
    <div className="app">
      <header>
        <b>WealthOS</b>
        <div className="tabs">
          {VIEWS.map(([v, label]) => <button key={v} className={view === v ? "active" : ""} onClick={() => setView(v)}>{label}</button>)}
        </div>
        <span className="spacer" />
        <details className="account">
          <summary>{session.username || "Account"}</summary>
          <div className="menu">
            <button onClick={exportAccount}>Export my data</button>
            <button className="danger" onClick={deleteAccount}>Delete account</button>
            <button onClick={onLogout}>Sign out</button>
          </div>
        </details>
      </header>
      {error && <div className="banner" onClick={() => setError("")}>{error} <small>(click to dismiss)</small></div>}
      {view === "chat" && <Chat u={u} boards={boards} onPinned={(id) => { setOpenBoard(id); setView("dashboards"); }} />}
      {view === "dashboards" && <Dashboards u={u} boards={boards} reloadBoards={reloadBoards} openId={openBoard} onError={setError} />}
      {view === "strategy" && <Strategy u={u} boards={boards} onError={setError} />}
      {view === "rebalance" && <Rebalance u={u} boards={boards} onError={setError} />}
    </div>
  );
}

// Sign in with a username and password, or (local development only) paste a token made with:
// cd backend && python -c "from app.main import _issue_token; print(_issue_token('me', 'me'))"
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
      {import.meta.env.DEV && (  // dev server only: production builds drop this panel
        <details>
          <summary>Local development: use a token</summary>
          <input placeholder="User id" value={devUser} onChange={(e) => setDevUser(e.target.value)} />
          <input placeholder="Token" value={devToken} onChange={(e) => setDevToken(e.target.value)} />
          <button disabled={!devUser || !devToken} onClick={() => onDone({ userId: devUser, token: devToken })}>Continue</button>
        </details>
      )}
    </div>
  );
}
