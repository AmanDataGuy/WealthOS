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
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

html, body, [class*="css"], * {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif !important;
}

[data-testid="stAppViewContainer"],
[data-testid="stMain"],
section.main { background: #ffffff !important; }
.main .block-container { padding: 2rem 2.5rem 3rem; max-width: 1080px; }

#MainMenu, footer,
[data-testid="stToolbar"],
[data-testid="stDecoration"] { display: none !important; }

[data-testid="stSidebar"] {
    background: #f8fafc !important;
    border-right: 1px solid #e5e7eb !important;
}
section[data-testid="stSidebar"] {
    transform: none !important;
    min-width: 210px !important;
    width: 210px !important;
}
[data-testid="stSidebarCollapseButton"],
[data-testid="collapsedControl"] { display: none !important; }

h1 { color: #111827 !important; font-size: 1.4rem !important; font-weight: 700 !important; margin: 0 0 0.15rem !important; }
h2 { color: #111827 !important; font-size: 1rem !important; font-weight: 600 !important; margin: 0 0 0.6rem !important; }
h3 { color: #374151 !important; font-size: 0.9rem !important; font-weight: 600 !important; }
p, li { color: #374151 !important; }

[data-testid="stTextInput"] input,
[data-testid="stTextArea"] textarea,
[data-testid="stNumberInput"] input {
    background: #ffffff !important;
    border: 1px solid #d1d5db !important;
    border-radius: 6px !important;
    color: #111827 !important;
    font-size: 0.9rem !important;
}
[data-testid="stTextInput"] input:focus,
[data-testid="stTextArea"] textarea:focus {
    border-color: #2563eb !important;
    box-shadow: 0 0 0 3px #dbeafe !important;
}

[data-testid="stButton"] button,
[data-testid="stFormSubmitButton"] button {
    border-radius: 6px !important;
    font-weight: 500 !important;
    font-size: 0.875rem !important;
    transition: all 0.15s !important;
}
[data-testid="stButton"] button[kind="primary"],
[data-testid="stFormSubmitButton"] button {
    background: #2563eb !important;
    border: none !important;
    color: #ffffff !important;
    font-weight: 600 !important;
}
[data-testid="stButton"] button[kind="primary"]:hover,
[data-testid="stFormSubmitButton"] button:hover { background: #1d4ed8 !important; }
[data-testid="stButton"] button[kind="secondary"] {
    background: #ffffff !important;
    border: 1px solid #d1d5db !important;
    color: #374151 !important;
}
[data-testid="stButton"] button[kind="secondary"]:hover {
    border-color: #2563eb !important;
    color: #2563eb !important;
}

[data-baseweb="tab-list"] {
    border-bottom: 1px solid #e5e7eb !important;
    background: transparent !important;
    gap: 0 !important;
}
[data-baseweb="tab"] {
    color: #6b7280 !important;
    font-size: 0.875rem !important;
    font-weight: 500 !important;
    padding: 0.6rem 1.25rem !important;
    border-bottom: 2px solid transparent !important;
    background: transparent !important;
}
[data-baseweb="tab"]:hover { color: #374151 !important; background: transparent !important; }
[aria-selected="true"][data-baseweb="tab"] {
    color: #2563eb !important;
    border-bottom: 2px solid #2563eb !important;
}

[data-testid="stMetric"] {
    background: #f8fafc !important;
    border: 1px solid #e5e7eb !important;
    border-radius: 8px !important;
    padding: 1rem 1.25rem !important;
}
[data-testid="stMetricLabel"] { color: #6b7280 !important; font-size: 0.72rem !important; text-transform: uppercase; letter-spacing: 0.05em; }
[data-testid="stMetricValue"] { color: #111827 !important; font-size: 1.3rem !important; font-weight: 700 !important; }

[data-testid="stDataFrame"] {
    border: 1px solid #e5e7eb !important;
    border-radius: 8px !important;
    overflow: hidden !important;
}

[data-testid="stFileUploader"] section {
    border: 1.5px dashed #d1d5db !important;
    border-radius: 8px !important;
    background: #f8fafc !important;
}

[data-testid="stExpander"] {
    border: 1px solid #e5e7eb !important;
    border-radius: 8px !important;
    background: #ffffff !important;
}

[data-testid="stAlert"] { border-radius: 6px !important; font-size: 0.875rem !important; }
hr { border-color: #e5e7eb !important; margin: 1.25rem 0 !important; }

[data-baseweb="select"] > div {
    background: #ffffff !important;
    border: 1px solid #d1d5db !important;
    border-radius: 6px !important;
}

[data-testid="stSidebar"] [data-testid="stRadio"] label {
    padding: 0.45rem 0.75rem !important;
    border-radius: 6px !important;
    font-size: 0.875rem !important;
    font-weight: 500 !important;
    color: #374151 !important;
    display: block;
    cursor: pointer;
}
[data-testid="stSidebar"] [data-testid="stRadio"] label:hover {
    background: #eff6ff !important;
    color: #2563eb !important;
}

small, .stCaptionContainer, [data-testid="stCaptionContainer"] {
    color: #9ca3af !important;
    font-size: 0.78rem !important;
}
</style>
""", unsafe_allow_html=True)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _api(method: str, path: str, **kwargs):
    try:
        r = getattr(requests, method)(
            f"{API_URL}{path}", timeout=kwargs.pop("timeout", 12), **kwargs
        )
        return r.json() if r.ok else None
    except Exception:
        return None


def verdict_pill(v: str) -> str:
    cfg = {
        "buy":   ("background:#eff6ff;color:#2563eb;border:1px solid #bfdbfe;", "Buy"),
        "hold":  ("background:#f3f4f6;color:#6b7280;border:1px solid #d1d5db;", "Hold"),
        "avoid": ("background:#111827;color:#ffffff;border:1px solid #111827;", "Avoid"),
    }
    style, label = cfg.get((v or "").lower(),
                           ("background:#f3f4f6;color:#6b7280;border:1px solid #d1d5db;", v or "—"))
    return (
        f'<span style="{style}font-size:0.75rem;font-weight:600;'
        f'padding:0.2rem 0.6rem;border-radius:999px;letter-spacing:0.03em;">'
        f'{label}</span>'
    )


def risk_bar_html(score) -> str:
    if not score:
        return '<span style="color:#9ca3af;font-size:0.82rem;">—</span>'
    pct   = int(score) * 10
    color = "#2563eb" if pct <= 40 else "#6b7280" if pct <= 60 else "#111827"
    return (
        f'<div style="display:flex;align-items:center;gap:0.6rem;">'
        f'<div style="flex:1;background:#e5e7eb;border-radius:4px;height:5px;">'
        f'<div style="background:{color};width:{pct}%;height:5px;border-radius:4px;"></div></div>'
        f'<span style="font-size:0.82rem;color:#374151;font-weight:600;white-space:nowrap;">{score}/10</span>'
        f'</div>'
    )


def alloc_bar_html(ticker: str, weight: float, max_w: float) -> str:
    pct = (weight / max_w * 100) if max_w else 0
    return (
        f'<div style="margin-bottom:0.55rem;">'
        f'<div style="display:flex;justify-content:space-between;font-size:0.82rem;margin-bottom:0.2rem;">'
        f'<span style="color:#111827;font-weight:500;">{ticker}</span>'
        f'<span style="color:#6b7280;">{weight:.1f}%</span></div>'
        f'<div style="background:#e5e7eb;border-radius:4px;height:6px;">'
        f'<div style="background:#2563eb;width:{min(pct,100):.0f}%;height:6px;border-radius:4px;"></div>'
        f'</div></div>'
    )


def fmt_date(ts: str) -> str:
    return ts[:10] if ts else "—"


# ── Auth ──────────────────────────────────────────────────────────────────────

_cookies = CookieController()

if not st.session_state.get("logged_in"):
    uid  = _cookies.get("wo_user_id")
    user = _cookies.get("wo_username")
    if uid and user:
        st.session_state.logged_in = True
        st.session_state.username  = user
        st.session_state.user_id   = uid
    else:
        st.session_state.setdefault("logged_in", False)
        st.session_state.setdefault("username",  "")
        st.session_state.setdefault("user_id",   "")

if not st.session_state.logged_in:
    _, col, _ = st.columns([1, 1.1, 1])
    with col:
        st.markdown(
            '<div style="text-align:center;padding:2.5rem 0 1.75rem;">'
            '<div style="font-size:1.75rem;font-weight:700;color:#111827;letter-spacing:-0.02em;">WealthOS</div>'
            '<div style="font-size:0.85rem;color:#6b7280;margin-top:0.35rem;">Personal financial intelligence</div>'
            '</div>',
            unsafe_allow_html=True,
        )

        t_in, t_up = st.tabs(["Sign in", "Create account"])

        with t_in:
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
                            _cookies.set("wo_user_id",  d["user_id"],  max_age=30 * 24 * 3600)
                            _cookies.set("wo_username", d["username"], max_age=30 * 24 * 3600)
                            st.rerun()
                        elif r.status_code == 503:
                            st.error("Backend offline — start the API server first.")
                        else:
                            st.error("Invalid username or password.")
                    except Exception:
                        st.error("Cannot reach backend at localhost:8000.")
            st.caption("Demo: `admin / wealthos123` · `demo / demo123`")

        with t_up:
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
                            st.success("Account created. Sign in above.")
                        elif r.status_code == 409:
                            st.error("Username taken — try another.")
                        else:
                            st.error(r.json().get("detail", "Signup failed."))
                    except Exception:
                        st.error("Cannot reach backend.")
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
        '<span style="font-size:1.05rem;font-weight:700;color:#111827;letter-spacing:-0.01em;">WealthOS</span>'
        '</div>',
        unsafe_allow_html=True,
    )
    initial = st.session_state.username[:1].upper()
    st.markdown(
        f'<div style="display:flex;align-items:center;gap:0.5rem;padding:0 0 0.75rem;">'
        f'<div style="width:24px;height:24px;border-radius:50%;background:#dbeafe;'
        f'display:flex;align-items:center;justify-content:center;'
        f'font-size:0.7rem;font-weight:700;color:#2563eb;flex-shrink:0;">{initial}</div>'
        f'<span style="font-size:0.82rem;color:#374151;font-weight:500;">{st.session_state.username}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

    st.divider()
    page = st.radio("nav", ["Analyze", "History", "Settings"], label_visibility="collapsed")
    st.divider()

    st.markdown(
        '<div style="font-size:0.77rem;color:#6b7280;display:flex;align-items:center;gap:0.4rem;">'
        '<span style="width:6px;height:6px;border-radius:50%;background:#16a34a;'
        'display:inline-block;flex-shrink:0;"></span>8 agents active</div>',
        unsafe_allow_html=True,
    )
    st.markdown("")
    if st.button("Sign out", use_container_width=True):
        _cookies.remove("wo_user_id")
        _cookies.remove("wo_username")
        for k in ("logged_in", "username", "user_id", "doc_status", "last_result", "viewing_memo"):
            st.session_state.pop(k, None)
        st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# PAGE — ANALYZE
# ══════════════════════════════════════════════════════════════════════════════

if page == "Analyze":

    # Memory banner — one line, only if memory exists
    mem_data = _api("get", f"/memory/{USER_ID}")
    if mem_data and mem_data.get("has_memory"):
        raw     = (mem_data["memory"] or "").replace("\n", " · ").strip()
        preview = raw[:160] + "…" if len(raw) > 160 else raw
        st.markdown(
            f'<div style="background:#eff6ff;border:1px solid #bfdbfe;border-radius:6px;'
            f'padding:0.45rem 0.85rem;font-size:0.82rem;color:#1d4ed8;margin-bottom:1rem;">'
            f'<strong>Memory</strong>&nbsp;&nbsp;{preview}</div>',
            unsafe_allow_html=True,
        )

    # File uploader — outside form so indexing fires immediately on drop
    st.markdown(
        '<span style="font-size:0.875rem;font-weight:500;color:#374151;">Attach documents</span>'
        '<span style="font-size:0.78rem;color:#9ca3af;margin-left:0.5rem;">'
        'salary slips, bank statements, loan docs (PDF)</span>',
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
            color = "#16a34a" if status == "ready" else "#dc2626" if status == "error" else "#6b7280"
            parts.append(f'<span style="color:{color};font-size:0.8rem;">{icon} {name}</span>')
        st.markdown("&nbsp;&nbsp;".join(parts) + "<br>", unsafe_allow_html=True)

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

        fc1, fc2 = st.columns([1, 2])
        ticker  = fc1.text_input("Ticker", placeholder="AAPL")
        horizon = fc2.radio(
            "Horizon",
            ["Short-term", "Mid-term", "Long-term", "Let AI decide"],
            horizontal=True,
            index=2,
        )
        mock = st.checkbox("Mock mode (no backend needed)", value=False)
        submitted = st.form_submit_button(
            "Run analysis", use_container_width=True, type="primary"
        )

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
                "query":   query or f"Analyze {ticker}",
                "ticker":  ticker,
                "user_id": USER_ID,
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
                f'<div style="font-size:0.7rem;color:#6b7280;text-transform:uppercase;'
                f'letter-spacing:0.06em;margin-bottom:0.45rem;">Verdict</div>'
                f'{verdict_pill(verdict)}</div>',
                unsafe_allow_html=True,
            )
        with col_r:
            st.markdown(
                f'<div style="padding:0.75rem 0 0.25rem;">'
                f'<div style="font-size:0.7rem;color:#6b7280;text-transform:uppercase;'
                f'letter-spacing:0.06em;margin-bottom:0.55rem;">Risk</div>'
                f'{risk_bar_html(risk_score)}</div>',
                unsafe_allow_html=True,
            )
        with col_d:
            dcf_str = f"${dcf:,.2f}" if dcf else "—"
            st.markdown(
                f'<div style="padding:0.75rem 0 0.25rem;">'
                f'<div style="font-size:0.7rem;color:#6b7280;text-transform:uppercase;'
                f'letter-spacing:0.06em;margin-bottom:0.35rem;">DCF value</div>'
                f'<div style="font-size:1.3rem;font-weight:700;color:#111827;">{dcf_str}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

        if memo:
            st.markdown(
                '<div style="border:1px solid #e5e7eb;border-radius:8px;'
                'padding:1.5rem 1.75rem;background:#ffffff;margin-top:0.75rem;">',
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
                        f'<div style="padding:0.55rem 0;border-bottom:1px solid #f3f4f6;">'
                        f'<div style="display:flex;align-items:center;gap:0.55rem;">'
                        f'<span style="font-weight:600;color:#111827;font-size:0.9rem;">{ticker_h}</span>'
                        f'{verdict_pill(verdict)}</div>'
                        f'<div style="font-size:0.77rem;color:#9ca3af;margin-top:0.15rem;">{meta}</div>'
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
            '<span style="font-size:0.875rem;font-weight:600;color:#111827;">'
            'What WealthOS knows about you</span>',
            unsafe_allow_html=True,
        )
        mem_data = _api("get", f"/memory/{USER_ID}")
        mem_text = (mem_data or {}).get("memory", "")
        if mem_text:
            for s in [ln.strip().lstrip("- ") for ln in mem_text.split("\n") if ln.strip()][:6]:
                st.markdown(
                    f'<div style="padding:0.4rem 0;font-size:0.875rem;color:#374151;'
                    f'border-bottom:1px solid #f3f4f6;">{s}</div>',
                    unsafe_allow_html=True,
                )
            st.markdown("<div style='height:0.5rem'></div>", unsafe_allow_html=True)
            if st.button("Clear memory", type="secondary"):
                r = requests.delete(f"{API_URL}/memory/{USER_ID}", timeout=10)
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
            '<span style="font-size:0.875rem;font-weight:600;color:#111827;">Your investor profile</span>',
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
                    f'<span style="background:#eff6ff;color:#2563eb;font-size:0.75rem;'
                    f'font-weight:500;padding:0.18rem 0.5rem;border-radius:999px;'
                    f'margin-right:0.3rem;border:1px solid #bfdbfe;">{s}</span>'
                    for s in sectors
                )
                st.markdown(
                    f'<div style="margin-top:0.6rem;">'
                    f'<span style="font-size:0.78rem;color:#6b7280;margin-right:0.5rem;">Sectors</span>'
                    f'{tags}</div>',
                    unsafe_allow_html=True,
                )
        else:
            st.caption("Profile builds after your first completed analysis.")

        st.markdown("<div style='height:1.25rem'></div>", unsafe_allow_html=True)

        # Past decisions
        st.markdown(
            '<span style="font-size:0.875rem;font-weight:600;color:#111827;">'
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
            rows = [
                {
                    "Ticker":  a.get("ticker", "—"),
                    "Date":    fmt_date(a.get("analysis_date", "")),
                    "Verdict": a.get("verdict", "—"),
                    "Excerpt": (a.get("verdict_text") or "")[:100],
                }
                for a in analyses
            ]
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        else:
            st.caption("No past decisions stored yet.")


# ══════════════════════════════════════════════════════════════════════════════
# PAGE — SETTINGS
# ══════════════════════════════════════════════════════════════════════════════

elif page == "Settings":
    st.markdown("<div style='height:0.25rem'></div>", unsafe_allow_html=True)

    st.markdown(
        '<span style="font-size:0.875rem;font-weight:600;color:#111827;">Portfolio</span>',
        unsafe_allow_html=True,
    )
    portfolio_data = _api("get", f"/portfolio/{USER_ID}")
    holdings       = (portfolio_data or {}).get("holdings", [])

    if not holdings:
        st.info("No holdings found. Add them via the portfolio MCP server or the API.")
    else:
        df = pd.DataFrame(holdings)
        display_cols = [c for c in ["ticker", "quantity", "avg_buy_price", "target_weight", "sector"]
                        if c in df.columns]

        col_t, col_c = st.columns([3, 2])
        with col_t:
            st.dataframe(df[display_cols], use_container_width=True, hide_index=True)
        with col_c:
            if "target_weight" in df.columns:
                max_w = float(df["target_weight"].max() or 1)
                for _, row in df.iterrows():
                    st.markdown(
                        alloc_bar_html(row["ticker"], float(row.get("target_weight") or 0), max_w),
                        unsafe_allow_html=True,
                    )

    # Rebalance suggestion from last analysis
    res = st.session_state.get("last_result")
    if res and res.get("rebalance_suggestion"):
        st.divider()
        st.markdown(
            '<span style="font-size:0.875rem;font-weight:600;color:#111827;">'
            'Last rebalancing suggestion</span>',
            unsafe_allow_html=True,
        )
        actions = (res["rebalance_suggestion"] or {}).get("actions", [])
        if actions:
            for a in actions:
                urg   = (a.get("urgency") or "").capitalize()
                u_col = "#dc2626" if a.get("urgency") == "high" else "#6b7280"
                st.markdown(
                    f'<div style="padding:0.4rem 0;border-bottom:1px solid #f3f4f6;font-size:0.875rem;">'
                    f'<span style="color:#374151;">'
                    f'{a.get("action","").capitalize()} <strong>{a.get("ticker","")}</strong> '
                    f'{a.get("from_weight","")}% → {a.get("to_weight","")}%</span>'
                    f'<span style="float:right;font-size:0.75rem;color:{u_col};">{urg}</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
        else:
            st.caption("No rebalancing actions needed.")
    else:
        st.caption("Run an analysis from the Analyze page to see rebalancing suggestions here.")

    # Risk tolerance
    st.divider()
    st.markdown(
        '<span style="font-size:0.875rem;font-weight:600;color:#111827;">Risk tolerance</span>',
        unsafe_allow_html=True,
    )
    st.radio(
        "risk",
        [
            "Conservative — capital preservation, low volatility",
            "Moderate — balanced growth and safety",
            "Aggressive — maximum growth, volatility is fine",
        ],
        label_visibility="collapsed",
        index=1,
    )
    st.caption(
        "Mention your risk preference in the Analyze message — "
        "the agent reads it directly from your query."
    )
