import os
import requests
import pandas as pd
import streamlit as st
from streamlit_cookies_controller import CookieController

API_URL = os.getenv("WEALTHOS_API_URL", "http://localhost:8000")

st.set_page_config(
    page_title="WealthOS",
    page_icon="W",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
/* Standard native OS font stack — no external font dependency to fail to load.
   Excludes Streamlit's own icon elements (data-testid starting "stIcon") —
   those render icons as ligature text (e.g. "upload", "visibility") in a
   bundled icon font. Forcing a different font on them turns every native
   icon in the app into raw overlapping text (file uploader, password toggle).
   Leaving them out lets Streamlit's own icon font apply as designed. */
html, body, [class*="css"],
*:not([data-testid^="stIcon"]):not([data-testid^="stIcon"] *) {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif !important;
}

/* ── Base background ── */
[data-testid="stAppViewContainer"],
[data-testid="stMain"],
section.main,
.stApp { background: #ffffff !important; }
.main .block-container { padding: 2rem 2.5rem 3rem; max-width: 1080px; }

#MainMenu, footer,
[data-testid="stToolbar"],
[data-testid="stDecoration"] { display: none !important; }

/* ── Sidebar ── */
[data-testid="stSidebar"] {
    background: #f6f8fa !important;
    border-right: 1px solid #d8dee4 !important;
}
section[data-testid="stSidebar"] {
    transform: none !important;
    min-width: 210px !important;
    width: 210px !important;
}
[data-testid="stSidebarCollapseButton"],
[data-testid="collapsedControl"] { display: none !important; }

/* ── Typography ── */
/* Size hierarchy, deliberately spaced apart: h1 32px > h2 24px > h3 18px > body 16px > caption 13px */
h1 { color: #1f2328 !important; font-size: 2rem !important; font-weight: 700 !important; margin: 0 0 0.25rem !important; letter-spacing: -0.02em !important; }
h2 { color: #1f2328 !important; font-size: 1.5rem !important; font-weight: 600 !important; margin: 0 0 0.6rem !important; }
h3 { color: #24292f !important; font-size: 1.125rem !important; font-weight: 600 !important; margin: 0 0 0.4rem !important; }
p, li { color: #57606a !important; font-size: 1rem !important; }
label { color: #57606a !important; font-size: 1rem !important; }
/* Button label text is wrapped in a <p> — the rule above would otherwise
   grey it out even on a blue primary button. Force it back to white. */
[data-testid="stButton"] button[kind="primary"] p,
[data-testid="stFormSubmitButton"] button p { color: #ffffff !important; }

/* Streamlit's own header bar auto-follows browser dark-mode preference
   when nothing overrides it — force it to match the light page. */
[data-testid="stHeader"] { background: #ffffff !important; }

/* ── Inputs ── */
[data-testid="stTextInput"] input,
[data-testid="stTextArea"] textarea,
[data-testid="stNumberInput"] input {
    background: #f6f8fa !important;
    border: 1px solid #c9d1d9 !important;
    border-radius: 6px !important;
    color: #1f2328 !important;
    font-size: 0.9rem !important;
}
[data-testid="stTextInput"] input::placeholder,
[data-testid="stTextArea"] textarea::placeholder { color: #8c959f !important; }
[data-testid="stTextInput"] input:focus,
[data-testid="stTextArea"] textarea:focus {
    border-color: #3b82f6 !important;
    box-shadow: 0 0 0 3px rgba(59,130,246,0.2) !important;
}

/* ── Buttons ── */
[data-testid="stButton"] button,
[data-testid="stFormSubmitButton"] button {
    border-radius: 8px !important;
    font-weight: 600 !important;
    font-size: 0.875rem !important;
    transition: all 0.15s !important;
}
[data-testid="stButton"] button[kind="primary"],
[data-testid="stFormSubmitButton"] button {
    background: #3b82f6 !important;
    border: none !important;
    color: #ffffff !important;
}
[data-testid="stButton"] button[kind="primary"]:hover,
[data-testid="stFormSubmitButton"] button:hover { background: #2563eb !important; }
[data-testid="stButton"] button[kind="secondary"] {
    background: transparent !important;
    border: 1px solid #c9d1d9 !important;
    color: #57606a !important;
}
[data-testid="stButton"] button[kind="secondary"]:hover {
    border-color: #3b82f6 !important;
    color: #3b82f6 !important;
}

/* ── Tabs ── */
[data-baseweb="tab-list"] {
    border-bottom: 1px solid #d8dee4 !important;
    background: transparent !important;
    gap: 0 !important;
}
[data-baseweb="tab"] {
    color: #8c959f !important;
    font-size: 0.875rem !important;
    font-weight: 500 !important;
    padding: 0.6rem 1.25rem !important;
    border-bottom: 2px solid transparent !important;
    background: transparent !important;
}
[data-baseweb="tab"]:hover { color: #57606a !important; background: transparent !important; }
[aria-selected="true"][data-baseweb="tab"] {
    color: #3b82f6 !important;
    border-bottom: 2px solid #3b82f6 !important;
}

/* ── Metrics ── */
[data-testid="stMetric"] {
    background: #f6f8fa !important;
    border: 1px solid #d8dee4 !important;
    border-radius: 8px !important;
    padding: 1rem 1.25rem !important;
}
[data-testid="stMetricLabel"] { color: #8c959f !important; font-size: 0.72rem !important; text-transform: uppercase; letter-spacing: 0.05em; }
[data-testid="stMetricValue"] { color: #1f2328 !important; font-size: 1.3rem !important; font-weight: 700 !important; }

/* ── Dataframe ── */
[data-testid="stDataFrame"] {
    border: 1px solid #d8dee4 !important;
    border-radius: 8px !important;
    overflow: hidden !important;
}
[data-testid="stDataFrame"] * { color: #57606a !important; background: #f6f8fa !important; }

/* ── File uploader ── */
[data-testid="stFileUploader"] section {
    border: 1.5px dashed #c9d1d9 !important;
    border-radius: 8px !important;
    background: #f6f8fa !important;
}
[data-testid="stFileUploader"] * { color: #6e7781 !important; }

/* ── Expander ── */
[data-testid="stExpander"] {
    border: 1px solid #d8dee4 !important;
    border-radius: 8px !important;
    background: #f6f8fa !important;
}
[data-testid="stExpander"] summary { color: #57606a !important; }

/* ── Alerts ── */
[data-testid="stAlert"] { border-radius: 6px !important; font-size: 0.875rem !important; }

/* ── Divider ── */
hr { border-color: #d8dee4 !important; margin: 1.25rem 0 !important; }

/* ── Select / Dropdown ── */
[data-baseweb="select"] > div {
    background: #f6f8fa !important;
    border: 1px solid #c9d1d9 !important;
    border-radius: 6px !important;
    color: #1f2328 !important;
}
[data-baseweb="menu"] { background: #f6f8fa !important; border: 1px solid #c9d1d9 !important; }
[data-baseweb="option"] { background: #f6f8fa !important; color: #57606a !important; }
[data-baseweb="option"]:hover { background: #eaeef2 !important; }

/* ── Sidebar nav ── */
[data-testid="stSidebar"] [data-testid="stRadio"] > label:first-child { display: none !important; }
[data-testid="stSidebar"] [data-testid="stRadio"] div[role="radiogroup"] { gap: 0.15rem !important; }
[data-testid="stSidebar"] [data-testid="stRadio"] label {
    padding: 0.5rem 0.75rem !important;
    border-radius: 6px !important;
    font-size: 0.875rem !important;
    font-weight: 500 !important;
    color: #6e7781 !important;
    display: flex !important;
    align-items: center !important;
    cursor: pointer;
    width: 100% !important;
}
[data-testid="stSidebar"] [data-testid="stRadio"] label:hover {
    background: rgba(59,130,246,0.1) !important;
    color: #3b82f6 !important;
}
[data-testid="stSidebar"] [data-testid="stRadio"] input[type="radio"] { display: none !important; }

/* ── File uploader ── */
[data-testid="stFileUploaderDropzone"] { background: #f6f8fa !important; border-color: #c9d1d9 !important; }
[data-testid="stFileUploaderDropzone"] small { color: #8c959f !important; }

/* ── Checkbox / Radio ── */
[data-testid="stCheckbox"] label { color: #6e7781 !important; }
[data-testid="stRadio"] label { color: #57606a !important; }

/* ── Caption ── */
small, .stCaptionContainer, [data-testid="stCaptionContainer"] {
    color: #8c959f !important;
    font-size: 0.8125rem !important;
}

/* ── Scrollbar ── */
::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-track { background: #f6f8fa; }
::-webkit-scrollbar-thumb { background: #c9d1d9; border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: #3b82f6; }
</style>
""", unsafe_allow_html=True)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _auth_headers() -> dict:
    """Bearer token for {user_id}-scoped endpoints — no-op if the backend
    has no WEALTHOS_JWT_SECRET configured, since those endpoints fail open."""
    token = st.session_state.get("token", "")
    return {"Authorization": f"Bearer {token}"} if token else {}


def _api(method: str, path: str, **kwargs):
    try:
        headers = {**_auth_headers(), **kwargs.pop("headers", {})}
        r = getattr(requests, method)(
            f"{API_URL}{path}", timeout=kwargs.pop("timeout", 12), headers=headers, **kwargs
        )
        return r.json() if r.ok else None
    except Exception:
        return None


def verdict_pill(v: str) -> str:
    cfg = {
        "buy":   ("background:rgba(59,130,246,0.15);color:#3b82f6;border:1px solid rgba(59,130,246,0.4);", "Buy"),
        "hold":  ("background:#eaeef2;color:#6e7781;border:1px solid #d0d7de;", "Hold"),
        "avoid": ("background:rgba(239,68,68,0.15);color:#ef4444;border:1px solid rgba(239,68,68,0.4);", "Avoid"),
    }
    style, label = cfg.get((v or "").lower(),
                           ("background:#eaeef2;color:#6e7781;border:1px solid #d0d7de;", v or "—"))
    return (
        f'<span style="{style}font-size:0.75rem;font-weight:600;'
        f'padding:0.2rem 0.6rem;border-radius:999px;letter-spacing:0.03em;">'
        f'{label}</span>'
    )


def risk_bar_html(score) -> str:
    if not score:
        return '<span style="color:#8c959f;font-size:0.82rem;">—</span>'
    pct   = int(score) * 10
    color = "#3b82f6" if pct <= 40 else "#6e7781" if pct <= 60 else "#ef4444"
    return (
        f'<div style="display:flex;align-items:center;gap:0.6rem;">'
        f'<div style="flex:1;background:#d8dee4;border-radius:4px;height:5px;">'
        f'<div style="background:{color};width:{pct}%;height:5px;border-radius:4px;"></div></div>'
        f'<span style="font-size:0.82rem;color:#57606a;font-weight:600;white-space:nowrap;">{score}/10</span>'
        f'</div>'
    )


def alloc_bar_html(ticker: str, weight: float, max_w: float) -> str:
    pct = (weight / max_w * 100) if max_w else 0
    return (
        f'<div style="margin-bottom:0.55rem;">'
        f'<div style="display:flex;justify-content:space-between;font-size:0.82rem;margin-bottom:0.2rem;">'
        f'<span style="color:#1f2328;font-weight:500;">{ticker}</span>'
        f'<span style="color:#8c959f;">{weight:.1f}%</span></div>'
        f'<div style="background:#d8dee4;border-radius:4px;height:6px;">'
        f'<div style="background:#3b82f6;width:{min(pct,100):.0f}%;height:6px;border-radius:4px;"></div>'
        f'</div></div>'
    )


def fmt_date(ts: str) -> str:
    return ts[:10] if ts else "—"


# ── Auth ──────────────────────────────────────────────────────────────────────

_cookies = CookieController()

if not st.session_state.get("logged_in"):
    uid   = _cookies.get("wo_user_id")
    user  = _cookies.get("wo_username")
    token = _cookies.get("wo_token")
    if uid and user:
        st.session_state.logged_in = True
        st.session_state.username  = user
        st.session_state.user_id   = uid
        st.session_state.token     = token or ""
    else:
        st.session_state.setdefault("logged_in", False)
        st.session_state.setdefault("username",  "")
        st.session_state.setdefault("user_id",   "")
        st.session_state.setdefault("token",     "")

if not st.session_state.logged_in:
    _, col, _ = st.columns([1, 1.1, 1])
    with col:
        st.markdown(
            '<div style="text-align:center;padding:2.5rem 0 1.75rem;">'
            '<div style="font-size:2.5rem;font-weight:700;color:#1f2328;letter-spacing:-0.02em;">Wealth<span style="color:#3b82f6;">OS</span></div>'
            '<div style="font-size:0.85rem;color:#8c959f;margin-top:0.4rem;">Personal financial intelligence</div>'
            '</div>',
            unsafe_allow_html=True,
        )

        st.session_state.setdefault("auth_mode", "signin")

        if st.session_state.auth_mode == "signin":
            with st.form("login"):
                u  = st.text_input("Username")
                p  = st.text_input("Password", type="password")
                ok = st.form_submit_button("Sign in", use_container_width=True, type="primary")
            if ok:
                if not u or not p:
                    st.error("Fill in both fields.")
                else:
                    try:
                        r = requests.post(f"{API_URL}/auth/login",
                                          json={"username": u, "password": p}, timeout=10)
                        if r.status_code == 200:
                            d = r.json()
                            st.session_state.logged_in = True
                            st.session_state.username  = d["username"]
                            st.session_state.user_id   = d["user_id"]
                            st.session_state.token     = d.get("token", "")
                            _cookies.set("wo_user_id",  d["user_id"],  max_age=30 * 24 * 3600)
                            _cookies.set("wo_username", d["username"], max_age=30 * 24 * 3600)
                            _cookies.set("wo_token",    d.get("token", ""), max_age=30 * 24 * 3600)
                            st.rerun()
                        elif r.status_code == 503:
                            st.error("Backend offline — start the API server first.")
                        else:
                            st.error("Invalid username or password.")
                    except Exception:
                        st.error("Cannot reach backend at localhost:8000.")
            st.caption("Demo: `admin / wealthos123` · `demo / demo123`")

            if st.button("New here? Create an account", use_container_width=True, type="secondary"):
                st.session_state.auth_mode = "signup"
                st.rerun()

        else:
            with st.form("signup"):
                su  = st.text_input("Username")
                sp  = st.text_input("Password", type="password")
                sp2 = st.text_input("Confirm password", type="password")
                sok = st.form_submit_button("Create account", use_container_width=True, type="primary")
            if sok:
                if not su or not sp or not sp2:
                    st.error("All fields are required.")
                elif sp != sp2:
                    st.error("Passwords do not match.")
                elif len(su.strip()) < 3:
                    st.error("Username must be at least 3 characters.")
                elif len(sp) < 6:
                    st.error("Password must be at least 6 characters.")
                else:
                    try:
                        r = requests.post(f"{API_URL}/auth/signup",
                                          json={"username": su.strip(), "password": sp}, timeout=10)
                        if r.status_code == 200:
                            st.success("Account created — sign in below.")
                            st.session_state.auth_mode = "signin"
                        elif r.status_code == 409:
                            st.error("Username taken — try another.")
                        else:
                            st.error(r.json().get("detail", "Signup failed."))
                    except Exception:
                        st.error("Cannot reach backend.")

            if st.button("Already have an account? Sign in", use_container_width=True, type="secondary"):
                st.session_state.auth_mode = "signin"
                st.rerun()
    st.stop()


# ── Session defaults ──────────────────────────────────────────────────────────

USER_ID = st.session_state.user_id
st.session_state.setdefault("doc_status",   {})
st.session_state.setdefault("last_result",  None)
st.session_state.setdefault("viewing_memo", None)


# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown(
        '<div style="padding:1.25rem 0 0.5rem;">'
        '<span style="font-size:1.6rem;font-weight:700;color:#1f2328;letter-spacing:-0.03em;">Wealth<span style="color:#3b82f6;">OS</span></span>'
        '</div>',
        unsafe_allow_html=True,
    )
    initial = st.session_state.username[:1].upper()
    st.markdown(
        f'<div style="display:flex;align-items:center;gap:0.5rem;padding:0 0 0.75rem;">'
        f'<div style="width:24px;height:24px;border-radius:50%;background:rgba(59,130,246,0.2);'
        f'display:flex;align-items:center;justify-content:center;'
        f'font-size:0.7rem;font-weight:700;color:#3b82f6;flex-shrink:0;">{initial}</div>'
        f'<span style="font-size:0.82rem;color:#6e7781;font-weight:500;">{st.session_state.username}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

    st.divider()
    page = st.radio("nav", ["Analyze", "History"], label_visibility="collapsed")
    st.divider()

    st.markdown(
        '<div style="font-size:0.77rem;color:#8c959f;display:flex;align-items:center;gap:0.4rem;">'
        '<span style="width:6px;height:6px;border-radius:50%;background:#22c55e;'
        'display:inline-block;flex-shrink:0;"></span>8 agents active</div>',
        unsafe_allow_html=True,
    )
    st.markdown("")
    if st.button("Sign out", use_container_width=True):
        _cookies.remove("wo_user_id")
        _cookies.remove("wo_username")
        _cookies.remove("wo_token")
        for k in ("logged_in", "username", "user_id", "token", "doc_status", "last_result", "viewing_memo"):
            st.session_state.pop(k, None)
        st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# PAGE — ANALYZE
# ══════════════════════════════════════════════════════════════════════════════

if page == "Analyze":

    st.markdown(
        '<h1 style="font-size:1.6rem;font-weight:700;color:#1f2328;margin-bottom:0.25rem;">Analyze</h1>'
        '<p style="font-size:0.875rem;color:#8c959f;margin-top:0;margin-bottom:1.25rem;">Get a personalized investment memo powered by 8 AI agents.</p>',
        unsafe_allow_html=True,
    )

    # Memory banner — one line, only if memory exists
    mem_data = _api("get", f"/memory/{USER_ID}")
    if mem_data and mem_data.get("has_memory"):
        raw     = (mem_data["memory"] or "").replace("\n", " · ").strip()
        preview = raw[:160] + "…" if len(raw) > 160 else raw
        st.markdown(
            f'<div style="background:rgba(59,130,246,0.1);border:1px solid rgba(59,130,246,0.3);border-radius:6px;'
            f'padding:0.45rem 0.85rem;font-size:0.82rem;color:#2563eb;margin-bottom:1rem;">'
            f'<strong>Memory</strong>&nbsp;&nbsp;{preview}</div>',
            unsafe_allow_html=True,
        )

    # Analysis form
    with st.form("analyze_form"):
        query = st.text_area(
            "What do you want to know?",
            placeholder=(
                "e.g. I have around ₹30k–50k to invest and I'm fairly conservative. "
                "Should I add AAPL to my portfolio right now, or wait?"
            ),
            height=115,
        )

        fc1, fc2, fc3 = st.columns([1, 1, 2])
        ticker  = fc1.text_input("Ticker", placeholder="AAPL")
        amount  = fc2.number_input(
            "Investment amount (₹)", min_value=0, value=20000, step=5000,
        )
        horizon = fc3.radio(
            "Horizon",
            ["Short-term", "Mid-term", "Long-term", "Let AI decide"],
            horizontal=True,
            index=2,
        )
        mock = st.checkbox("Mock mode (no backend needed)", value=False)
        submitted = st.form_submit_button(
            "Run analysis", use_container_width=True, type="primary"
        )

    # File uploader — below form, outside it so indexing fires immediately
    st.markdown("<div style='height:0.75rem'></div>", unsafe_allow_html=True)
    st.markdown(
        '<span style="font-size:0.875rem;font-weight:500;color:#57606a;">Attach documents</span>'
        '<span style="font-size:0.78rem;color:#8c959f;margin-left:0.5rem;">'
        'Upload salary slips, bank statements, or loan docs so WealthOS can give you truly personalised advice based on your actual financial situation (PDF)</span>',
        unsafe_allow_html=True,
    )
    uploaded = st.file_uploader(
        "docs",
        type=["pdf"],
        accept_multiple_files=True,
        label_visibility="collapsed",
        key="doc_uploader",
    )

    if uploaded:
        for uf in uploaded:
            if uf.name not in st.session_state.doc_status:
                st.session_state.doc_status[uf.name] = "indexing"
                with st.spinner(f"Indexing {uf.name}…"):
                    try:
                        r = requests.post(
                            f"{API_URL}/upload-personal-doc",
                            data={"user_id": USER_ID},
                            files={"file": (uf.name, uf.getvalue(), "application/pdf")},
                            timeout=120,
                        )
                        st.session_state.doc_status[uf.name] = "ready" if r.ok else "error"
                    except Exception:
                        st.session_state.doc_status[uf.name] = "error"

    if st.session_state.doc_status:
        parts = []
        for name, status in st.session_state.doc_status.items():
            icon  = "✓" if status == "ready" else "×" if status == "error" else "…"
            color = "#22c55e" if status == "ready" else "#ef4444" if status == "error" else "#8c959f"
            parts.append(f'<span style="color:{color};font-size:0.8rem;">{icon} {name}</span>')
        st.markdown("&nbsp;&nbsp;".join(parts) + "<br>", unsafe_allow_html=True)

    if submitted:
        if not mock and not ticker:
            st.error("Enter a ticker symbol.")
            st.stop()
        if not mock and not query:
            st.error("Write your question first.")
            st.stop()

        ticker = (ticker or "AAPL").upper().strip()
        h_map  = {
            "Short-term":    "short",
            "Mid-term":      "mid",
            "Long-term":     "long",
            "Let AI decide": None,
        }
        sel_horizon = h_map[horizon]

        if mock:
            import time as _t
            with st.spinner(f"Running mock analysis for {ticker}…"):
                _t.sleep(2)
            st.session_state.last_result = {
                "ticker": ticker,
                "verdict": "Hold",
                "risk_score": 5,
                "dcf_value": 162.40,
                "final_memo": (
                    f"## {ticker} — Investment Analysis\n\n"
                    "### Financial Snapshot\n"
                    "Revenue $383B (10-K FY2024) · Net Income $97B · P/E 28x · FCF $107B\n\n"
                    "### Risk Assessment\n"
                    "Risk score 5/10 (Moderate). Valuation is reasonable; "
                    "macro headwinds are mild. Balance sheet is strong.\n\n"
                    "### Final Verdict\n"
                    "**Hold.** Fair value near current price. "
                    "Add on a 5–8% pullback rather than at current levels."
                ),
                "messages": [
                    "Router Node ✅", "Finance Node ✅", "Data Node ✅",
                    "Risk Node ✅", "Code Node ✅", "Writer Node ✅",
                ],
                "error": None,
            }
        else:
            payload = {
                "query":         query or f"Analyze {ticker}",
                "ticker":        ticker,
                "user_id":       USER_ID,
                "invest_amount": float(amount),
            }
            if sel_horizon:
                payload["investment_horizon"] = sel_horizon
            with st.spinner(f"Running 8 agents for {ticker}… (~60 s)"):
                try:
                    r = requests.post(f"{API_URL}/analyze", json=payload, timeout=180)
                    if r.status_code == 200:
                        st.session_state.last_result = r.json()
                    elif r.status_code == 429:
                        st.error("Rate limit reached — max 10 analyses per minute. Try again shortly.")
                        st.stop()
                    else:
                        st.error(f"Backend error {r.status_code}: {r.text[:200]}")
                        st.stop()
                except requests.exceptions.ConnectionError:
                    st.error("Cannot connect to backend at localhost:8000. Is it running?")
                    st.stop()
                except requests.exceptions.Timeout:
                    st.error("Request timed out after 180 s.")
                    st.stop()

    # Results section
    res = st.session_state.get("last_result")
    if res:
        st.divider()

        verdict    = res.get("verdict") or ""
        risk_score = res.get("risk_score")
        dcf        = res.get("dcf_value")
        memo       = res.get("final_memo") or res.get("memo") or ""
        messages   = res.get("messages", [])

        if res.get("error"):
            st.warning(f"Agent warning: {res['error']}")

        col_v, col_r, col_d = st.columns(3)
        with col_v:
            st.markdown(
                f'<div style="padding:0.75rem 0 0.25rem;">'
                f'<div style="font-size:0.7rem;color:#8c959f;text-transform:uppercase;'
                f'letter-spacing:0.06em;margin-bottom:0.45rem;">Verdict</div>'
                f'{verdict_pill(verdict)}</div>',
                unsafe_allow_html=True,
            )
        with col_r:
            st.markdown(
                f'<div style="padding:0.75rem 0 0.25rem;">'
                f'<div style="font-size:0.7rem;color:#8c959f;text-transform:uppercase;'
                f'letter-spacing:0.06em;margin-bottom:0.55rem;">Risk</div>'
                f'{risk_bar_html(risk_score)}</div>',
                unsafe_allow_html=True,
            )
        with col_d:
            dcf_str = f"${dcf:,.2f}" if dcf else "—"
            st.markdown(
                f'<div style="padding:0.75rem 0 0.25rem;">'
                f'<div style="font-size:0.7rem;color:#8c959f;text-transform:uppercase;'
                f'letter-spacing:0.06em;margin-bottom:0.35rem;">DCF value</div>'
                f'<div style="font-size:1.3rem;font-weight:700;color:#1f2328;">{dcf_str}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

        if memo:
            st.markdown(
                '<div style="border:1px solid #d8dee4;border-radius:8px;'
                'padding:1.5rem 1.75rem;background:#f6f8fa;margin-top:0.75rem;">',
                unsafe_allow_html=True,
            )
            st.markdown(memo.replace("$", "\\$"))
            st.markdown("</div>", unsafe_allow_html=True)

            c1, _ = st.columns([1, 4])
            c1.download_button(
                "Download memo",
                data=memo,
                file_name=f"{res.get('ticker', 'analysis')}_memo.txt",
                mime="text/plain",
            )

        if messages:
            with st.expander("Agent log", expanded=False):
                for m in messages:
                    st.caption(m)


# ══════════════════════════════════════════════════════════════════════════════
# PAGE — HISTORY
# ══════════════════════════════════════════════════════════════════════════════

elif page == "History":

    tab_analyses, tab_memory = st.tabs(["Analyses", "Memory"])

    # ── Analyses tab ──────────────────────────────────────────────────────────
    with tab_analyses:
        st.markdown("<div style='height:0.5rem'></div>", unsafe_allow_html=True)

        history_data = _api("get", f"/history/{USER_ID}?limit=50")
        history      = (history_data or {}).get("history", [])

        fc1, fc2 = st.columns([3, 1])
        search  = fc1.text_input("Search", placeholder="Ticker, e.g. AAPL", label_visibility="collapsed")
        vfilter = fc2.selectbox("Verdict", ["All", "Buy", "Hold", "Avoid"], label_visibility="collapsed")

        filtered = history
        if search:
            filtered = [h for h in filtered if search.upper() in (h.get("ticker") or "").upper()]
        if vfilter != "All":
            filtered = [h for h in filtered if vfilter.lower() in (h.get("verdict") or "").lower()]

        if not history:
            st.markdown("<div style='height:0.75rem'></div>", unsafe_allow_html=True)
            st.info("No analyses yet — run your first one from the Analyze page.")
        elif not filtered:
            st.caption("No results match your filter.")
        else:
            st.markdown("<div style='height:0.5rem'></div>", unsafe_allow_html=True)
            for i, h in enumerate(filtered):
                ticker_h = h.get("ticker") or "—"
                verdict  = h.get("verdict") or ""
                risk     = h.get("risk_score")
                dcf      = h.get("dcf_value")
                date     = fmt_date(h.get("created_at", ""))
                meta     = " · ".join(filter(None, [
                    f"Risk {risk}/10" if risk else "",
                    f"DCF ${dcf:,.2f}" if dcf else "",
                    date,
                ]))
                c_info, c_btn = st.columns([6, 1])
                with c_info:
                    st.markdown(
                        f'<div style="padding:0.55rem 0;border-bottom:1px solid #d8dee4;">'
                        f'<div style="display:flex;align-items:center;gap:0.55rem;">'
                        f'<span style="font-weight:600;color:#1f2328;font-size:0.9rem;">{ticker_h}</span>'
                        f'{verdict_pill(verdict)}</div>'
                        f'<div style="font-size:0.77rem;color:#8c959f;margin-top:0.15rem;">{meta}</div>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
                with c_btn:
                    st.markdown("<div style='padding-top:0.4rem'>", unsafe_allow_html=True)
                    if st.button("View", key=f"v_{i}", type="secondary"):
                        st.session_state.viewing_memo = h
                    st.markdown("</div>", unsafe_allow_html=True)

    # Memo dialog
    if st.session_state.get("viewing_memo"):
        @st.dialog("Investment memo", width="large")
        def _memo_dialog():
            h    = st.session_state.viewing_memo
            memo = h.get("memo") or ""
            st.caption(
                f"{h.get('ticker', '—')} · {fmt_date(h.get('created_at', ''))} · "
                f"{h.get('verdict', '—')}"
            )
            st.divider()
            if memo:
                st.markdown(memo.replace("$", "\\$"))
                st.download_button(
                    "Download",
                    data=memo,
                    file_name=f"{h.get('ticker', 'analysis')}_memo.txt",
                    mime="text/plain",
                )
            else:
                st.info("Full memo not stored for this run.")
            if st.button("Close"):
                st.session_state.viewing_memo = None
                st.rerun()
        _memo_dialog()

    # ── Memory tab ────────────────────────────────────────────────────────────
    with tab_memory:
        st.markdown("<div style='height:0.5rem'></div>", unsafe_allow_html=True)

        # Mem0
        st.markdown(
            '<span style="font-size:0.875rem;font-weight:600;color:#1f2328;">'
            'What WealthOS knows about you</span>',
            unsafe_allow_html=True,
        )
        mem_data = _api("get", f"/memory/{USER_ID}")
        mem_text = (mem_data or {}).get("memory", "")
        if mem_text:
            for s in [ln.strip().lstrip("- ") for ln in mem_text.split("\n") if ln.strip()][:6]:
                st.markdown(
                    f'<div style="padding:0.4rem 0;font-size:0.875rem;color:#57606a;'
                    f'border-bottom:1px solid #d8dee4;">{s}</div>',
                    unsafe_allow_html=True,
                )
            st.markdown("<div style='height:0.5rem'></div>", unsafe_allow_html=True)
            if st.button("Clear memory", type="secondary"):
                r = requests.delete(f"{API_URL}/memory/{USER_ID}", headers=_auth_headers(), timeout=10)
                if r.ok:
                    st.success("Memory cleared.")
                    st.rerun()
                else:
                    st.error("Could not clear memory.")
        else:
            st.caption("No memory yet. Run an analysis to start building context.")

        st.markdown("<div style='height:1.25rem'></div>", unsafe_allow_html=True)

        # Investor profile
        st.markdown(
            '<span style="font-size:0.875rem;font-weight:600;color:#1f2328;">Your investor profile</span>',
            unsafe_allow_html=True,
        )
        profile_data = _api("get", f"/user-profile/{USER_ID}")
        profile      = (profile_data or {}).get("profile")
        if profile:
            total  = profile.get("total_analyses") or 0
            buys   = profile.get("buy_count")   or 0
            holds  = profile.get("hold_count")  or 0
            avoids = profile.get("avoid_count") or 0
            avg_r  = profile.get("avg_risk_score")
            sectors = profile.get("preferred_sectors") or []

            pc1, pc2, pc3 = st.columns(3)
            pc1.metric("Analyses", total)
            pc2.metric("Buy / Hold / Avoid", f"{buys} · {holds} · {avoids}")
            pc3.metric("Avg risk", f"{avg_r:.1f}/10" if avg_r else "—")

            if sectors:
                tags = "".join(
                    f'<span style="background:rgba(59,130,246,0.15);color:#3b82f6;font-size:0.75rem;'
                    f'font-weight:500;padding:0.18rem 0.5rem;border-radius:999px;'
                    f'margin-right:0.3rem;border:1px solid rgba(59,130,246,0.3);">{s}</span>'
                    for s in sectors
                )
                st.markdown(
                    f'<div style="margin-top:0.6rem;">'
                    f'<span style="font-size:0.78rem;color:#8c959f;margin-right:0.5rem;">Sectors</span>'
                    f'{tags}</div>',
                    unsafe_allow_html=True,
                )
        else:
            st.caption("Profile builds after your first completed analysis.")

        st.markdown("<div style='height:1.25rem'></div>", unsafe_allow_html=True)

        # Past decisions
        st.markdown(
            '<span style="font-size:0.875rem;font-weight:600;color:#1f2328;">'
            'Past decisions</span>',
            unsafe_allow_html=True,
        )
        st.caption(
            "These verdict embeddings are retrieved during every new risk analysis "
            "to keep recommendations consistent with your history."
        )
        analyses_data = _api("get", f"/user-analyses/{USER_ID}?limit=8")
        analyses      = (analyses_data or {}).get("analyses", [])
        if analyses:
            # st.dataframe renders inside a sandboxed iframe that page-level CSS
            # injection can't theme — hand-built HTML table stays consistent
            # with the rest of the dark UI instead.
            verdict_color = {"Buy": "#22c55e", "Hold": "#eab308", "Avoid": "#ef4444"}
            rows_html = ""
            for a in analyses:
                v     = a.get("verdict", "—")
                color = verdict_color.get(v, "#6e7781")
                rows_html += (
                    '<tr style="border-bottom:1px solid #d8dee4;">'
                    f'<td style="padding:0.5rem 0.75rem;color:#1f2328;font-weight:600;">{a.get("ticker","—")}</td>'
                    f'<td style="padding:0.5rem 0.75rem;color:#6e7781;">{fmt_date(a.get("analysis_date",""))}</td>'
                    f'<td style="padding:0.5rem 0.75rem;color:{color};font-weight:600;">{v}</td>'
                    f'<td style="padding:0.5rem 0.75rem;color:#57606a;font-size:0.82rem;">{(a.get("verdict_text") or "")[:100]}</td>'
                    '</tr>'
                )
            st.markdown(
                '<table style="width:100%;border-collapse:collapse;background:#f6f8fa;'
                'border:1px solid #d8dee4;border-radius:8px;overflow:hidden;">'
                '<thead><tr style="border-bottom:1px solid #c9d1d9;">'
                '<th style="text-align:left;padding:0.5rem 0.75rem;color:#8c959f;font-size:0.78rem;">Ticker</th>'
                '<th style="text-align:left;padding:0.5rem 0.75rem;color:#8c959f;font-size:0.78rem;">Date</th>'
                '<th style="text-align:left;padding:0.5rem 0.75rem;color:#8c959f;font-size:0.78rem;">Verdict</th>'
                '<th style="text-align:left;padding:0.5rem 0.75rem;color:#8c959f;font-size:0.78rem;">Excerpt</th>'
                f'</tr></thead><tbody>{rows_html}</tbody></table>',
                unsafe_allow_html=True,
            )
        else:
            st.caption("No past decisions stored yet.")
