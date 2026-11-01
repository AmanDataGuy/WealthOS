// api.ts - every call to the FastAPI backend, plus the shapes it returns (mirrors dashboards.py / engine.py).
// The session (user id + token) lives in localStorage; every request sends the token as a Bearer header.

export type Session = { userId: string; token: string; username?: string };
export type Layout = { x: number; y: number; w: number; h: number };
type Query = { metrics: string[]; dimensions: string[]; time: string; filters: object[] };
export type Tile = {
  id: string; title: string; kind: string; query: Query | null; sql: string | null; text: string | null;
  layout: Layout; style: Record<string, any>; fields: Record<string, string>;
};
export type Spec = { id: string; title: string; version: number; workspace_id: string; tiles: Record<string, Tile>;
  global_time: string; template_id: string | null; parent_version: number | null; note: string };
export type Op = { op: string; tile_id?: string; value?: Record<string, any> };
export type TileResult = { rows?: Record<string, any>[]; error?: string; warnings?: string[]; provenance?: any };
export type Snapshot = { version: number; time: string; refreshed_at: string; tiles: Record<string, TileResult>;
  changes?: Record<string, { status: string; values?: Record<string, { before: number; after: number }> }> };
export type MetricDef = { name: string; shape: string; unit: string; python?: string };
export type Semantic = { columns: Record<string, Record<string, { type: string; role: string }>>;
  metrics: Record<string, MetricDef> };

// Chart colours for the dark theme (ECharts can't read CSS variables): the glow's blue -> violet -> orange -> pink,
// then lighter steps; axis text and grid lines are tuned to read on the dark glass cards.
export const CHART = {
  colors: ["#6f9bff", "#ff9a5a", "#ff6b9a", "#a98bff", "#ffc48a", "#3d63e6"],
  text: "#c9d1e4", muted: "#9aa5bf", axis: "rgba(255,255,255,.18)", grid: "rgba(255,255,255,.06)",
  axisStyle: () => ({ axisLine: { lineStyle: { color: CHART.axis } }, axisLabel: { color: CHART.muted }, splitLine: { lineStyle: { color: CHART.grid } } }),
  tooltip: { backgroundColor: "#0d1428", borderColor: "rgba(255,255,255,.14)", textStyle: { color: "#eef1f8" } },
};

export const KINDS = ["kpi", "line", "bar", "stacked_bar", "area", "pie", "donut", "table", "waterfall", "text"];
export const TIMES = ["today", "mtd", "last_month", "qtd", "ytd", "fy", "last_fy", "trailing_12m", "all"];

let session: Session | null = JSON.parse(localStorage.getItem("wos_session") || "null");
// Read the saved login.
export const getSession = () => session;
// Save the login (or clear it on logout).
export function setSession(s: Session | null) {
  session = s;
  s ? localStorage.setItem("wos_session", JSON.stringify(s)) : localStorage.removeItem("wos_session");
}

// fetch with the token; non-2xx answers throw an Error carrying the backend's plain "detail" message.
async function request(path: string, method: string, body?: unknown): Promise<Response> {
  const form = body instanceof FormData;
  const headers: Record<string, string> = session ? { Authorization: `Bearer ${session.token}` } : {};
  if (!form) headers["Content-Type"] = "application/json";  // for a form the browser sets type and boundary itself
  const offline = "Can't reach the WealthOS server (it may be restarting). Try again in a moment.";
  let res: Response;
  try {
    res = await fetch("/api" + path, { method, headers, body: body === undefined || form ? (body as FormData | undefined) : JSON.stringify(body) });
  } catch { throw new Error(offline); }
  if (!res.ok) {
    let detail: any = res.status >= 500 ? offline : res.statusText;  // a 5xx without our JSON = the server isn't there
    try { detail = (await res.json()).detail ?? detail; } catch { /* not JSON */ }
    if (res.status === 401 && session && !path.startsWith("/auth")) { setSession(null); location.reload(); }  // signed out
    if (Array.isArray(detail)) detail = detail.map((d) => `${(d.loc ?? []).slice(1).join(".")}: ${d.msg}`).join("; ");  // form errors
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return res;
}

// GET, POST, DELETE and PUT: send the request and return the JSON answer.
export const get = async <T = any>(path: string): Promise<T> => (await request(path, "GET")).json();
export const post = async <T = any>(path: string, body: unknown = {}): Promise<T> => (await request(path, "POST", body)).json();
export const del = async <T = any>(path: string, body?: unknown): Promise<T> => (await request(path, "DELETE", body)).json();
export const put = async <T = any>(path: string, body: unknown): Promise<T> => (await request(path, "PUT", body)).json();
export const patch = async <T = any>(path: string, body: unknown): Promise<T> => (await request(path, "PATCH", body)).json();

// Send a file as a form (multipart).
export const upload = async <T = any>(path: string, form: FormData): Promise<T> => (await request(path, "POST", form)).json();

// Download a file the backend generates (it needs the token, so a plain <a href> can't do it).
export async function download(path: string, filename: string) {
  const blob = await (await request(path, "GET")).blob();
  const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: filename });
  a.click();
  URL.revokeObjectURL(a.href);
}

// A link from data (news, filings) only if it is http(s): a "javascript:" URL in a row must never become clickable.
export function safeHref(u: unknown): string | undefined {
  try {
    const x = new URL(String(u));
    return x.protocol === "https:" || x.protocol === "http:" ? x.href : undefined;
  } catch {
    return undefined;
  }
}

// Numbers as the tile's style asks: ₹ with Indian grouping (default for INR), lakh / crore, %, or plain.
export function formatNumber(v: unknown, format?: string, unit?: string): string {
  if (typeof v !== "number") return v == null ? "–" : String(v);
  const f = format || (unit === "INR" ? "inr" : unit === "pct" ? "pct" : "plain");
  const inr = (n: number, d = 0) => n.toLocaleString("en-IN", { maximumFractionDigits: d });
  if (f === "inr") return "₹" + inr(v);
  if (f === "inr_lakh") return "₹" + inr(v / 1e5, 2) + " L";
  if (f === "inr_crore") return "₹" + inr(v / 1e7, 2) + " Cr";
  if (f === "pct") return inr(v, 1) + "%";
  return inr(v, 2);
}
