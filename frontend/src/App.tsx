// App.tsx - a first visit opens a guest account with sample data (or sign in), then five pages under one top bar: Chat (home: ask anything, upload your data), Dashboards,
// Strategy lab, Rebalancing and Company (an Excel model of a US-listed company from its SEC filings). The chat's tools
// reach the rest (tax, prices, backtests, rebalancing), so every page talks to the same assistant.
import { useEffect, useRef, useState } from "react";
import { del, download, get, getSession, post, Session, setSession } from "./api";
import Chat from "./Chat";
import Company from "./Company";
import Dashboards from "./Dashboards";
import Rebalance from "./Rebalance";
import Strategy from "./Strategy";

type View = "chat" | "dashboards" | "strategy" | "rebalance" | "company";
const VIEWS: [View, string][] = [["chat", "Chat"], ["dashboards", "Dashboards"], ["strategy", "Strategy lab"], ["rebalance", "Rebalancing"],
  ["company", "Company"]];

// The whole page. A first visit gets a guest account with the sample data, so there is nothing to fill in;
// signing out (or a failed guest start) shows the sign-in screen.
export default function App() {
  const [session, setS] = useState<Session | null>(getSession());
  const [signIn, setSignIn] = useState(false);
  const asked = useRef(false);  // React's dev mode runs effects twice; one guest account is enough
  const start = (s: Session) => { setSession(s); setS(s); };
  useEffect(() => {
    if (session || signIn || asked.current) return;
    asked.current = true;
    post("/auth/guest").then((r) => start({ userId: r.user_id, token: r.token, username: r.username }))
      .catch(() => setSignIn(true));
  }, [session, signIn]);
  if (session) return <Shell session={session} onLogout={() => { setSession(null); setS(null); setSignIn(true); }} />;
  if (!signIn) return <div className="login"><h2>WealthOS</h2><p>Opening a demo account with sample data…</p></div>;
  return <Login onDone={start} onGuest={() => { asked.current = false; setSignIn(false); }} />;
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
      {session.username?.startsWith("guest-") && view === "chat" && (
        <div className="banner guest">
          Demo account with a sample bank statement, trades and holdings. Try <i>"How much did I spend on Swiggy this
          FY?"</i> or <i>"Old or new tax regime for me?"</i>. <button onClick={onLogout}>Sign in to use your own files</button>
        </div>
      )}
      {error && <div className="banner" onClick={() => setError("")}>{error} <small>(click to dismiss)</small></div>}
      {view === "chat" && <Chat u={u} boards={boards} onPinned={(id) => { setOpenBoard(id); setView("dashboards"); }} />}
      {view === "dashboards" && <Dashboards u={u} boards={boards} reloadBoards={reloadBoards} openId={openBoard} onError={setError} />}
      {view === "strategy" && <Strategy u={u} boards={boards} onError={setError} />}
      {view === "rebalance" && <Rebalance u={u} boards={boards} onError={setError} />}
      {view === "company" && <Company u={u} onError={setError} />}
    </div>
  );
}

// Sign in with a username and password.
function Login({ onDone, onGuest }: { onDone: (s: Session) => void; onGuest: () => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
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
      <button onClick={onGuest}>Try it with sample data instead</button>
      {error && <div className="error">{error}</div>}
    </div>
  );
}
