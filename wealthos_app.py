import os
import streamlit as st
import requests
import time
import random
import json
import pandas as pd
from datetime import datetime
from streamlit_cookies_controller import CookieController

# ── Config ────────────────────────────────────────────────────────────────────
API_URL = os.getenv("WEALTHOS_API_URL", "http://localhost:8000")

st.set_page_config(
    page_title="WealthOS — AI Finance",
    page_icon="💰",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
/* ── Global ──────────────────────────────────────────────────── */
[data-testid="stAppViewContainer"] { background: #0f1117; }
[data-testid="stSidebar"] { background: #161b27; border-right: 1px solid #2d3348; }
[data-testid="stSidebar"] * { color: #e0e6f0 !important; }

/* ── Hide default Streamlit chrome ───────────────────────────── */
#MainMenu { visibility: hidden; }
footer { visibility: hidden; }
[data-testid="stToolbar"] { visibility: hidden; }
[data-testid="stDecoration"] { display: none; }

/* ── Permanent sidebar ───────────────────────────────────────── */
/* Override Streamlit's translateX(-110%) that hides a collapsed sidebar */
section[data-testid="stSidebar"] {
    transform: none !important;
    min-width: 280px !important;
    width: 280px !important;
    display: flex !important;
    visibility: visible !important;
}
/* Hide the collapse button — user can never trigger collapse */
[data-testid="stSidebarCollapseButton"] { display: none !important; }
[data-testid="collapsedControl"]        { display: none !important; }

/* ── Auth card ───────────────────────────────────────────────── */
.auth-hero {
    text-align: center;
    padding: 2.5rem 0 1.5rem;
}
.auth-hero h1 {
    font-size: 2.8rem;
    font-weight: 800;
    background: linear-gradient(135deg, #4f9cf9, #a855f7);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    margin: 0;
}
.auth-hero p {
    color: #8b9ab5;
    font-size: 1rem;
    margin-top: 0.4rem;
}

/* ── Metric cards ────────────────────────────────────────────── */
[data-testid="stMetric"] {
    background: #1e2435;
    border: 1px solid #2d3348;
    border-radius: 12px;
    padding: 1rem 1.25rem;
}
[data-testid="stMetricLabel"] { color: #8b9ab5 !important; font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.05em; }
[data-testid="stMetricValue"] { color: #e0e6f0 !important; font-size: 1.6rem; font-weight: 700; }
[data-testid="stMetricDelta"] { color: #4f9cf9 !important; }

/* ── Dataframe ───────────────────────────────────────────────── */
[data-testid="stDataFrame"] { border: 1px solid #2d3348; border-radius: 10px; overflow: hidden; }

/* ── Buttons ─────────────────────────────────────────────────── */
[data-testid="stButton"] button[kind="primary"] {
    background: linear-gradient(135deg, #4f9cf9, #a855f7) !important;
    border: none !important;
    border-radius: 8px !important;
    font-weight: 600 !important;
    letter-spacing: 0.02em !important;
    transition: opacity 0.2s !important;
}
[data-testid="stButton"] button[kind="primary"]:hover { opacity: 0.88 !important; }
[data-testid="stFormSubmitButton"] button {
    background: linear-gradient(135deg, #4f9cf9, #a855f7) !important;
    border: none !important;
    border-radius: 8px !important;
    font-weight: 600 !important;
}

/* ── Tabs ────────────────────────────────────────────────────── */
[data-testid="stTabs"] [data-baseweb="tab-list"] { border-bottom: 1px solid #2d3348; gap: 1rem; }
[data-testid="stTabs"] [data-baseweb="tab"] { color: #8b9ab5 !important; font-weight: 500; padding: 0.5rem 1rem !important; }
[data-testid="stTabs"] [aria-selected="true"] { color: #4f9cf9 !important; border-bottom: 2px solid #4f9cf9 !important; }

/* ── Inputs ──────────────────────────────────────────────────── */
[data-testid="stTextInput"] input, [data-testid="stTextArea"] textarea {
    background: #1e2435 !important;
    border: 1px solid #2d3348 !important;
    border-radius: 8px !important;
    color: #e0e6f0 !important;
}
[data-testid="stTextInput"] input:focus, [data-testid="stTextArea"] textarea:focus {
    border-color: #4f9cf9 !important;
    box-shadow: 0 0 0 2px rgba(79,156,249,0.2) !important;
}

/* ── Expanders ───────────────────────────────────────────────── */
[data-testid="stExpander"] { background: #1e2435; border: 1px solid #2d3348; border-radius: 10px; }

/* ── Sidebar nav ─────────────────────────────────────────────── */
[data-testid="stRadio"] label { padding: 0.4rem 0.6rem; border-radius: 6px; }
[data-testid="stRadio"] label:hover { background: #252c3e; }

/* ── Page titles ─────────────────────────────────────────────── */
h1 { color: #e0e6f0 !important; font-weight: 700 !important; }
h2, h3 { color: #c5cfe0 !important; }

/* ── Divider ─────────────────────────────────────────────────── */
hr { border-color: #2d3348 !important; }

/* ── Success / Error / Info ──────────────────────────────────── */
[data-testid="stAlert"] { border-radius: 8px !important; border: none !important; }
</style>
""", unsafe_allow_html=True)

# ── Auth ──────────────────────────────────────────────────────────────────────
_cookies = CookieController()

# CookieController returns None on the first render (JS not yet executed).
# Streamlit auto-reruns once the component loads — on that second render the
# real cookie values are available. So we check cookies on EVERY render
# while the user is not yet confirmed logged-in.
if not st.session_state.get("logged_in", False):
    saved_uid  = _cookies.get("wo_user_id")
    saved_user = _cookies.get("wo_username")
    if saved_uid and saved_user:
        st.session_state.logged_in = True
        st.session_state.username  = saved_user
        st.session_state.user_id   = saved_uid
    else:
        st.session_state.setdefault("logged_in", False)
        st.session_state.setdefault("username", "")
        st.session_state.setdefault("user_id", "")

def _auth_api(endpoint: str, payload: dict):
    try:
        r = requests.post(f"{API_URL}{endpoint}", json=payload, timeout=10)
        return r.json(), r.status_code
    except Exception as e:
        return {"detail": str(e)}, 503

# ── Auth page ─────────────────────────────────────────────────────────────────
if not st.session_state.logged_in:
    col_l, col_m, col_r = st.columns([1, 1.4, 1])
    with col_m:
        st.markdown("""
        <div class="auth-hero">
            <h1>💰 WealthOS</h1>
            <p>AI-powered personal financial intelligence · 7 agents · DCF · Risk · Memo</p>
        </div>
        """, unsafe_allow_html=True)

        tab_in, tab_up = st.tabs(["Sign In", "Sign Up"])

        with tab_in:
            with st.form("login_form"):
                li_user = st.text_input("Username", placeholder="Enter your username")
                li_pass = st.text_input("Password", type="password", placeholder="Enter your password")
                li_btn  = st.form_submit_button("Sign In →", use_container_width=True, type="primary")
            if li_btn:
                if not li_user or not li_pass:
                    st.error("Please fill in both fields")
                else:
                    data, code = _auth_api("/auth/login", {"username": li_user, "password": li_pass})
                    if code == 200:
                        st.session_state.logged_in = True
                        st.session_state.username  = data["username"]
                        st.session_state.user_id   = data["user_id"]
                        _cookies.set("wo_user_id",  data["user_id"],  max_age=30*24*3600)
                        _cookies.set("wo_username", data["username"], max_age=30*24*3600)
                        st.rerun()
                    elif code == 503:
                        st.error("⚠️ Backend offline — start the API server first")
                    else:
                        st.error("Invalid username or password")
            st.caption("Default accounts: `admin / wealthos123` · `demo / demo123`")

        with tab_up:
            with st.form("signup_form"):
                su_user  = st.text_input("Username", placeholder="At least 3 characters")
                su_pass  = st.text_input("Password", type="password", placeholder="At least 6 characters")
                su_pass2 = st.text_input("Confirm Password", type="password", placeholder="Repeat your password")
                su_btn   = st.form_submit_button("Create Account →", use_container_width=True, type="primary")
            if su_btn:
                if not su_user or not su_pass or not su_pass2:
                    st.error("All fields are required")
                elif su_pass != su_pass2:
                    st.error("Passwords do not match")
                elif len(su_user.strip()) < 3:
                    st.error("Username must be at least 3 characters")
                elif len(su_pass) < 6:
                    st.error("Password must be at least 6 characters")
                else:
                    data, code = _auth_api("/auth/signup", {"username": su_user.strip(), "password": su_pass})
                    if code == 200:
                        st.success(f"✅ Account created! Welcome, **{data['username']}**. Sign in with the Sign In tab.")
                    elif code == 409:
                        st.error("Username already taken — try a different one")
                    elif code == 503:
                        st.error("⚠️ Backend offline — start the API server first")
                    else:
                        st.error(data.get("detail", "Signup failed"))

    st.stop()

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("""
    <div style="padding: 1rem 0 0.5rem; text-align: center;">
        <div style="font-size: 1.8rem; font-weight: 800; background: linear-gradient(135deg, #4f9cf9, #a855f7);
             -webkit-background-clip: text; -webkit-text-fill-color: transparent;">💰 WealthOS</div>
        <div style="font-size: 0.72rem; color: #5a6478; text-transform: uppercase; letter-spacing: 0.08em; margin-top: 2px;">
            AI Financial Intelligence
        </div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown(f"""
    <div style="background: #252c3e; border-radius: 8px; padding: 0.6rem 0.8rem; margin-bottom: 0.5rem;
         border: 1px solid #2d3348; display: flex; align-items: center; gap: 0.5rem;">
        <div style="width: 28px; height: 28px; border-radius: 50%; background: linear-gradient(135deg, #4f9cf9, #a855f7);
             display: flex; align-items: center; justify-content: center; font-size: 0.75rem; font-weight: 700; flex-shrink: 0;">
            {st.session_state.username[:1].upper()}
        </div>
        <div>
            <div style="font-size: 0.82rem; font-weight: 600; color: #e0e6f0;">{st.session_state.username}</div>
            <div style="font-size: 0.68rem; color: #5a6478;">Signed in</div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    st.divider()
    page = st.radio(
        "Navigate",
        ["📊 Dashboard", "🔍 Analyze", "📈 Portfolio", "📋 Reports", "⚙️ Settings", "ℹ️ About Us", "📬 Contact Us"],
        label_visibility="collapsed",
    )
    st.divider()
    st.markdown("""
    <div style="background: #1a2e1a; border: 1px solid #2d4a2d; border-radius: 8px; padding: 0.5rem 0.75rem;
         font-size: 0.78rem; color: #4caf73;">
        🟢 7 Agents Active
    </div>
    """, unsafe_allow_html=True)
    st.divider()
    st.markdown("**My Documents**")
    uploaded_file = st.file_uploader(
        "Upload EMI receipts, loan statements, salary slips (PDF)",
        type=["pdf"],
        key="personal_doc_upload",
    )
    if uploaded_file is not None:
        with st.spinner("Indexing document..."):
            try:
                resp = requests.post(
                    f"{API_URL}/upload-personal-doc",
                    data={"user_id": st.session_state.user_id},
                    files={"file": (uploaded_file.name, uploaded_file.getvalue(), "application/pdf")},
                    timeout=120,
                )
                if resp.status_code == 200:
                    r = resp.json()
                    st.success(f"Indexed {r['chunks_indexed']} chunks from {r['filename']}")
                else:
                    st.error(f"Upload failed: {resp.text}")
            except Exception as e:
                st.error(f"Upload error: {e}")

    st.markdown("")
    if st.button("Sign Out", use_container_width=True):
        _cookies.remove("wo_user_id")
        _cookies.remove("wo_username")
        st.session_state.logged_in = False
        st.session_state.username  = ""
        st.session_state.user_id   = ""
        st.rerun()

USER_ID = st.session_state.user_id


# ── Helpers ───────────────────────────────────────────────────────────────────

def verdict_emoji(v):
    if not v: return "⚪"
    v = v.lower()
    if "buy" in v: return "🟢"
    if "hold" in v: return "🟡"
    if "avoid" in v or "sell" in v: return "🔴"
    return "⚪"

def _api_get(path: str, timeout: int = 10):
    try:
        r = requests.get(f"{API_URL}{path}", timeout=timeout)
        if r.status_code == 200:
            return r.json(), None
        return None, f"HTTP {r.status_code}"
    except Exception as e:
        return None, str(e)

AGENTS = [
    "Finance Node", "Data Node", "Research Node", "Code Node (DCF)",
    "Risk Node", "Validation Node", "Rebalancing Node", "Writer Node"
]

MOCK_MEMO = """## WealthOS Investment Analysis: Tesla, Inc. (TSLA)

### Executive Summary
**AVOID** — Tesla presents a high-risk profile with DCF intrinsic value of **$20.76**, implying a **94% downside** from current price of $348.95.

### Financial Snapshot
- Revenue: $94,827M | Net Income: $3,794M | P/E: 323.1x | FCF: $6,220M

### Valuation Analysis
DCF intrinsic value of **$20.76** vs current **$348.95** — stock appears significantly overvalued.

### Risk Assessment
Risk score **8/10 (High)** driven by macro sensitivity and high valuation multiples.

### Recommendation
Avoid initiating new positions. Current price leaves no margin of safety.
"""


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: DASHBOARD
# ══════════════════════════════════════════════════════════════════════════════
if page == "📊 Dashboard":
    st.markdown("""
    <div style="margin-bottom: 1.5rem;">
        <h1 style="margin: 0; font-size: 1.8rem;">📊 Dashboard</h1>
        <p style="color: #5a6478; margin: 0.2rem 0 0; font-size: 0.9rem;">Your financial intelligence overview</p>
    </div>
    """, unsafe_allow_html=True)

    history_data, err = _api_get(f"/history/{USER_ID}?limit=20")
    history = history_data.get("history", []) if history_data else []

    # ── Metrics ───────────────────────────────────────────────────────────────
    total_runs = len(history)
    avg_risk   = round(
        sum(h.get("risk_score") or 0 for h in history) / total_runs, 1
    ) if total_runs else 0
    avg_latency = round(
        sum(h.get("latency_ms") or 0 for h in history) / total_runs / 1000, 1
    ) if total_runs else 0
    last_verdict = history[0].get("verdict", "N/A") if history else "N/A"

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Analyses", str(total_runs),        "this account")
    c2.metric("Avg Risk Score", f"{avg_risk}/10",       "lower is safer")
    c3.metric("Last Verdict",   last_verdict or "—",    "most recent")
    c4.metric("Avg Latency",    f"{avg_latency}s",      "per pipeline run")

    st.divider()
    col_chart, col_qa = st.columns([2, 1])

    with col_chart:
        st.subheader("Recent Analyses")
        if history:
            rows = []
            for h in history[:10]:
                ts = h.get("created_at", "")
                date_str = ts[:10] if ts else "—"
                rows.append({
                    "Date":       date_str,
                    "Ticker":     h.get("ticker", ""),
                    "Verdict":    f"{verdict_emoji(h.get('verdict'))} {h.get('verdict') or '—'}",
                    "Risk":       f"{h.get('risk_score') or '—'}/10",
                    "DCF":        f"${h.get('dcf_value'):.2f}" if h.get("dcf_value") else "—",
                    "Latency(s)": round((h.get("latency_ms") or 0) / 1000, 1),
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            if err:
                st.caption(f"⚠️ API error: {err}")
        else:
            st.info("No analyses yet. Go to 🔍 Analyze to run your first one.")
            if err:
                st.caption(f"(Could not reach backend: {err})")

    with col_qa:
        st.subheader("Quick Analyze")
        st.caption("Enter a ticker and run the full AI pipeline instantly.")
        ticker_quick = st.text_input("Ticker", placeholder="e.g. AAPL, NVDA, TSLA")
        if st.button("Run Analysis →", use_container_width=True, type="primary"):
            if ticker_quick:
                st.info(f"Go to 🔍 Analyze page and enter **{ticker_quick.upper()}**")
            else:
                st.warning("Enter a ticker first")
        st.caption("Takes ~60s · 8 agents · DCF + Risk + Memo")


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: ANALYZE
# ══════════════════════════════════════════════════════════════════════════════
elif page == "🔍 Analyze":
    st.title("🔍 Analyze Stock")

    mock = st.toggle("🧪 Mock Mode (no backend needed)", value=False)
    if mock:
        st.info("Mock Mode ON — using fake data, backend not called")

    with st.form("analyze_form"):
        col1, col2 = st.columns(2)
        with col1:
            ticker  = st.text_input("Ticker Symbol *", placeholder="e.g. AAPL")
            amount  = st.number_input("Investment Amount (₹)", min_value=1000, value=50000, step=1000)
            user_id = st.text_input("User ID", value=USER_ID)
        with col2:
            risk    = st.selectbox("Risk Tolerance", ["Conservative", "Moderate", "Aggressive"])
            horizon = st.selectbox("Investment Horizon", ["Short Term (< 1yr)", "Medium Term (1-3yr)", "Long Term (3yr+)"])
        query = st.text_area(
            "Your Question / Context *",
            placeholder="e.g. Should I invest in AAPL right now? I have a moderate risk appetite.",
            help="This is sent as the 'query' to the backend. Be specific!"
        )
        submitted = st.form_submit_button("🚀 Run Analysis", use_container_width=True, type="primary")

    if submitted:
        if not ticker:
            st.error("Please enter a ticker symbol")
            st.stop()
        if not query and not mock:
            st.error("Please enter your question/context")
            st.stop()

        ticker = ticker.upper().strip()

        st.subheader("🤖 Agent Progress")
        progress_bar = st.progress(0, text="Starting agents...")
        agent_cols   = st.columns(4)
        agent_ph     = {}
        for i, agent in enumerate(AGENTS):
            agent_ph[agent] = agent_cols[i % 4].empty()
            agent_ph[agent].info(f"⏳ {agent}")

        result = None

        if mock:
            for i, agent in enumerate(AGENTS):
                time.sleep(0.5)
                agent_ph[agent].success(f"✅ {agent} ({random.randint(2,9)}s)")
                progress_bar.progress((i + 1) / len(AGENTS), text=f"Running {agent}...")
            result = {
                "ticker": ticker, "verdict": "Avoid", "risk_score": 8,
                "dcf_value": 20.76, "memo": MOCK_MEMO,
                "messages": [f"✅ {a}" for a in AGENTS], "error": None,
            }
        else:
            payload = {
                "query":         query or f"Analyze {ticker} stock",
                "ticker":        ticker,
                "user_id":       user_id,
                "invest_amount": float(amount),
            }
            progress_bar.progress(0.1, text="Sending to backend...")
            with st.spinner(f"Running 8 agents for {ticker}... (~60s)"):
                try:
                    response = requests.post(f"{API_URL}/analyze", json=payload, timeout=180)
                    if response.status_code == 200:
                        result = response.json()
                    else:
                        st.error(f"❌ Backend returned {response.status_code}: {response.text}")
                        st.stop()
                except requests.exceptions.ConnectionError:
                    st.error("❌ Cannot connect to backend at `localhost:8000`. Is it running?")
                    st.info("Run: `uvicorn api.main:app --reload` in another terminal, or enable Mock Mode.")
                    st.stop()
                except requests.exceptions.Timeout:
                    st.error("❌ Timed out after 180s.")
                    st.stop()
                except Exception as e:
                    st.error(f"❌ Error: {e}")
                    st.stop()

            for i, agent in enumerate(AGENTS):
                agent_ph[agent].success(f"✅ {agent}")
                progress_bar.progress((i + 1) / len(AGENTS))

        progress_bar.progress(1.0, text="✅ Analysis Complete!")

        # ── Results ──────────────────────────────────────────────────────────
        st.divider()
        st.subheader(f"📄 Results — {ticker}")

        verdict    = result.get("verdict") or "N/A"
        risk_score = result.get("risk_score") or 0
        dcf        = result.get("dcf_value") or 0
        memo       = result.get("memo") or ""
        messages   = result.get("messages", [])
        error      = result.get("error")

        if error:
            st.warning(f"⚠️ Agent error: {error}")

        verdict_color = {"buy": "#1a3a1a", "hold": "#2e2a0e", "avoid": "#3a1a1a"}.get(verdict.lower() if verdict else "", "#1e2435")
        verdict_border = {"buy": "#2d6a2d", "hold": "#6a5c1a", "avoid": "#6a2d2d"}.get(verdict.lower() if verdict else "", "#2d3348")
        verdict_text   = {"buy": "#4caf73", "hold": "#f0c040", "avoid": "#f06060"}.get(verdict.lower() if verdict else "", "#8b9ab5")
        st.markdown(f"""
        <div style="background: {verdict_color}; border: 1px solid {verdict_border}; border-radius: 12px;
             padding: 1.2rem 1.5rem; margin-bottom: 1rem; display: flex; align-items: center; gap: 1rem;">
            <div style="font-size: 2.5rem;">{verdict_emoji(verdict)}</div>
            <div>
                <div style="font-size: 0.72rem; color: #8b9ab5; text-transform: uppercase; letter-spacing: 0.1em;">Verdict for {ticker}</div>
                <div style="font-size: 2rem; font-weight: 800; color: {verdict_text};">{verdict or "N/A"}</div>
            </div>
        </div>
        """, unsafe_allow_html=True)

        m1, m2 = st.columns(2)
        m1.metric("DCF Intrinsic Value", f"${dcf:,.2f}" if dcf else "N/A (no FCF data)")
        m2.metric("Risk Score", f"{risk_score}/10" if risk_score else "N/A")

        col_memo, col_right = st.columns([3, 2])
        with col_memo:
            st.subheader("📝 Investment Memo")
            st.markdown(memo.replace("$", "\\$") if memo else "_No memo generated_")

        with col_right:
            if dcf:
                st.subheader("DCF vs Market Price")
                st.bar_chart(pd.DataFrame({"Value ($)": [dcf]}, index=["DCF Value"]))
            if risk_score and risk_score > 0:
                st.subheader("Risk Level")
                st.progress(min(int(risk_score), 10) / 10)
                label = "🟢 Low" if risk_score <= 3 else "🟡 Medium" if risk_score <= 6 else "🔴 High"
                st.caption(f"{risk_score}/10 — {label} Risk")

        if messages:
            with st.expander("📋 Agent Log"):
                for msg in messages:
                    st.text(msg)


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: PORTFOLIO
# ══════════════════════════════════════════════════════════════════════════════
elif page == "📈 Portfolio":
    st.title("📈 Portfolio")

    portfolio_data, err = _api_get(f"/portfolio/{USER_ID}")
    holdings = portfolio_data.get("holdings", []) if portfolio_data else []

    if not holdings:
        st.info("No portfolio holdings found for this user.")
        if err:
            st.caption(f"(API error: {err})")
    else:
        df = pd.DataFrame(holdings)

        col_left, col_right = st.columns(2)
        with col_left:
            st.subheader("Allocation by Ticker")
            if "ticker" in df.columns and "quantity" in df.columns:
                st.bar_chart(df.set_index("ticker")["quantity"])

        with col_right:
            st.subheader("Avg Buy Price vs Target Weight")
            if "ticker" in df.columns and "avg_buy_price" in df.columns:
                st.bar_chart(df.set_index("ticker")["avg_buy_price"])

        st.divider()
        st.subheader("Holdings Detail")
        st.dataframe(df, use_container_width=True, hide_index=True)


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: REPORTS
# ══════════════════════════════════════════════════════════════════════════════
elif page == "📋 Reports":
    st.title("📋 Reports")

    col_s, col_f = st.columns([3, 1])
    search         = col_s.text_input("Search by ticker", placeholder="e.g. AAPL")
    verdict_filter = col_f.selectbox("Verdict", ["All", "Buy", "Hold", "Avoid"])

    history_data, err = _api_get(f"/history/{USER_ID}?limit=50")
    history = history_data.get("history", []) if history_data else []

    if err and not history:
        st.warning(f"Could not load history from backend: {err}")

    # Filter
    filtered = history
    if search:
        filtered = [h for h in filtered if search.upper() in (h.get("ticker") or "").upper()]
    if verdict_filter != "All":
        filtered = [h for h in filtered if verdict_filter.lower() in (h.get("verdict") or "").lower()]

    # Fetch live from backend
    st.divider()
    col_fetch, col_btn = st.columns([3, 1])
    fetch_ticker = col_fetch.text_input("Fetch live cached analysis from backend", placeholder="e.g. AAPL")
    if col_btn.button("Fetch", use_container_width=True):
        try:
            r = requests.get(f"{API_URL}/state/{fetch_ticker.upper()}", timeout=10)
            if r.status_code == 200:
                data = r.json()
                st.success(f"Found cached analysis for {fetch_ticker.upper()}")
                st.metric("Verdict", data.get("verdict", "N/A"))
                st.metric("Risk Score", data.get("risk_score", "N/A"))
                st.markdown(data.get("memo", ""))
            else:
                st.warning(f"No cached analysis found for {fetch_ticker.upper()}")
        except Exception:
            st.error("Cannot connect to backend")

    st.divider()
    st.subheader(f"Past Analyses ({len(filtered)} found)")

    if not filtered:
        st.info("No analyses match your filters. Run an analysis from the 🔍 Analyze page first.")
    else:
        for h in filtered:
            ticker  = h.get("ticker", "?")
            verdict = h.get("verdict") or "N/A"
            risk    = h.get("risk_score")
            dcf     = h.get("dcf_value")
            ts      = h.get("created_at", "")
            date_str = ts[:10] if ts else "—"
            memo    = h.get("memo") or ""
            label   = (
                f"{verdict_emoji(verdict)} {ticker} | "
                f"Verdict: {verdict} | "
                f"Risk: {risk}/10 | "
                f"DCF: {'${:.2f}'.format(dcf) if dcf else '—'} | "
                f"{date_str}"
            )
            with st.expander(label):
                if memo:
                    st.markdown(memo.replace("$", "\\$"))
                    st.download_button(
                        "📥 Download Memo", data=memo,
                        file_name=f"{ticker}_memo.txt", mime="text/plain",
                        key=f"dl_{h.get('id', ticker)}",
                    )
                else:
                    st.caption("_No memo stored for this run._")


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: SETTINGS
# ══════════════════════════════════════════════════════════════════════════════
elif page == "⚙️ Settings":
    st.title("⚙️ Settings")

    st.subheader("🔌 Backend Status")
    if st.button("Check Backend Connection"):
        try:
            r = requests.get(f"{API_URL}/health", timeout=5)
            if r.status_code == 200:
                data = r.json()
                st.success(f"✅ Connected! Version: {data.get('version')} | Agents: {data.get('agents')}")
            else:
                st.error(f"Backend returned {r.status_code}")
        except Exception:
            st.error("❌ Cannot reach backend at localhost:8000")

    st.divider()
    st.subheader("Financial Profile")
    col1, col2, col3 = st.columns(3)
    col1.number_input("Monthly Income (₹)", value=100000, step=5000)
    col2.number_input("Monthly Expenses (₹)", value=70000, step=5000)
    col3.number_input("Portfolio Value (₹)", value=500000, step=10000)

    st.subheader("Risk Tolerance")
    st.radio("Select your risk profile", [
        "Conservative — Capital preservation, low volatility",
        "Moderate — Balanced growth and safety",
        "Aggressive — Maximum growth, high volatility OK",
    ])

    st.subheader("Notification Preferences")
    st.toggle("Analysis Complete notifications", value=True)
    st.toggle("Market Alerts", value=True)
    st.toggle("Weekly Summary emails", value=False)

    st.subheader("🌅 Morning Briefing")
    user_id_brief = st.text_input("User ID", value=USER_ID)
    if st.button("📨 Trigger Morning Briefing Now", type="primary"):
        try:
            r = requests.post(f"{API_URL}/briefing/send-now", json={"user_id": user_id_brief}, timeout=60)
            if r.status_code == 200:
                data = r.json()
                st.success("✅ Briefing triggered!")
                st.markdown(data.get("briefing", ""))
            else:
                st.error(f"Error: {r.text}")
        except Exception as e:
            st.error(f"❌ {e}")

    st.divider()
    if st.button("💾 Save Settings", type="primary", use_container_width=True):
        st.success("✅ Settings saved!")


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: ABOUT US
# ══════════════════════════════════════════════════════════════════════════════
elif page == "ℹ️ About Us":
    st.title("ℹ️ About WealthOS")

    st.markdown("""
    **WealthOS** is an AI-powered personal financial intelligence platform built to answer one question:

    > *"Should I — specifically me, given my income and risk profile — invest in this right now?"*

    Instead of generic advice, WealthOS combines your personal financial situation with real-time stock data,
    SEC filings, and rigorous financial models to deliver a personalized investment memo in under 90 seconds.
    """)

    st.divider()
    st.subheader("How It Works")

    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown("#### 📥 You Provide")
        st.markdown("""
        - A stock ticker (e.g. AAPL)
        - Your investment question
        - Your financial profile
        """)
    with col2:
        st.markdown("#### 🤖 7 AI Agents Run")
        st.markdown("""
        - Finance Agent — your health score
        - Data Agent — real stock numbers
        - Research Agent — news & filings
        - Risk Agent — 3-way debate
        - Code Agent — DCF & Monte Carlo
        - Rebalancing Agent — portfolio fit
        - Writer Agent — full memo
        """)
    with col3:
        st.markdown("#### 📄 You Get")
        st.markdown("""
        - BUY / HOLD / AVOID verdict
        - DCF intrinsic value
        - Risk score 1–10
        - 7-section investment memo
        - Portfolio rebalancing plan
        """)

    st.divider()
    st.subheader("Technology Stack")
    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown("""
        | Layer | Technology |
        |---|---|
        | Orchestration | LangGraph (StateGraph) |
        | LLM | Groq — Llama 3.3 70B |
        | Vector Search | Qdrant (hybrid dense + BM25) |
        | Embeddings | sentence-transformers (local CPU) |
        | Code Sandbox | E2B cloud containers |
        """)
    with col_b:
        st.markdown("""
        | Layer | Technology |
        |---|---|
        | Tool Protocol | MCP (Model Context Protocol) |
        | Database | PostgreSQL + asyncpg |
        | Cache | Redis |
        | Prompt Optimization | DSPy BootstrapFewShot |
        | Backend | FastAPI |
        """)

    st.divider()
    st.subheader("The Team")
    st.markdown("""
    WealthOS is built as a showcase of modern AI engineering patterns:
    multi-agent orchestration, RAG pipelines, financial modeling, and production-grade observability.

    Built with ❤️ using LangGraph, FastMCP, Qdrant, and Groq.
    """)


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: CONTACT US
# ══════════════════════════════════════════════════════════════════════════════
elif page == "📬 Contact Us":
    st.title("📬 Contact Us")

    col_left, col_right = st.columns([3, 2])

    with col_left:
        st.markdown("Have a question, bug report, or feature request? Fill out the form below.")
        st.divider()

        with st.form("contact_form"):
            name    = st.text_input("Your Name", value=st.session_state.username)
            email   = st.text_input("Email Address", placeholder="you@example.com")
            subject = st.selectbox("Subject", [
                "General Inquiry",
                "Bug Report",
                "Feature Request",
                "Data / Analysis Question",
                "Other",
            ])
            message = st.text_area("Message", placeholder="Describe your question or feedback...", height=150)
            send    = st.form_submit_button("Send Message", use_container_width=True, type="primary")

        if send:
            if not email or "@" not in email:
                st.error("Please enter a valid email address")
            elif not message.strip():
                st.error("Please enter a message")
            else:
                st.success(f"✅ Thanks, **{name}**! Your message has been received. We'll get back to you at {email} shortly.")
                st.balloons()

    with col_right:
        st.subheader("Get In Touch")
        st.markdown("""
        **Response time:** Within 24–48 hours

        **For bugs:** Please include:
        - The ticker you analyzed
        - The error message shown
        - Approximate time of the run

        **For features:** Describe the use case,
        not just the feature — helps us build
        the right thing.
        """)
        st.divider()
        st.subheader("Quick Links")
        st.markdown("""
        - [API Docs](http://localhost:8000/docs)
        - [Health Check](http://localhost:8000/health)
        - [Agent Cards](http://localhost:8000/agents)
        """)
