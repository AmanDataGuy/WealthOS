import streamlit as st
import requests
import time
import random
import json
import pandas as pd

# ── Config ────────────────────────────────────────────────────────────────────
API_URL = "http://localhost:8000"

st.set_page_config(
    page_title="WealthOS — AI Finance",
    page_icon="💰",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 💰 WealthOS")
    st.caption("AI Finance")
    st.divider()
    page = st.radio(
        "Navigate",
        ["📊 Dashboard", "🔍 Analyze", "📈 Portfolio", "📋 Reports", "⚙️ Settings"],
        label_visibility="collapsed",
    )
    st.divider()
    st.success("🟢 7 Agents Active")

# ── Mock Data ─────────────────────────────────────────────────────────────────
MOCK_ANALYSES = [
    {"Ticker": "TSLA", "Verdict": "🔴 Avoid", "Risk": "8/10", "DCF Value": "$20.76",  "Date": "15/01/2025"},
    {"Ticker": "AAPL", "Verdict": "🟡 Hold",  "Risk": "4/10", "DCF Value": "$187.50", "Date": "14/01/2025"},
    {"Ticker": "NVDA", "Verdict": "🟢 Buy",   "Risk": "5/10", "DCF Value": "$620.00", "Date": "13/01/2025"},
    {"Ticker": "MSFT", "Verdict": "🟢 Buy",   "Risk": "3/10", "DCF Value": "$380.00", "Date": "12/01/2025"},
    {"Ticker": "META", "Verdict": "🟡 Hold",  "Risk": "5/10", "DCF Value": "$510.00", "Date": "11/01/2025"},
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

AGENTS = [
    "Finance Node", "Data Node", "Research Node", "Code Node (DCF)",
    "Risk Node", "Validation Node", "Rebalancing Node", "Writer Node"
]

MOCK_PORTFOLIO = [
    {"Asset Class": "Equity", "Current %": 65, "Target %": 60, "Action": "🔴 Sell", "Amount (₹)": "₹5,000"},
    {"Asset Class": "Debt",   "Current %": 15, "Target %": 25, "Action": "🟢 Buy",  "Amount (₹)": "₹10,000"},
    {"Asset Class": "Gold",   "Current %": 12, "Target %": 10, "Action": "🔴 Sell", "Amount (₹)": "₹2,000"},
    {"Asset Class": "Cash",   "Current %": 8,  "Target %": 5,  "Action": "🔴 Sell", "Amount (₹)": "₹3,000"},
]

def verdict_emoji(v):
    if not v: return "⚪"
    v = v.lower()
    if "buy" in v: return "🟢"
    if "hold" in v: return "🟡"
    if "avoid" in v or "sell" in v: return "🔴"
    return "⚪"

# ══════════════════════════════════════════════════════════════════════════════
# PAGE: DASHBOARD
# ══════════════════════════════════════════════════════════════════════════════
if page == "📊 Dashboard":
    st.title("📊 Dashboard")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Portfolio Health", "72 / 100", "+3 this week")
    c2.metric("Monthly Surplus", "₹30,000", "investable ₹20k")
    c3.metric("Analyses Run", "4", "this month")
    c4.metric("Avg Risk Score", "5.0", "medium range")

    st.divider()
    col_chart, col_qa = st.columns([2, 1])

    with col_chart:
        st.subheader("Monthly Surplus Trend")
        months = ["Aug", "Sep", "Oct", "Nov", "Dec", "Jan"]
        surplus = [22000, 24000, 26000, 28000, 30000, 30000]
        df = pd.DataFrame({"Month": months, "Surplus (₹)": surplus})
        st.line_chart(df.set_index("Month"))

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

    st.divider()
    st.subheader("Recent Analyses")
    st.dataframe(pd.DataFrame(MOCK_ANALYSES), use_container_width=True, hide_index=True)

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
            user_id = st.text_input("User ID", value="test-user")
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

        st.markdown(f"## {verdict_emoji(verdict)} Verdict: **{verdict}**")

        m1, m2 = st.columns(2)
        m1.metric("DCF Intrinsic Value", f"${dcf:,.2f}" if dcf else "N/A")
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

    col_left, col_right = st.columns(2)
    with col_left:
        st.subheader("Current Allocation")
        df_alloc = pd.DataFrame(MOCK_PORTFOLIO)[["Asset Class", "Current %"]]
        st.bar_chart(df_alloc.set_index("Asset Class"))

    with col_right:
        st.subheader("Current vs Target")
        df_compare = pd.DataFrame([
            {"Asset Class": r["Asset Class"], "Current %": r["Current %"], "Target %": r["Target %"]}
            for r in MOCK_PORTFOLIO
        ]).set_index("Asset Class")
        st.bar_chart(df_compare)

    st.divider()
    st.subheader("Rebalancing Actions")
    st.dataframe(pd.DataFrame(MOCK_PORTFOLIO), use_container_width=True, hide_index=True)

# ══════════════════════════════════════════════════════════════════════════════
# PAGE: REPORTS
# ══════════════════════════════════════════════════════════════════════════════
elif page == "📋 Reports":
    st.title("📋 Reports")

    col_s, col_f = st.columns([3, 1])
    search         = col_s.text_input("Search by ticker", placeholder="e.g. AAPL")
    verdict_filter = col_f.selectbox("Verdict", ["All", "Buy", "Hold", "Avoid"])

    filtered = MOCK_ANALYSES
    if search:
        filtered = [a for a in filtered if search.upper() in a["Ticker"]]
    if verdict_filter != "All":
        filtered = [a for a in filtered if verdict_filter in a["Verdict"]]

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
    st.subheader("Past Analyses")
    for a in filtered:
        with st.expander(f"{a['Verdict']} {a['Ticker']} | Risk: {a['Risk']} | DCF: {a['DCF Value']} | {a['Date']}"):
            st.markdown(MOCK_MEMO)
            st.download_button(
                "📥 Download Memo", data=MOCK_MEMO,
                file_name=f"{a['Ticker']}_memo.txt", mime="text/plain",
                key=f"dl_{a['Ticker']}"
            )

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
    user_id_brief = st.text_input("User ID", value="test-user")
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