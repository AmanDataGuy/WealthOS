================================================================================
WEALTHOS v2.0
Personal Financial Intelligence Platform
Complete Project Blueprint — Agentic AI Engineer Portfolio
================================================================================

A2A · MCP · AG-UI · LangGraph · CrewAI · Agno · PydanticAI · Smolagents · Mem0
LiteLLM · LlamaIndex · Browser-Use · Firecrawl · Composio · E2B · Redis · Kafka
Qdrant · DSPy · Guardrails AI · Whisper · Modal · LangSmith · AgentOps · W&B Weave
Temporal · FastAPI · Docker · CI/CD

================================================================================
TECH STACK OVERVIEW
================================================================================

Layer                  | Technology                        | Purpose
-----------------------|-----------------------------------|----------------------------------
Frontend               | CopilotKit + AG-UI                | Streaming UI, HITL, live agent
                       |                                   | events, voice input
Orchestrator           | LangGraph + Temporal              | Master state machine, durable
                       |                                   | long-running workflows
LLM Gateway            | LiteLLM                           | Unified interface across all
                       |                                   | providers, fallbacks, cost tracking
Agent Protocol         | A2A (Google)                      | Agent-to-agent communication
Tool Protocol          | MCP + Composio                    | Standardized tool access +
                       |                                   | 300+ pre-built integrations
Agents                 | Agno + PydanticAI + CrewAI        | 7 specialized agents
                       | + Smolagents                      |
Web Intelligence       | Browser-Use + Firecrawl           | Real browser navigation +
                       |                                   | clean web scraping
Document RAG           | LlamaIndex                        | 10-K/10-Q PDF ingestion and
                       |                                   | semantic retrieval
Memory                 | Mem0                              | Long-term user memory,
                       |                                   | past analyses
Vector DB              | Qdrant                            | Dedicated vector search for
                       |                                   | financial documents
Code Execution         | E2B Sandbox                       | Safe Python execution for
                       |                                   | financial modelling
Voice Input            | OpenAI Whisper                    | Speech-to-text for voice queries
Vision / OCR           | Claude 3.5 Sonnet (multimodal)    | Receipt scanning, bank statement
                       |                                   | parsing, image analysis
Reasoning              | DeepSeek R1 / o3-mini             | Chain-of-thought for complex
                       |                                   | investment decisions
Prompt Optimization    | DSPy                              | Automated prompt engineering
                       |                                   | for Writer Agent
Output Validation      | Guardrails AI + PydanticAI        | Runtime schema validation,
                       |                                   | hallucination blocking
Caching + Pub/Sub      | Redis                             | Response caching, real-time
                       |                                   | alerts, rate limiting
Event Streaming        | Kafka                             | Price alerts, proactive finance
                       |                                   | notifications, morning briefing
Observability          | LangSmith + AgentOps + W&B Weave  | AI-layer tracing, agent
                       |                                   | decisions, eval tracking
Embeddings             | Modal (bge-large)                 | Self-hosted embeddings,
                       |                                   | zero API cost
Fine-tuning            | Modal H100 (QLoRA)                | Writer Agent fine-tuned on
                       |                                   | investment memos
Deployment             | FastAPI + Docker + GitHub Actions | Production-grade CI/CD
                       | → AWS                             | with live URL
Infra                  | AWS Bedrock + S3 + App Runner     | Cloud AI, storage,
                       | + CloudWatch                      | deployment, monitoring

================================================================================
SECTION 1 — WHAT WEALTHOS DOES
================================================================================

WealthOS is a production-grade, 7-agent personal financial intelligence platform.
It does two things in one product that no existing portfolio project combines:
it analyzes stocks like a Wall Street research desk, and it analyzes your personal
finances like a CFO analyzes a business — then cross-references both to give you
recommendations that are actually relevant to your specific financial situation.

A user can ask: "Should I invest ₹20,000 in Reliance Industries right now?" —
and WealthOS knows your monthly surplus is ₹18,000, your food spending spiked 35%
last month, you have an outstanding home loan EMI of ₹12,000, and you haven't
maximized your 80C deduction yet. The final memo is not generic financial advice.
It is advice for this person, at this moment in their financial life.

WealthOS supports four input modes — text, voice, image, and PDF — so a user can
type a question, speak it, photograph a receipt, or upload an entire bank statement.
Every input type feeds the same 7-agent pipeline.

--------------------------------------------------------------------------------
1.1 Input System — Four Modes
--------------------------------------------------------------------------------

Input Type | How to Use               | Processed By                   | Use Case
-----------|--------------------------|--------------------------------|---------------------------
Text       | Type in chat interface   | CopilotKit + LangGraph         | Ask any investment question,
           |                          |                                | describe finances manually
Voice      | Microphone button        | OpenAI Whisper STT             | Speak your query hands-free
           | → WebM audio             | transcribed to text            |
Image      | Upload JPEG/PNG          | Claude 3.5 Sonnet              | Scan receipts, expense
           |                          | (multimodal vision)            | screenshots, salary slips,
           |                          |                                | handwritten notes
PDF        | Upload PDF file          | Claude 3.5 Sonnet              | Bank statements, payslips,
           |                          | (multimodal vision,            | investment reports,
           |                          | page-by-page)                  | tax documents

All four input types produce a normalized text query and/or structured financial
data that flows into the same LangGraph pipeline. A user uploading a bank statement
PDF gets exactly the same depth of analysis as one who typed their expenses manually
— the Finance Agent handles both paths.

Input Router Logic:

```python
class InputRouter:
    async def route(self, input: UserInput) -> NormalizedInput:
        if input.type == "text":
            return NormalizedInput(query=input.text)
        elif input.type == "audio":
            transcript = whisper_model.transcribe(input.audio_file)
            return NormalizedInput(query=transcript["text"], source="voice")
        elif input.type == "image":
            extracted = claude_vision.extract(input.image,
                prompt="Extract all financial data: amounts, dates, merchant,
                        category. Return JSON.")
            return NormalizedInput(query=input.query,
                                   financial_data=extracted, source="image")
        elif input.type == "pdf":
            pages = pdf_to_images(input.pdf_file)
            extracted_pages = [
                claude_vision.extract(page,
                    prompt="Extract all financial transactions, amounts, dates.
                            Return JSON.")
                for page in pages
            ]
            merged = merge_page_extractions(extracted_pages)
            return NormalizedInput(query=input.query,
                                   financial_data=merged, source="pdf")
```

--------------------------------------------------------------------------------
1.2 User Journey — End to End
--------------------------------------------------------------------------------

Step | What Happens                                              | Technology
-----|-----------------------------------------------------------|------------------------
1    | User speaks, types, uploads image, or uploads PDF        | Whisper / CopilotKit /
     |                                                           | Claude Vision
2    | Input Router normalizes all input types to structured     | InputRouter + FastAPI
     | data                                                      |
3    | Finance Agent runs first — pulls spending data,           | Finance Agent (Agno)
     | calculates surplus, flags anomalies, computes Financial   |
     | Health Score                                              |
4    | Orchestrator receives PersonalFinanceSnapshot +           | LangGraph + Temporal
     | HealthScore, plans parallel research                      |
5    | Research, Data, Code agents fire in parallel via A2A      | A2A protocol
6    | Research Agent uses Browser-Use to navigate actual        | Browser-Use + Firecrawl
     | analyst pages + Firecrawl for news                       | + MCP
7    | Data Agent fetches numbers via MCP + LlamaIndex queries   | LlamaIndex + Qdrant
     | past 10-K/10-Q filings from Qdrant                       | + MCP
8    | Risk Agent enriches its score with user's personal        | CrewAI +
     | context (EMIs, surplus, goals, Health Score)              | PersonalFinanceSnapshot
9    | Code Agent builds a 5-year DCF model and Monte Carlo      | Smolagents + E2B
     | simulation in E2B sandbox                                 |
10   | Rebalancing Agent checks current portfolio holdings,      | Agno + portfolio-mcp
     | computes drift from target allocation, suggests rebalance |
11   | AG-UI streams live progress to user — tool calls,         | AG-UI event stream
     | agent status, partial results                             |
12   | HITL checkpoint: user reviews findings, DCF output, risk  | AG-UI pause/resume
     | score, rebalance suggestion — then approves               |
13   | Writer Agent (DSPy-optimized prompts) streams final       | LangGraph + AG-UI
     | personalized investment memo                              |
14   | User can export memo as formatted PDF pushed to Google    | PDF export + Composio
     | Drive via Composio                                        |
15   | Kafka publishes alert: set price target → get notified    | Kafka + Redis
     | via Gmail or WhatsApp                                     | + Composio
16   | Mem0 stores analysis + personal finance context in        | Mem0 + S3 + Postgres
     | long-term memory                                          |
17   | Full trace: LangSmith + AgentOps + W&B Weave              | Observability layer

--------------------------------------------------------------------------------
1.3 What Each Agent Does
--------------------------------------------------------------------------------

AGENT 1 — Finance Agent (Agno) — runs first, always
----------------------------------------------------
Job: Build a complete picture of the user's personal financial health before any
investment analysis begins. Also computes the Financial Health Score.

- Bank transaction MCP tool → parse spending by category for last 3 months
- OCR tool (Claude 3.5 Vision) → scan uploaded receipts and bank PDF statements
  (handles both image and PDF input types)
- Anomaly Detector → z-score on 3-month rolling average per category,
  flags outliers > 1.5σ
- Goal Tracker → checks progress toward user-set milestones
  (house, car, retirement, emergency fund)
- Subscription Auditor → identifies recurring charges, flags ones with
  no recent usage
- Debt Optimizer → calculates optimal repayment order (avalanche or snowball)
  for active EMIs
- Tax Optimizer → identifies unutilized deductions (80C, 80D, HRA)
  given current salary
- Insurance Gap Analyzer → flags if life/health cover is below recommended
  multiple of annual income
- Peer Benchmarking → compares user's category spending to anonymized peer
  cohort by income bracket
- Financial Health Score → single 0-100 score summarizing overall financial
  health (see 1.4)
- Produces: PersonalFinanceSnapshot — fully typed Pydantic schema

AGENT 2 — Research Agent (Agno)
--------------------------------
Job: Find everything happening around the company using real browser navigation,
not just API calls.

- Browser-Use → navigates actual NSE/BSE pages, Moneycontrol, analyst report
  pages, LinkedIn CEO profiles
- Firecrawl MCP tool → clean extraction from news sites, Reddit finance threads,
  Seeking Alpha
- News MCP tool → sentiment from last 7 days of headlines, classified by Groq LLM
- Web search MCP tool → macro tailwinds, regulatory updates, sector trends
- Produces: ResearchReport with citations, sentiment score, analyst consensus,
  key themes

AGENT 3 — Data Agent (PydanticAI + AWS Bedrock)
-------------------------------------------------
Job: Pull hard financial numbers with zero hallucination. Validate every number
against a schema. Query past filings semantically via LlamaIndex + Qdrant.

- yfinance MCP tool → stock price, P/E, EPS, 52-week range, volume
- SEC EDGAR MCP tool → latest 10-K, 10-Q filings
- LlamaIndex query engine → semantic search over indexed filing PDFs in Qdrant
- Calculator MCP tool → CAGR, debt-to-equity, EPS growth, WACC
- Produces: FinancialSnapshot — typed, validated, no hallucinated numbers

AGENT 4 — Risk Agent (CrewAI + DeepSeek R1)
--------------------------------------------
Job: Score risk — enriched with the user's actual personal financial context
from the Finance Agent.

- Macro MCP tool → interest rate environment, sector rotation, inflation
- Portfolio MCP tool → correlation to user's existing holdings
- Competitor MCP tool → relative performance vs sector peers
- PersonalFinanceSnapshot injection → adjusts risk score based on user's
  EMI burden, surplus, goal timeline, Health Score
- Uses DeepSeek R1 via LiteLLM for chain-of-thought multi-factor reasoning
- Produces: RiskReport with score 1-10, 3 key factors with evidence,
  personal risk flag

AGENT 5 — Code Agent (Smolagents + E2B)
-----------------------------------------
Job: Generate and execute real Python financial models in a sandboxed environment.

- Input: FinancialSnapshot from Data Agent + ResearchReport macro assumptions
- E2B sandbox → safe Python execution (pandas, numpy, matplotlib)
- Builds: 5-year DCF model using analyst revenue estimates and WACC assumptions
- Builds: Monte Carlo simulation (10,000 paths) for probabilistic price range
- Builds: Sensitivity table — shows how verdict changes at different growth
  rate assumptions
- Output: DCF intrinsic value estimate + probability distribution +
  sensitivity chart image

AGENT 6 — Rebalancing Agent (Agno)
------------------------------------
Job: Look at the user's actual current portfolio holdings, compare against their
target allocation, and produce specific buy/sell suggestions that incorporate
the new investment decision.

- portfolio-mcp get_holdings(user_id) → current stocks, quantities,
  current value, sector exposure
- yfinance-mcp → get current prices for all holdings
- Compute current weights per sector/asset class
- Compare against user's target allocation (e.g. {equity: 60, debt: 30, gold: 10})
- Compute drift: sectors above target flagged for trim; below flagged as candidates
- Incorporate the new investment being considered:
  "If you buy Reliance at ₹20,000, your energy sector weight goes from 18%
   to 26% vs your 20% target"
- Suggest specific rebalance actions with rupee amounts
- Run rebalancing calculation in E2B sandbox for accuracy
- Produces: RebalanceSuggestion — specific actions, amounts, resulting allocation

```python
def suggest_rebalance(
    holdings: list[Holding],
    target_allocation: dict,
    new_investment: Investment,
    investable_amount: float
) -> RebalanceSuggestion:
    current_weights = compute_current_weights(holdings)
    projected_weights = compute_projected_weights(holdings, new_investment)
    drift = {k: projected_weights[k] - target_allocation[k]
             for k in target_allocation}
    suggestions = []
    for sector, d in drift.items():
        if d > 0.05:   # more than 5% overweight
            suggestions.append(TrimSuggestion(sector=sector,
                amount=d * total_portfolio_value))
        elif d < -0.05:  # more than 5% underweight
            suggestions.append(BuySuggestion(sector=sector,
                amount=abs(d) * total_portfolio_value))
    return RebalanceSuggestion(
        current_allocation=current_weights,
        projected_allocation=projected_weights,
        target_allocation=target_allocation,
        suggestions=suggestions,
        summary=f"Adding Reliance increases energy from "
                f"{current_weights['energy']:.0%} to "
                f"{projected_weights['energy']:.0%} vs "
                f"{target_allocation['energy']:.0%} target"
    )
```

AGENT 7 — Writer Agent (LangGraph node + DSPy-optimized prompts)
-----------------------------------------------------------------
Job: Synthesize all 6 agents' outputs into a personalized investment memo.
Prompts are DSPy-compiled, not hand-written.

- Input: Finance + Research + Data + Risk + Code + Rebalancing Agent outputs
- DSPy-compiled prompt → automatically optimized for memo quality score > 4.0/5.0
- Streams memo section by section via AG-UI:
    • Executive Summary (with Financial Health Score + personal context)
    • Financial Snapshot (real numbers from Data Agent)
    • Market Sentiment (citations from Research Agent)
    • Code-Based Valuation (DCF + Monte Carlo from Code Agent)
    • Risk Assessment (scored + personalized from Risk Agent)
    • Portfolio Rebalancing (specific actions from Rebalancing Agent)
    • Personal Finance Fit (anomalies, goals, tax, EMI impact from Finance Agent)
    • Final Verdict: Buy / Hold / Avoid — for this user specifically
- After memo: one-click PDF export → Composio pushes to Google Drive

--------------------------------------------------------------------------------
1.4 Financial Health Score
--------------------------------------------------------------------------------

A single 0-100 score computed by the Finance Agent summarizing the user's overall
financial health. Displayed prominently on the dashboard. Every investment memo
includes it.

```python
def compute_health_score(snapshot: PersonalFinanceSnapshot) -> HealthScore:
    scores = {}

    # Surplus ratio (30 points)
    surplus_ratio = snapshot.monthly_surplus / snapshot.monthly_income
    scores["surplus"] = min(30, surplus_ratio * 100)

    # Debt burden (25 points) — lower is better
    scores["debt"] = max(0, 25 - (snapshot.debt_burden_ratio * 50))

    # Goal progress (20 points)
    avg_goal_progress = mean([g.current / g.target for g in snapshot.goals])
    scores["goals"] = avg_goal_progress * 20

    # Tax utilization (15 points)
    tax_utilized = 1 - (snapshot.unutilized_80c / 150000)  # 1.5L is max 80C
    scores["tax"] = tax_utilized * 15

    # Insurance coverage (10 points)
    scores["insurance"] = 0 if snapshot.insurance_gap_flag else 10

    total = sum(scores.values())
    grade = ("Excellent" if total > 80 else
             "Good"      if total > 65 else
             "Fair"      if total >= 50 else "Needs Attention")

    return HealthScore(
        total=round(total),
        breakdown=scores,
        grade=grade,
        top_issue=min(scores, key=scores.get)  # worst performing dimension
    )
```

Score display on dashboard:
  80-100  →  Excellent      (green)
  65-79   →  Good           (blue)
  50-64   →  Fair           (amber)
  Below 50 → Needs Attention (red)

--------------------------------------------------------------------------------
1.5 Morning Briefing (Proactive Agent)
--------------------------------------------------------------------------------

Every morning at 8:00 AM, without the user asking, WealthOS proactively checks
the user's watchlist and finances and sends a briefing.
Powered by Temporal scheduled workflow + Kafka + Composio.

What it checks:
- Overnight price movements on watchlist stocks (> 3% move → flag)
- Any new anomalies in the user's spending since yesterday
- Any price alerts that are close to triggering (within 2%)
- Macro news that affects the user's holdings (interest rate announcements,
  sector news)

Output: 5-line WhatsApp or Gmail message:

  WealthOS Morning Briefing — 8:00 AM
  📉 Reliance: -4.2% overnight (Q3 miss, analyst downgrade)
  💰 Your food spend is 28% above average this month
  🔔 TCS is within 1.8% of your ₹3,800 alert
  🌐 RBI rate decision today at 2 PM — affects your HDFC Bank holding
  💡 You have ₹42,000 in unutilized 80C deduction with 3 months left

```python
# wealthOS/workflows/morning_briefing.py
from temporalio import workflow, activity
from datetime import timedelta

@workflow.defn
class MorningBriefingWorkflow:
    @workflow.run
    async def run(self, user_id: str):
        watchlist_alerts = await workflow.execute_activity(
            check_watchlist_movements, user_id,
            start_to_close_timeout=timedelta(seconds=30)
        )
        finance_alerts = await workflow.execute_activity(
            check_finance_anomalies, user_id,
            start_to_close_timeout=timedelta(seconds=20)
        )
        price_alerts = await workflow.execute_activity(
            check_near_alerts, user_id,
            start_to_close_timeout=timedelta(seconds=10)
        )
        macro_news = await workflow.execute_activity(
            check_macro_news, user_id,
            start_to_close_timeout=timedelta(seconds=20)
        )
        briefing = compile_briefing(
            watchlist_alerts, finance_alerts, price_alerts, macro_news
        )
        await workflow.execute_activity(
            send_briefing, user_id, briefing,
            start_to_close_timeout=timedelta(seconds=15)
        )

# Register as daily cron
client.start_workflow(
    MorningBriefingWorkflow.run,
    user_id,
    task_queue="briefing-queue",
    id=f"morning-briefing-{user_id}",
    cron_schedule="0 8 * * *"  # 8 AM daily
)
```

--------------------------------------------------------------------------------
1.6 Multi-Stock Comparison Mode
--------------------------------------------------------------------------------

User can ask: "Compare Reliance vs TCS vs Infosys" → WealthOS fires 3 parallel
analysis pipelines and Writer Agent produces a side-by-side comparison memo.

The architecture already supports this because agents are parallel by design.
Multi-stock mode adds:
- Parallel A2A calls per ticker
- Writer Agent receives 3× ResearchReports + 3× FinancialSnapshots
- Output: comparative table + recommendation ranked by fit to this user's portfolio

Implementation note: share the PersonalFinanceSnapshot and RebalanceSuggestion
across all 3 pipelines — they share the same user context; only the
stock-specific research differs.

--------------------------------------------------------------------------------
1.7 PDF Export
--------------------------------------------------------------------------------

After the Writer Agent streams the final memo, the user gets a "Download as PDF"
button.

```python
# wealthOS/api/export.py
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet

async def export_memo_as_pdf(memo: str, ticker: str, user_id: str) -> bytes:
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    styles = getSampleStyleSheet()
    story = []

    story.append(Paragraph(f"WealthOS Investment Analysis: {ticker}",
                            styles["Title"]))
    story.append(Paragraph(f"Generated: {datetime.now().strftime('%B %d, %Y')}",
                            styles["Normal"]))
    story.append(Spacer(1, 12))

    for section in parse_memo_sections(memo):
        story.append(Paragraph(section.title, styles["Heading2"]))
        story.append(Paragraph(section.content, styles["Normal"]))
        story.append(Spacer(1, 8))

    doc.build(story)
    pdf_bytes = buffer.getvalue()

    # Push to Google Drive via Composio
    toolset.execute_action(
        action=Action.GOOGLEDRIVE_UPLOAD_FILE,
        params={
            "file_name": f"WealthOS_{ticker}_{date.today()}.pdf",
            "file_content": base64.b64encode(pdf_bytes).decode(),
            "mime_type": "application/pdf"
        }
    )
    return pdf_bytes
```

================================================================================
SECTION 2 — SYSTEM ARCHITECTURE
================================================================================

--------------------------------------------------------------------------------
2.1 High-Level Flow
--------------------------------------------------------------------------------

[Text] [Voice → Whisper STT] [Image → Claude Vision] [PDF → Claude Vision]
                              |
                        Input Router
                              |
                              ▼
         LiteLLM Gateway (Groq / Bedrock / DeepSeek R1 / Ollama / o3-mini)
                              |
                              ▼
         LangGraph Orchestrator + Temporal (durable workflow)
                              |
        ┌─────────────────────┼──────────────────────────────────┐
        ▼                     ▼                                  ▼
  Finance Agent (Agno)  Research Agent              Data Agent (PydanticAI
  OCR receipts          (Browser-Use + Firecrawl)   + LlamaIndex + Qdrant)
  Anomaly detect        Live web nav                yfinance, SEC filings
  Health Score          News sentiment
  Tax/goals/debt        Analyst reports
        |                     |                                  |
        └─────────────────────┴──────────────────────────────────┘
                              |
              ┌───────────────┴─────────────────┐
              ▼                                 ▼
        Risk Agent                        Code Agent
        (CrewAI + DeepSeek R1)            (Smolagents + E2B)
        Personal context                  DCF model
        Risk score                        Monte Carlo
                                          Sensitivity
              └───────────────┬─────────────────┘
                              |
                              ▼
                    Rebalancing Agent (Agno)
                    Current holdings
                    Drift from target
                    Specific suggestions
                              |
                    [HITL Checkpoint — AG-UI]
                              |
                              ▼
                    Writer Agent (LangGraph + DSPy)
                    Personalized memo (streamed)
                    PDF export → Google Drive
                              |
                 ┌────────────┼────────────────┐
                 ▼            ▼                ▼
           Mem0 memory   Kafka price      WhatsApp/Gmail
                         alerts           alerts

--------------------------------------------------------------------------------
2.2 LangGraph State Schema
--------------------------------------------------------------------------------

```python
class WealthOSState(TypedDict):
    # Input
    query:                    str
    voice_input:              bool
    image_input:              Optional[str]   # base64 image if uploaded
    pdf_input:                Optional[bytes] # raw PDF bytes if uploaded
    input_source:             str             # "text"|"voice"|"image"|"pdf"
    tickers:                  list[str]       # one or multiple (comparison mode)
    comparison_mode:          bool            # True if multi-stock comparison

    # Agent outputs
    personal_finance:         PersonalFinanceSnapshot
    health_score:             HealthScore
    research_output:          ResearchReport
    financial_data:           FinancialSnapshot
    risk_assessment:          RiskReport
    code_output:              CodeAgentOutput
    rebalance_suggestion:     RebalanceSuggestion

    # Control flow
    user_approved:            bool
    final_memo:               str
    pdf_exported:             bool

    # Context
    memory_context:           list[dict]
    messages:                 list[BaseMessage]
    price_alert_set:          bool
    morning_briefing_scheduled: bool
    llm_backend:              str
```

--------------------------------------------------------------------------------
2.3 PersonalFinanceSnapshot Schema
--------------------------------------------------------------------------------

```python
class PersonalFinanceSnapshot(BaseModel):
    user_id:                      str
    analysis_date:                str
    monthly_income:               float
    monthly_surplus:              float
    risk_capacity:                Literal["low", "medium", "high"]
    health_score:                 HealthScore

    # Spending
    spending_by_category:         dict[str, float]
    anomalies:                    list[AnomalyFlag]

    # Debt
    active_emis:                  list[EMI]
    debt_burden_ratio:            float
    recommended_repayment_order:  list[str]

    # Goals
    goals:                        list[FinancialGoal]
    monthly_needed_per_goal:      dict[str, float]

    # Tax (India-specific)
    tax_bracket:                  str
    unutilized_80c:               float
    unutilized_hra:               float
    estimated_tax_saving:         float

    # Insurance
    insurance_gap_flag:           bool
    recommended_cover:            float

    # Subscriptions
    recurring_charges:            list[Subscription]
    potentially_unused:           list[Subscription]

    # Peers
    peer_benchmarks:              dict[str, PeerComparison]

    # Investment readiness
    investable_monthly:           float
    recommended_investment_split: dict
```

--------------------------------------------------------------------------------
2.4 MCP Server Specifications
--------------------------------------------------------------------------------

MCP Server       | Tools Exposed                                      | Data Source
-----------------|----------------------------------------------------|-----------------
yfinance-mcp     | get_price, get_financials, get_history, get_info   | Yahoo Finance
sec-edgar-mcp    | get_10k, get_10q, get_filings_list                 | SEC EDGAR API
news-mcp         | search_news, get_sentiment, get_headlines           | NewsAPI / GNews
calculator-mcp   | calc_cagr, calc_pe, calc_dcf_inputs, calc_wacc     | Pure Python
finance-mcp      | parse_transactions, detect_anomalies,              | Postgres
                 | calc_surplus, audit_subscriptions                  |
portfolio-mcp    | get_holdings, get_correlation,                     | Postgres
                 | get_exposure, get_sector_weight                    |
macro-mcp        | get_interest_rates, get_inflation,                 | FRED API
                 | get_sector_performance                             |
tax-mcp          | calc_80c_remaining, calc_hra,                      | Pure Python
                 | estimate_tax, get_slab                             |
competitor-mcp   | get_peers, compare_metrics, get_market_share       | yfinance + custom
alert-mcp        | set_price_alert, cancel_alert, list_alerts         | Redis + Kafka
qdrant-mcp       | embed_and_store, similarity_search, find_related   | Qdrant + Modal

--------------------------------------------------------------------------------
2.5 LiteLLM Routing Configuration
--------------------------------------------------------------------------------

```python
litellm_config = {
    "model_list": [
        # Fast, cheap — simple tasks
        {"model_name": "groq-llama",
         "litellm_params": {"model": "groq/llama-3.1-70b-versatile"}},

        # Cloud-grade — Data Agent
        {"model_name": "bedrock-claude",
         "litellm_params": {
             "model": "bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0"}},

        # Reasoning — Risk Agent complex decisions
        {"model_name": "deepseek-r1",
         "litellm_params": {"model": "deepseek/deepseek-r1"}},

        # Reasoning alternative
        {"model_name": "o3-mini",
         "litellm_params": {"model": "openai/o3-mini"}},

        # Local / private — sensitive personal finance data
        {"model_name": "ollama-llama",
         "litellm_params": {"model": "ollama/llama3.2",
                            "api_base": "http://ollama:11434"}},

        # Vision — OCR receipts and bank statements
        {"model_name": "vision-claude",
         "litellm_params": {
             "model": "bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0"}},
    ],
    "router_settings": {
        "routing_strategy": "latency-based-routing",
        "fallbacks": [{"groq-llama": ["bedrock-claude"]}],
        "context_window_fallbacks": [{"groq-llama": ["bedrock-claude"]}]
    }
}
```

================================================================================
SECTION 3 — PHASED BUILD PLAN
================================================================================

Total estimated timeline: 11 phases. Each phase is independently demoable.
Apply after Phase 3 is complete.

================================================================================

================================================================================
PHASE 0 — FOUNDATION & SETUP
Days 1-3
Goal: Environment running, all credentials working, all 4 input types working,
      database live, all external tools verified
================================================================================

--------------------------------------------------------------------------------
Step 0.1 — Project Structure
--------------------------------------------------------------------------------

Create GitHub repo: wealthOS

wealthOS/
├── agents/
│   ├── finance_agent.py
│   ├── research_agent.py
│   ├── data_agent.py
│   ├── risk_agent.py
│   ├── code_agent.py
│   ├── rebalancing_agent.py
│   └── writer_agent.py
├── mcp_servers/
│   ├── market_server.py
│   ├── sec_edgar_server.py
│   ├── news_server.py
│   ├── finance_server.py
│   ├── calculator_server.py
│   ├── tax_server.py
│   └── portfolio_server.py
├── graph/
│   ├── state.py
│   └── nodes.py
├── rag/
│   ├── indexer.py
│   └── query_engine.py
├── input/
│   ├── router.py
│   ├── whisper_handler.py
│   ├── vision_handler.py
│   └── pdf_processor.py
├── memory/
│   └── mem0_client.py
├── gateway/
│   └── litellm_config.yaml
├── workflows/
│   ├── temporal_workflows.py
│   └── morning_briefing.py
├── api/
│   ├── main.py
│   └── export.py
├── health/
│   └── score.py
├── frontend/
├── eval/
│   ├── writer_golden_dataset.json
│   └── compiled_writer.json
├── modal_apps/
│   ├── embedder.py
│   ├── finetune_writer.py
│   └── writer_inference.py
└── .github/workflows/
    └── ci.yml

--------------------------------------------------------------------------------
Step 0.2 — Environment Setup
--------------------------------------------------------------------------------

pip install langgraph langchain agno pydantic-ai crewai smolagents mem0ai
pip install copilotkit fastapi uvicorn langsmith agentops python-dotenv
pip install yfinance httpx asyncpg sqlalchemy[asyncio] boto3
pip install python-jose slowapi litellm llama-index llama-index-vector-stores-qdrant
pip install qdrant-client guardrails-ai dspy-ai modal e2b openai
pip install browser-use firecrawl-py composio-core redis kafka-python
pip install openai-whisper torch torchvision torchaudio wandb weave
pip install temporal-sdk python-multipart Pillow reportlab scipy

.env file:
  OPENAI_API_KEY=
  GROQ_API_KEY=
  ANTHROPIC_API_KEY=
  DEEPSEEK_API_KEY=
  LANGCHAIN_API_KEY=
  AGENTOPS_API_KEY=
  MEM0_API_KEY=
  FIRECRAWL_API_KEY=
  COMPOSIO_API_KEY=
  E2B_API_KEY=
  WANDB_API_KEY=
  WEALTHOS_DB_URL=postgresql+asyncpg://postgres:yourpassword@localhost:5432/wealthos
  QDRANT_URL=http://localhost:6333
  REDIS_URL=redis://localhost:6379
  KAFKA_BOOTSTRAP_SERVERS=localhost:9092
  AWS_ACCESS_KEY_ID=
  AWS_SECRET_ACCESS_KEY=
  AWS_REGION=us-east-1
  JWT_SECRET_KEY=
  MODAL_TOKEN_ID=
  MODAL_TOKEN_SECRET=
  TEMPORAL_HOST=localhost:7233
  NEWSAPI_KEY=
  SEC_USER_AGENT=WealthOS yourname@email.com

--------------------------------------------------------------------------------
Step 0.3 — Infrastructure Setup (Local, No Docker Compose)
--------------------------------------------------------------------------------

PostgreSQL runs natively in WSL. Redis and Qdrant run as standalone Docker
containers — no compose file required. Kafka and Temporal are added later
when their phases begin.

  Service     | How It Runs                                      | Port
  ------------|--------------------------------------------------|-------
  PostgreSQL  | Native install in WSL (postgresql service)       | 5432
  Redis       | docker run -d --name redis redis:7-alpine        | 6379
  Qdrant      | docker run -d --name qdrant qdrant/qdrant        | 6333
  Kafka       | Added in Phase 6 (price alerts)                  | 9092
  Temporal    | Added in Phase 4 (workflows)                     | 7233
  Ollama      | Added in Phase 3 (local LLM)                     | 11434

Start Redis:
  docker run -d --name redis -p 6379:6379 redis:7-alpine

Start Qdrant:
  docker run -d --name qdrant -p 6333:6333 qdrant/qdrant

Start PostgreSQL (WSL):
  sudo service postgresql start

Verify all three are up before writing any agent code.

--------------------------------------------------------------------------------
Step 0.4 — Database Setup
--------------------------------------------------------------------------------

Connect to PostgreSQL and run:

  sudo -u postgres psql
  CREATE DATABASE wealthos;
  \c wealthos
  CREATE EXTENSION IF NOT EXISTS vector;
  CREATE EXTENSION IF NOT EXISTS pgcrypto;

Create all tables:

```sql
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email TEXT UNIQUE NOT NULL,
    name TEXT,
    phone TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE analyses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    ticker TEXT NOT NULL,
    verdict TEXT,
    memo TEXT,
    health_score INT,
    risk_score INT,
    dcf_value FLOAT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE personal_finance_snapshots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    monthly_income FLOAT,
    monthly_surplus FLOAT,
    debt_burden_ratio FLOAT,
    health_score INT,
    health_grade TEXT,
    snapshot JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE portfolio_holdings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id),
    ticker TEXT NOT NULL,
    quantity FLOAT NOT NULL,
    avg_buy_price FLOAT NOT NULL,
    sector TEXT,
    asset_type VARCHAR(20) DEFAULT 'equity',
    added_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE price_alerts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    ticker TEXT NOT NULL,
    target_price FLOAT NOT NULL,
    direction TEXT CHECK (direction IN ('above', 'below')),
    is_active BOOL DEFAULT TRUE,
    triggered_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE token_budgets (
    user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    tokens_used INT DEFAULT 0,
    tokens_limit INT DEFAULT 500000,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE transactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    date DATE NOT NULL,
    description TEXT,
    amount FLOAT NOT NULL,
    type TEXT CHECK (type IN ('credit', 'debit')),
    category TEXT,
    source TEXT DEFAULT 'manual',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE financial_goals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    target_amount FLOAT NOT NULL,
    current_amount FLOAT DEFAULT 0,
    deadline_date DATE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE subscriptions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    amount FLOAT NOT NULL,
    frequency TEXT DEFAULT 'monthly',
    last_charged DATE,
    is_flagged BOOL DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

Add indexes for performance:

```sql
CREATE INDEX idx_analyses_user_id ON analyses(user_id);
CREATE INDEX idx_analyses_ticker ON analyses(ticker);
CREATE INDEX idx_holdings_user_id ON portfolio_holdings(user_id);
CREATE INDEX idx_alerts_user_active ON price_alerts(user_id, is_active);
CREATE INDEX idx_transactions_user_date ON transactions(user_id, date);
CREATE INDEX idx_snapshots_user_id ON personal_finance_snapshots(user_id);
```

--------------------------------------------------------------------------------
Step 0.5 — LiteLLM Gateway Setup
--------------------------------------------------------------------------------

pip install litellm[proxy]
litellm --config gateway/litellm_config.yaml --port 4000

Test all 5 LLM backends through the gateway before writing agent code.
This is the single gateway all agents use — no agent calls OpenAI/Groq/Bedrock
directly.

--------------------------------------------------------------------------------
Step 0.6 — Input Router — All 4 Input Types
--------------------------------------------------------------------------------

Build input/router.py, input/whisper_handler.py, input/vision_handler.py,
input/pdf_processor.py.

Test each path:
  text  → pass-through to NormalizedInput
  voice → Whisper transcription → NormalizedInput
  image → Claude 3.5 vision extraction → NormalizedInput with financial_data
  PDF   → page-by-page Claude vision → merged NormalizedInput with financial_data

All 4 paths must return the same NormalizedInput schema before Phase 1 begins.

--------------------------------------------------------------------------------
Step 0.7 — Verify All External Tools
--------------------------------------------------------------------------------

- yfinance        → fetch RELIANCE.NS price and P/E
- Firecrawl       → scrape one Moneycontrol article
- Browser-Use     → navigate to NSE website, extract price
- E2B             → run a simple Python script in sandbox, verify output
- Qdrant          → store a test vector, retrieve it
- Redis           → set and get a key
- Whisper         → transcribe a 10-second audio clip
- AWS Bedrock     → call Claude 3.5 Sonnet via boto3
- DeepSeek R1     → simple reasoning call via LiteLLM
- Claude Vision   → extract data from a sample receipt image

PHASE 0 DELIVERABLE:
  PostgreSQL, Redis, and Qdrant all running and reachable
  All 4 input types processed correctly end-to-end
  All external tools returning valid responses

================================================================================
PHASE 1 — MCP SERVERS
Days 4-12
Goal: All 7 MCP servers running with validated outputs, Redis caching,
      pytest coverage, CI running
================================================================================

NOTE ON SERVER COUNT:
  The original plan specified 11 MCP servers. After review, 4 were consolidated
  or folded into existing servers to reduce infrastructure complexity:

  Dropped / Merged:
  - macro-mcp        → folded into market_server.py (yfinance has proxies
                        for Nifty, Sensex, currency rates, sector indices)
  - competitor-mcp   → folded into market_server.py (peer comparison is
                        yfinance calls with sector logic)
  - alert-mcp        → price_alerts table exists in Postgres; alert logic
                        runs via APScheduler added in Phase 6, not a
                        separate MCP server
  - qdrant-mcp       → replaced by pgvector inside existing PostgreSQL;
                        no separate Qdrant MCP server needed

  Final count: 7 MCP servers

--------------------------------------------------------------------------------
Step 1.1 — market_server.py  (replaces yfinance-mcp + macro-mcp + competitor-mcp)
--------------------------------------------------------------------------------

Tools:
  get_price(ticker)
  get_financials(ticker)
  get_history(ticker, period)
  get_info(ticker)
  get_recommendations(ticker)
  get_market_overview()               → Nifty 50, Sensex, Bank Nifty, S&P 500,
                                        Nasdaq, Dow
  get_currency_rates()                → USD/INR, EUR/INR, GBP/INR, Gold, Crude
  get_sector_performance()            → 8 Nifty sectoral indices sorted by day
                                        change
  get_competitors(ticker)             → sector peer list via hardcoded map for
                                        major Indian stocks
  compare_stocks(tickers)             → side-by-side P/E, ROE, margins, market
                                        cap + best-in-class picks

- Redis caching: 5 min TTL for price / 1 hour TTL for financials and macro
- All outputs validated with Pydantic schemas
- Tech: yfinance, FastMCP, Redis, Pydantic

--------------------------------------------------------------------------------
Step 1.2 — sec_edgar_server.py
--------------------------------------------------------------------------------

Tools:
  get_10k(ticker)
  get_10q(ticker)
  get_filings_list(ticker, count)

- Looks up ticker → CIK via SEC's public company_tickers.json
- Returns filing date, accession number, and direct document URL
- Document URL fed into LlamaIndex RAG pipeline in Phase 2
- No API key required — SEC EDGAR is a free public US government API
- Requires SEC_USER_AGENT header in .env: "WealthOS yourname@email.com"
- Redis caching: 6 hour TTL — filings don't change intraday
- Tech: SEC EDGAR API (free), httpx, FastMCP, Redis, Pydantic

--------------------------------------------------------------------------------
Step 1.3 — news_server.py
--------------------------------------------------------------------------------

Tools:
  search_news(query, days)
  get_sentiment(ticker)
  get_headlines(ticker, count)

- load_dotenv() must be called before any os.getenv() call in the file
- Sentiment scorer: keyword-based for Phase 1; upgraded to Groq LLM
  classification per headline in Phase 3 when Research Agent is wired
- Firecrawl as fallback: if NewsAPI quota hit → scrape Google News
- Redis caching: 30 min TTL
- Tech: NewsAPI, Firecrawl, FastMCP, Redis, Pydantic

--------------------------------------------------------------------------------
Step 1.4 — finance_server.py
--------------------------------------------------------------------------------

Tools:
  get_transactions(user_id, months)   → fetch + filter from Postgres by date
  analyze_spending(user_id, months)   → category breakdown + anomaly flags
  get_surplus(user_id, months)        → income minus expenses = net monthly
                                        surplus
  get_subscriptions(user_id)          → recurring charges, flags unused ones
  get_goals(user_id)                  → financial goals + progress percentage

- Reads WEALTHOS_DB_URL from .env; strips +asyncpg prefix for asyncpg
  compatibility:
    DATABASE_URL = os.getenv("WEALTHOS_DB_URL", "")
    DATABASE_URL = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
- All backed by transactions, subscriptions, financial_goals Postgres tables
- Tech: Postgres + asyncpg, FastMCP, Pydantic, numpy (z-score)

--------------------------------------------------------------------------------
Step 1.5 — calculator_server.py
--------------------------------------------------------------------------------

Tools:
  compound_interest(principal, rate, years)
  loan_emi(principal, rate, months)
  sip_returns(monthly_amount, rate, years)
  inflation_adjusted(amount, rate, years)
  fire_number(monthly_expenses, withdrawal_rate)
  xirr(cashflows, dates)              → actual return rate on investments
  calc_cagr(start, end, years)
  calc_pe_ratio(price, eps)
  calc_eps_growth(eps_history)
  calc_debt_ratio(debt, equity)
  calc_dcf_inputs(ticker)
  calc_wacc(ticker)
  calc_intrinsic_value(fcf, growth, wacc)

- Pure Python — no external API calls, no DB reads
- Output feeds Code Agent's DCF and Monte Carlo models in Phase 5
- Tech: Pure Python, numpy, scipy, FastMCP, Pydantic

--------------------------------------------------------------------------------
Step 1.6 — tax_server.py
--------------------------------------------------------------------------------

Tools:
  calculate_tax(income, section_80c, hra, other_deductions)
                                      → old vs new Indian tax regime comparison
  capital_gains_tax(buy_price, sell_price, quantity, holding_days, asset_type)
                                      → STCG/LTCG for equity, MF, property
  tax_saving_suggestions(income, investments)
                                      → unutilized 80C, 80D, HRA deductions
  advance_tax_schedule(income)        → quarterly payment deadlines + amounts
  get_optimal_regime(income, deductions)
                                      → which regime saves more tax

- Pure Python — implements Indian Income Tax 2025-26 slab rules fully
- No external API required — rules are codified directly
- Tech: Pure Python, FastMCP, Pydantic

--------------------------------------------------------------------------------
Step 1.7 — portfolio_server.py
--------------------------------------------------------------------------------

Tools:
  get_holdings(user_id)               → fetch all holdings from Postgres
  get_portfolio_value(user_id)        → holdings × live prices via yfinance
  get_pnl(user_id)                    → profit/loss per stock and overall
  get_allocation(user_id)             → sector/asset breakdown as percentages +
                                        concentration warnings

- Postgres-backed, user-scoped
- Calls yfinance directly for live prices — no dependency on market_server
- Used heavily by Rebalancing Agent in Phase 5
- Tech: Postgres + asyncpg, yfinance, FastMCP, Pydantic

--------------------------------------------------------------------------------
Step 1.8 — pgvector Setup (replaces qdrant-mcp)
--------------------------------------------------------------------------------

Vector search is handled inside existing PostgreSQL via the pgvector extension —
no separate Qdrant MCP server required.

Enable in PostgreSQL:

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE document_embeddings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ticker TEXT,
    doc_type TEXT,           -- '10-K', '10-Q', 'news', 'insight'
    chunk_text TEXT,
    embedding vector(1536),  -- OpenAI ada-002 or bge-large dimensions
    metadata JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX ON document_embeddings
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);
```

Query pattern used by Data Agent and LlamaIndex RAG pipeline (Phase 2):

```python
# Find top-5 semantically similar chunks for a query
SELECT chunk_text, metadata,
       1 - (embedding <=> query_vector) AS similarity
FROM document_embeddings
WHERE ticker = 'AAPL'
ORDER BY embedding <=> query_vector
LIMIT 5;
```

--------------------------------------------------------------------------------
Step 1.9 — Test All MCP Servers
--------------------------------------------------------------------------------

- pytest for every tool in every server (~5 tests per server = ~35 tests total)
- Add full MCP test suite to GitHub Actions CI
- Document each server in wealthOS/mcp_servers/README.md
- All 7 servers must pass before Phase 2 begins

MCP Server Summary Table:

  Server              | Tools Count | Data Source              | Replaces
  --------------------|-------------|--------------------------|----------------------
  market_server.py    | 10          | yfinance                 | yfinance-mcp +
                      |             |                          | macro-mcp +
                      |             |                          | competitor-mcp
  sec_edgar_server.py | 3           | SEC EDGAR API (free)     | sec-edgar-mcp
  news_server.py      | 3           | NewsAPI + Firecrawl      | news-mcp
  finance_server.py   | 5           | Postgres                 | finance-mcp
  calculator_server.py| 13          | Pure Python              | calculator-mcp
  tax_server.py       | 5           | Pure Python              | tax-mcp
  portfolio_server.py | 4           | Postgres + yfinance      | portfolio-mcp
  pgvector (Postgres) | —           | PostgreSQL extension     | qdrant-mcp +
                      |             |                          | qdrant-server

PHASE 1 DELIVERABLE:
  pytest mcp_servers/ → all 35 tests green
  GitHub Actions CI running on every push
  pgvector document_embeddings table created and indexed
  wealthOS/mcp_servers/README.md documenting all 7 servers

================================================================================
PHASE 2 — LLAMAINDEX + QDRANT RAG PIPELINE
Days 13-17
Goal: Semantic search over 10-K and 10-Q filings with cited answers
================================================================================

--------------------------------------------------------------------------------
Step 2.1 — Document Indexer
--------------------------------------------------------------------------------

```python
# wealthOS/rag/indexer.py
from llama_index.core import VectorStoreIndex, SimpleDirectoryReader, StorageContext
from llama_index.vector_stores.qdrant import QdrantVectorStore
import qdrant_client

class FilingIndexer:
    def __init__(self):
        self.client = qdrant_client.QdrantClient(url=QDRANT_URL)
        self.vector_store = QdrantVectorStore(
            client=self.client,
            collection_name="financial_documents"
        )
        self.storage_context = StorageContext.from_defaults(
            vector_store=self.vector_store
        )

    def index_filing(self, pdf_path: str, ticker: str, filing_type: str):
        documents = SimpleDirectoryReader(input_files=[pdf_path]).load_data()
        for doc in documents:
            doc.metadata = {"ticker": ticker, "filing_type": filing_type}
        index = VectorStoreIndex.from_documents(
            documents,
            storage_context=self.storage_context,
            embed_model=ModalEmbedding()  # calls Modal bge-large endpoint
        )
        return index
```

Tech: LlamaIndex, Qdrant, Modal (bge-large embeddings)

--------------------------------------------------------------------------------
Step 2.2 — Query Engine
--------------------------------------------------------------------------------

```python
# wealthOS/rag/query_engine.py
class FilingQueryEngine:
    def query(self, question: str, ticker: str = None) -> str:
        filters = MetadataFilters(filters=[
            MetadataFilter(key="ticker", value=ticker)
        ]) if ticker else None

        query_engine = self.index.as_query_engine(
            similarity_top_k=5,
            filters=filters
        )
        response = query_engine.query(question)
        return str(response)
```

Example queries:
  "What did RELIANCE say about capex in their 2024 annual report?"
  "What is the 5-year revenue trend?"
  "What risks did management highlight?"

Tech: LlamaIndex, Qdrant, Modal embeddings

--------------------------------------------------------------------------------
Step 2.3 — Wire to Data Agent
--------------------------------------------------------------------------------

In data_agent.py, add LlamaIndex calls alongside MCP calls:
- Fetch 10-K PDF via SEC EDGAR MCP → index via FilingIndexer
- Query via FilingQueryEngine for semantic filing insights
- Combine semantic results with structured yfinance data → FinancialSnapshot

--------------------------------------------------------------------------------
Step 2.4 — Test RAG Pipeline
--------------------------------------------------------------------------------

- Index 5 companies' most recent 10-K PDFs
- Run 10 questions across all 5
- Verify: answers are grounded in filing content with source citations
- Benchmark: LlamaIndex query latency, Qdrant retrieval accuracy

PHASE 2 DELIVERABLE:
  Semantic queries over real 10-K filings returning accurate, cited answers

================================================================================
PHASE 3 — INDIVIDUAL AGENTS
Days 18-30
Goal: Each agent works standalone with verified outputs
      Apply to jobs after this phase
================================================================================

--------------------------------------------------------------------------------
Step 3.1 — Finance Agent (Agno) — Build First
--------------------------------------------------------------------------------

This is what makes WealthOS distinct. Build and test completely before any
other agent.

Sub-step 3.1.1 — OCR Receipt Scanner (image input path)
```python
import anthropic, base64, json

def scan_receipt(image_path: str) -> dict:
    client = anthropic.Anthropic()
    with open(image_path, "rb") as f:
        image_data = base64.b64encode(f.read()).decode()
    response = client.messages.create(
        model="claude-3-5-sonnet-20241022",
        max_tokens=1000,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": image_data}},
                {"type": "text",
                 "text": "Extract: merchant name, total amount, date, and "
                         "category (food/transport/shopping/etc). Return JSON only."}
            ]
        }]
    )
    return json.loads(response.content[0].text)
```

Sub-step 3.1.2 — Bank Statement Parser (PDF input path)
- Convert PDF pages to images → send to local Ollama vision model
- Data never leaves the user's environment (privacy mode)
- Extract: date, description, amount, type (credit/debit)
- Categorize with local LLM → return list of Transaction objects
- Tech: Ollama (llama3.2-vision), pdf_processor.py, Pydantic

Sub-step 3.1.3 — Anomaly Detection
```python
import numpy as np

def detect_anomalies(spending_history: dict[str, list[float]]) -> list[AnomalyFlag]:
    anomalies = []
    for category, monthly_amounts in spending_history.items():
        if len(monthly_amounts) < 3:
            continue
        rolling_mean = np.mean(monthly_amounts[-3:])
        rolling_std = np.std(monthly_amounts[-3:])
        current = monthly_amounts[-1]
        z_score = (current - rolling_mean) / (rolling_std + 1e-9)
        if abs(z_score) > 1.5:
            anomalies.append(AnomalyFlag(
                category=category, current=current,
                average=rolling_mean, z_score=z_score,
                direction="above" if z_score > 0 else "below"
            ))
    return anomalies
```

Sub-step 3.1.4 — Financial Health Score
- Implement compute_health_score() as defined in Section 1.4
- Tech: Pydantic, numpy

Sub-step 3.1.5 — Remaining Finance Agent tools
- Goal Tracker → back-calculate monthly savings needed per goal
- Subscription Auditor → flag recurring with no recent usage in last 30 days
- Debt Optimizer → avalanche vs snowball calculation
- Tax Optimizer → 80C, 80D, HRA utilization against current salary
- Insurance Gap Analyzer → cover vs recommended multiple of income
- Peer Benchmarking → query anonymized cohort data in Postgres

Test Finance Agent:
- Upload a real receipt image → verify JSON extraction
- Upload a sample bank statement PDF → verify transaction categorization
- Run anomaly detection on test data → verify outliers flagged
- Verify Health Score computation against manual calculation

--------------------------------------------------------------------------------
Step 3.2 — Research Agent (Agno + Browser-Use + Firecrawl)
--------------------------------------------------------------------------------

Sub-step 3.2.1 — Browser-Use integration
```python
from browser_use import Agent as BrowserAgent
from langchain_openai import ChatOpenAI

async def browse_analyst_reports(ticker: str) -> str:
    browser_agent = BrowserAgent(
        task=f"Go to Moneycontrol.com and find the latest analyst "
             f"recommendations for {ticker}. Extract: current rating "
             f"(buy/sell/hold), target price, and analyst firm names. "
             f"Return structured JSON.",
        llm=ChatOpenAI(model="gpt-4o")
    )
    result = await browser_agent.run()
    return result.final_result()
```

Sub-step 3.2.2 — Firecrawl integration
```python
from firecrawl import FirecrawlApp

def scrape_news_clean(url: str) -> str:
    app = FirecrawlApp(api_key=FIRECRAWL_API_KEY)
    result = app.scrape_url(url, params={"formats": ["markdown"]})
    return result["markdown"]
```

Test: Run on 5 different tickers — verify Browser-Use navigates successfully,
Firecrawl returns clean content, sentiment scores are reasonable.

Tech: Agno, Browser-Use, Firecrawl, News MCP, LiteLLM (groq-llama for sentiment)

--------------------------------------------------------------------------------
Step 3.3 — Data Agent (PydanticAI + AWS Bedrock + LlamaIndex)
--------------------------------------------------------------------------------

- yfinance + SEC EDGAR via MCP
- LlamaIndex query engine wired in from Phase 2
- LiteLLM routing: uses bedrock-claude model alias
- Redis cache: 15 min TTL per ticker for FinancialSnapshot
- 3-step retry loop: if Pydantic validation fails → retry with error context
- Automatic fallback: LiteLLM routes bedrock-claude → groq-llama on failure
- Produces: FinancialSnapshot (fully typed, zero hallucinated numbers)

Tech: PydanticAI, AWS Bedrock, LiteLLM, LlamaIndex, Qdrant, Redis, Pydantic

--------------------------------------------------------------------------------
Step 3.4 — Risk Agent (CrewAI + DeepSeek R1)
--------------------------------------------------------------------------------

Sub-step 3.4.1 — CrewAI multi-agent setup
- 3-agent crew: MacroAnalyst, PortfolioAnalyst, RiskScorer
- All share PersonalFinanceSnapshot as context

Sub-step 3.4.2 — Personal context injection
```python
def adjust_risk_for_personal_context(
    base_risk_score: int,
    personal: PersonalFinanceSnapshot
) -> tuple[int, list[str]]:
    adjustment = 0
    reasons = []
    if personal.debt_burden_ratio > 0.5:
        adjustment += 2
        reasons.append("EMI burden exceeds 50% of income — high liquidity risk")
    if personal.monthly_surplus < personal.investable_monthly * 0.3:
        adjustment += 1
        reasons.append("Low surplus margin — limited ability to hold through drawdown")
    if any(g.deadline_months < 12 for g in personal.goals):
        adjustment += 1
        reasons.append("Short-term financial goal — long-term equity inadvisable")
    if personal.health_score.total < 50:
        adjustment += 1
        reasons.append("Financial Health Score below 50 — reduce new equity exposure")
    return min(10, base_risk_score + adjustment), reasons
```

Sub-step 3.4.3 — DeepSeek R1 via LiteLLM
- Risk Agent uses deepseek-r1 alias in LiteLLM config
- Reasoning traces captured in AgentOps for full explainability

Tech: CrewAI, DeepSeek R1, LiteLLM, Macro/Portfolio/Competitor MCP, Pydantic

--------------------------------------------------------------------------------
Step 3.5 — Code Agent (Smolagents + E2B)
--------------------------------------------------------------------------------

Sub-step 3.5.1 — E2B sandbox tool
```python
from smolagents import CodeAgent, tool
from e2b_code_interpreter import Sandbox

@tool
def run_financial_model(code: str) -> dict:
    """Execute Python financial model code in E2B sandbox"""
    with Sandbox() as sandbox:
        execution = sandbox.run_code(code)
        if execution.error:
            return {"error": str(execution.error)}
        return {
            "output": execution.text,
            "charts": [r.png for r in execution.results if r.png]
        }
```

Sub-step 3.5.2 — DCF model template
```python
dcf_template = """
import numpy as np

fcf           = {fcf}
growth_rate_5y = {growth}
terminal_growth = 0.03
wacc          = {wacc}

projected_fcf   = [fcf * (1 + growth_rate_5y)**i for i in range(1, 6)]
terminal_value  = projected_fcf[-1] * (1 + terminal_growth) / (wacc - terminal_growth)
dcf_value       = sum(f / (1 + wacc)**i for i, f in enumerate(projected_fcf, 1))
dcf_value      += terminal_value / (1 + wacc)**5

print(f"DCF Intrinsic Value: {{dcf_value:,.0f}}")
"""
```

Sub-step 3.5.3 — Monte Carlo simulation
- 10,000 price paths using random growth rate sampling within analyst range
- Returns: probability distribution of 1-year price targets + base64 chart

Sub-step 3.5.4 — Sensitivity table
- Shows verdict at different growth rate + WACC combinations

Test: Run DCF for a known ticker vs published analyst estimates within 15%.
Tech: Smolagents, E2B, pandas, numpy, matplotlib, Calculator MCP

--------------------------------------------------------------------------------
Step 3.6 — Rebalancing Agent (Agno)
--------------------------------------------------------------------------------

Sub-step 3.6.1 — Holdings fetch and weight computation
- Call portfolio-mcp get_holdings(user_id)
- Call yfinance-mcp for current prices of all holdings
- Compute current_weights per sector

Sub-step 3.6.2 — Drift computation and suggestions
- Implement suggest_rebalance() as defined in Section 1.3 Agent 6
- Run calculation in E2B sandbox for verified arithmetic

Sub-step 3.6.3 — New investment impact projection
- Project weights after the new investment is added
- Flag sectors that will breach target ± 5%
- Produce specific trim/buy actions with rupee amounts

Test: Feed known holdings and target allocation → verify drift calculations
match manual computation exactly.
Tech: Agno, portfolio-mcp, yfinance-mcp, E2B, Pydantic

--------------------------------------------------------------------------------
Step 3.7 — Guardrails AI Validation Layer
--------------------------------------------------------------------------------

All agent outputs pass through Guardrails AI before Writer receives them:

```python
from guardrails import Guard
from guardrails.hub import ValidLength, ValidRange, RestrictToTopic

financial_guard = Guard().use_many(
    ValidRange(min=1, max=10, on="risk_score"),
    ValidLength(min=1, max=5, on="risk_factors"),
    RestrictToTopic(
        valid_topics=["finance", "investment", "stocks", "economics"],
        on="research_summary"
    )
)

validated_output, *rest = financial_guard.validate(raw_agent_output)
```

Tech: Guardrails AI, PydanticAI (dual-layer validation)

--------------------------------------------------------------------------------
Step 3.8 — Writer Agent (LangGraph node + DSPy)
--------------------------------------------------------------------------------

Placeholder DSPy program (full DSPy compilation in Phase 5):
- Define WriteMemo DSPy signature with all 6 agent outputs as input fields
- Hand-write baseline system prompt (will be replaced by DSPy in Phase 5)
- Wire streaming via AG-UI
- Add PDF export call after memo is complete
- Guardrails AI validates output structure before streaming begins

Tech: LangGraph, DSPy, LiteLLM, AG-UI, Guardrails AI, Composio (Drive export)

PHASE 3 DELIVERABLE:
  Each agent works standalone with verified outputs
  *** Apply to jobs after this phase ***

================================================================================
PHASE 4 — LANGGRAPH ORCHESTRATOR + TEMPORAL
Days 31-38
Goal: All 7 agents running in coordinated sequence and parallel
================================================================================

--------------------------------------------------------------------------------
Step 4.1 — LangGraph Graph Structure
--------------------------------------------------------------------------------

```python
graph = StateGraph(WealthOSState)

# Nodes
graph.add_node("input_router",       input_router_node)     # all 4 input types
graph.add_node("extract_intent",     extract_intent_node)   # parse ticker + query
graph.add_node("finance_agent",      finance_agent_node)    # RUNS FIRST
graph.add_node("parallel_agents",    parallel_agents_node)  # Research+Data+Code parallel
graph.add_node("risk_agent",         risk_agent_node)       # needs Finance + Data
graph.add_node("rebalancing_agent",  rebalancing_agent_node)
graph.add_node("hitl_checkpoint",    hitl_checkpoint_node)  # user review
graph.add_node("writer_agent",       writer_agent_node)     # DSPy-optimized
graph.add_node("store_results",      store_results_node)    # Mem0 + Kafka + DB
graph.add_node("error_handler",      error_handler_node)

# Edges
graph.set_entry_point("input_router")
graph.add_edge("input_router",      "extract_intent")
graph.add_edge("extract_intent",    "finance_agent")
graph.add_edge("finance_agent",     "parallel_agents")   # Research+Data+Code parallel
graph.add_edge("parallel_agents",   "risk_agent")
graph.add_edge("risk_agent",        "rebalancing_agent")
graph.add_edge("rebalancing_agent", "hitl_checkpoint")
graph.add_conditional_edges("hitl_checkpoint", route_hitl, {
    "approved": "writer_agent",
    "revise":   "extract_intent"
})
graph.add_edge("writer_agent", "store_results")
```

--------------------------------------------------------------------------------
Step 4.2 — Temporal Durable Workflow
--------------------------------------------------------------------------------

```python
# wealthOS/workflows/temporal_workflows.py
from temporalio import workflow, activity
from datetime import timedelta

@workflow.defn
class WealthOSWorkflow:
    @workflow.run
    async def run(self, query: str, user_id: str) -> str:
        personal_finance = await workflow.execute_activity(
            run_finance_agent, query, user_id,
            start_to_close_timeout=timedelta(seconds=30)
        )
        research, data, code = await asyncio.gather(
            workflow.execute_activity(run_research_agent, ...),
            workflow.execute_activity(run_data_agent, ...),
            workflow.execute_activity(run_code_agent, ...)
        )
        risk = await workflow.execute_activity(
            run_risk_agent, personal_finance, data,
            start_to_close_timeout=timedelta(seconds=20)
        )
        rebalance = await workflow.execute_activity(
            run_rebalancing_agent, personal_finance, data,
            start_to_close_timeout=timedelta(seconds=15)
        )
        # HITL pause — workflow waits for user approval signal
        await workflow.wait_condition(lambda: self.user_approved)
        memo = await workflow.execute_activity(
            run_writer_agent, research, data, risk, code,
            personal_finance, rebalance
        )
        return memo

    @workflow.signal
    def approve(self):
        self.user_approved = True
```

Why Temporal: without it, if a container restarts mid-analysis (60-second pipeline),
the entire run fails. Temporal resumes from the last completed activity automatically.

Tech: Temporal, LangGraph, asyncio

--------------------------------------------------------------------------------
Step 4.3 — Morning Briefing Workflow
--------------------------------------------------------------------------------

- Implement MorningBriefingWorkflow as defined in Section 1.5
- Register as daily cron: "0 8 * * *"
- Wire: Kafka events → briefing compiler → Composio WhatsApp + Gmail
- Test: trigger manually, verify all 4 check types work

Tech: Temporal (cron), Kafka, Composio, yfinance-mcp, finance-mcp, macro-mcp

--------------------------------------------------------------------------------
Step 4.4 — Multi-Stock Comparison Mode
--------------------------------------------------------------------------------

- Add comparison_mode flag to WealthOSState
- On detection of multiple tickers: spawn parallel pipelines per ticker
- Share PersonalFinanceSnapshot across all pipelines
- Writer Agent receives N× ResearchReports + N× FinancialSnapshots
- Output: comparative table ranked by fit to this user's portfolio

--------------------------------------------------------------------------------
Step 4.5 — HITL Checkpoint Implementation
--------------------------------------------------------------------------------

HITL panel shows all 7 agent output sections:
  1. Financial Health Score card (from Finance Agent)
  2. Personal Finance Summary (anomalies, surplus, goals)
  3. Research findings (Browser-Use sources highlighted, citations)
  4. Financial Snapshot (numbers with filing source citations)
  5. Code Agent output (DCF value, Monte Carlo chart)
  6. Risk Score (with personal risk adjustment explained)
  7. Rebalancing suggestion (current vs projected allocation)

Buttons:
  [Approve]  → triggers writer_agent_node
  [Revise]   → routes back to extract_intent_node
  [Set Price Alert] → calls alert-mcp → publishes to Kafka

Tech: AG-UI (pause/resume), Temporal (signal), LangGraph (conditional edges)

--------------------------------------------------------------------------------
Step 4.6 — AG-UI Streaming Events
--------------------------------------------------------------------------------

New events for WealthOS:
  HEALTH_SCORE_READY         Finance Agent computed Health Score
  INPUT_TYPE_DETECTED        which input mode was used
  PERSONAL_FINANCE_READY     Finance Agent completed snapshot
  BROWSER_NAVIGATING         Browser-Use navigating a specific URL
  CODE_RUNNING               Code Agent executing in E2B sandbox
  CODE_COMPLETE              DCF value + chart ready
  REBALANCING_READY          Rebalancing Agent completed suggestions
  PRICE_ALERT_SET            Kafka alert published
  BRIEFING_SCHEDULED         Morning briefing registered
  PDF_EXPORTED               PDF pushed to Google Drive

Plus all standard events: TEXT_MESSAGE_CHUNK, TOOL_CALL_START, STATE_DELTA, etc.

PHASE 4 DELIVERABLE:
  Full end-to-end pipeline: single query → all 7 agents → streamed memo

================================================================================
PHASE 5 — DSPY PROMPT OPTIMIZATION
Days 39-42
Goal: Writer Agent prompts compiled by DSPy, not hand-written
================================================================================

--------------------------------------------------------------------------------
Step 5.1 — Build Golden Dataset
--------------------------------------------------------------------------------

Create wealthOS/eval/writer_golden_dataset.json: 30 examples of:
  Input:  ResearchReport + FinancialSnapshot + RiskReport +
          PersonalFinanceSnapshot + CodeAgentOutput + RebalanceSuggestion
  Output: High-quality personalized investment memo scored 5/5 by LLM-as-Judge

Sources:
  - 10 memos written manually (best quality)
  - 10 memos from past runs that scored > 4.2
  - 10 memos adapted from real financial research

--------------------------------------------------------------------------------
Step 5.2 — Define DSPy Signature and Metric
--------------------------------------------------------------------------------

```python
import dspy

class WriteMemo(dspy.Signature):
    """Write a personalized investment memo for a specific user."""

    research_report:      str = dspy.InputField(desc="research findings with citations")
    financial_snapshot:   str = dspy.InputField(desc="validated financial metrics")
    risk_report:          str = dspy.InputField(desc="risk score and factors")
    personal_finance:     str = dspy.InputField(desc="user's financial health context")
    code_output:          str = dspy.InputField(desc="DCF value and Monte Carlo results")
    rebalance_suggestion: str = dspy.InputField(desc="portfolio rebalancing actions")

    memo: str = dspy.OutputField(desc="full personalized investment memo")

class MemoQualityMetric(dspy.Module):
    def forward(self, example, pred, trace=None) -> float:
        judge = dspy.Predict("memo, expected_memo -> score: float")
        result = judge(memo=pred.memo, expected_memo=example.memo)
        return float(result.score) / 5.0
```

Scored on 4 dimensions: structure, accuracy, personalization, actionability.

--------------------------------------------------------------------------------
Step 5.3 — Compile Optimized Program
--------------------------------------------------------------------------------

```python
optimizer = dspy.BootstrapFewShotWithRandomSearch(
    metric=MemoQualityMetric(),
    max_bootstrapped_demos=4,
    num_candidate_programs=10
)

compiled_writer = optimizer.compile(
    student=dspy.Predict(WriteMemo),
    trainset=golden_dataset
)

compiled_writer.save("wealthOS/eval/compiled_writer.json")
```

Runtime: 30-60 minutes on first compilation. Save and reuse.

--------------------------------------------------------------------------------
Step 5.4 — Measure Before vs After
--------------------------------------------------------------------------------

Run LLM-as-Judge on 10 held-out memos:

| Prompt Strategy   | Avg Quality | Structure | Personalization | Actionability |
|-------------------|-------------|-----------|-----------------|---------------|
| Hand-written      | X.X / 5.0   | X.X       | X.X             | X.X           |
| DSPy compiled     | X.X / 5.0   | X.X       | X.X             | X.X           |
| Fine-tuned (Ph11) | X.X / 5.0   | X.X       | X.X             | X.X           |

Target: DSPy compiled improves personalization dimension by 0.3-0.5 points.
Track in W&B Weave for full before/after comparison chart.

PHASE 5 DELIVERABLE:
  compiled_writer.json saved
  Measurable quality improvement over baseline documented in README

================================================================================
PHASE 6 — MEMORY + REDIS + KAFKA + ALERTS
Days 43-47
Goal: Persistent memory, smart caching, real-time alerts, notifications
================================================================================

--------------------------------------------------------------------------------
Step 6.1 — Mem0 Enhanced Memory
--------------------------------------------------------------------------------

```python
# READ before agent runs
memory_context = mem0_client.search(
    query=f"{ticker} analysis personal finance",
    user_id=user_id,
    limit=5
)
# Inject: "Last time you analyzed RELIANCE, your surplus was ₹12,000.
#  You were concerned about valuation. Your surplus has since grown to ₹18,000."

# WRITE after analysis completes
mem0_client.add([
    f"Analyzed {ticker} on {date}. Verdict: {verdict}. "
    f"Risk score: {risk_score}. Health Score: {health_score}.",
    f"User's surplus at time of analysis: ₹{surplus:,.0f}.",
    f"User's main concern: {key_risk}.",
    f"DCF intrinsic value: ₹{dcf_value:,.0f} vs current ₹{current_price:,.0f}.",
    f"Rebalancing actions taken: {rebalance_summary}."
], user_id=user_id)
```

Tech: Mem0, S3 (backup), Postgres (index)

--------------------------------------------------------------------------------
Step 6.2 — Redis Caching Strategy
--------------------------------------------------------------------------------

```python
CACHE_STRATEGIES = {
    "price_data":          60 * 5,        # 5 minutes
    "financial_snapshot":  60 * 15,       # 15 minutes
    "research_report":     60 * 30,       # 30 minutes
    "risk_report":         60 * 60,       # 1 hour
    "personal_finance":    60 * 60 * 6,   # 6 hours
    "macro_data":          60 * 60,       # 1 hour
    "filing_index":        60 * 60 * 24,  # 24 hours
    "health_score":        60 * 60 * 6,   # 6 hours
    "rebalance_suggestion":60 * 30,       # 30 minutes
}
```

Redis also used for:
- pub/sub for HITL events
- Rate limiting via slowapi (10 requests/minute per user)
- Session storage

Tech: Redis, slowapi

--------------------------------------------------------------------------------
Step 6.3 — Kafka Price Alert System
--------------------------------------------------------------------------------

Producer (Alert MCP Server):
```python
from kafka import KafkaProducer

producer = KafkaProducer(bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS)

def publish_price_alert(ticker, target, direction, user_id):
    producer.send("price-alerts", json.dumps({
        "ticker": ticker, "target": target,
        "direction": direction, "user_id": user_id
    }).encode())
```

Consumer (wealthOS/services/alert_consumer.py):
```python
from kafka import KafkaConsumer

consumer = KafkaConsumer("price-alerts",
                         bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS)

async def alert_consumer_loop():
    for message in consumer:
        alert = json.loads(message.value)
        current_price = yfinance.Ticker(alert["ticker"]).info["currentPrice"]
        if alert["direction"] == "above" and current_price >= alert["target"]:
            send_alert_notification(alert["user_id"], alert["ticker"], current_price)
        elif alert["direction"] == "below" and current_price <= alert["target"]:
            send_alert_notification(alert["user_id"], alert["ticker"], current_price)
```

Tech: Kafka (kafka-python), Postgres (price_alerts table), yfinance

--------------------------------------------------------------------------------
Step 6.4 — Composio Notifications
--------------------------------------------------------------------------------

```python
from composio import ComposioToolSet, Action

def send_alert_notification(user_id: str, ticker: str, price: float):
    toolset = ComposioToolSet()

    # Email via Gmail
    toolset.execute_action(
        action=Action.GMAIL_SEND_EMAIL,
        params={
            "to": get_user_email(user_id),
            "subject": f"WealthOS Alert: {ticker} hit your target",
            "body": f"{ticker} has reached ₹{price:,.2f}. "
                    f"Log in to WealthOS to review."
        }
    )

    # WhatsApp notification
    toolset.execute_action(
        action=Action.WHATSAPP_SEND_MESSAGE,
        params={
            "to": get_user_phone(user_id),
            "message": f"WealthOS: {ticker} at ₹{price:,.2f} — your target triggered."
        }
    )

    # Export to Google Drive
    toolset.execute_action(
        action=Action.GOOGLEDRIVE_UPLOAD_FILE,
        params={
            "file_name": f"WealthOS_{ticker}_{date.today()}.pdf",
            "file_content": get_last_memo_pdf(user_id, ticker),
            "mime_type": "application/pdf"
        }
    )
```

Tech: Composio (Gmail, WhatsApp, Google Drive actions)

PHASE 6 DELIVERABLE:
  Mem0 memory read/write working
  All Redis cache layers active
  Kafka producer + consumer running
  Price alert → WhatsApp/Gmail notification end-to-end verified

================================================================================
PHASE 7 — OBSERVABILITY
Days 48-51
Goal: Three-layer observability across AI traces, agent decisions, eval tracking
================================================================================

--------------------------------------------------------------------------------
Step 7.1 — LangSmith
--------------------------------------------------------------------------------

- Tag every LangGraph node with LangSmith trace
- Track per node: input/output, token count, latency, LiteLLM routing decision
- Create dataset: 30 test queries → run weekly evals
- Export traces for portfolio demo screenshots

Tech: LangSmith, LangChain tracing

--------------------------------------------------------------------------------
Step 7.2 — AgentOps
--------------------------------------------------------------------------------

- Track: memories retrieved per session, tools called per agent,
  Browser-Use navigation steps, A2A messages, DeepSeek R1 reasoning traces,
  rebalancing computation steps
- Export decision traces for portfolio demo

Tech: AgentOps

--------------------------------------------------------------------------------
Step 7.3 — W&B Weave
--------------------------------------------------------------------------------

```python
import weave

weave.init("wealthOS")

@weave.op()
def run_writer_agent(research, data, risk, personal_finance,
                     code_output, rebalance) -> str:
    # Weave captures inputs, outputs, latency, token cost automatically
    ...

@weave.op()
def compute_health_score(snapshot: PersonalFinanceSnapshot) -> HealthScore:
    ...
```

Use W&B Weave to compare:
  1. Baseline Writer Agent (hand-written prompt)
  2. DSPy-compiled Writer Agent
  3. Fine-tuned Writer Agent (Phase 11)

Result: before/after comparison chart in W&B dashboard.

Tech: W&B Weave

--------------------------------------------------------------------------------
Step 7.4 — AWS CloudWatch
--------------------------------------------------------------------------------

```python
import watchtower, logging

logging.getLogger().addHandler(
    watchtower.CloudWatchLogHandler(log_group="wealthOS-api")
)
```

Log groups:
  wealthOS-api       request count, error rate, agent success rate
  wealthOS-mcp       tool call latency per MCP server
  wealthOS-kafka     consumer lag, alert delivery time
  wealthOS-temporal  workflow completion rate, activity retries
  wealthOS-briefing  morning briefing delivery time

Tech: AWS CloudWatch, watchtower

PHASE 7 DELIVERABLE:
  Full trace visible in LangSmith for any analysis run
  AgentOps dashboard showing agent decision history
  W&B Weave showing baseline vs DSPy comparison

================================================================================
PHASE 8 — FRONTEND
Days 52-58
Goal: Full interactive UI with all 4 input modes, streaming, HITL,
      finance dashboard, rebalancing panel
================================================================================

--------------------------------------------------------------------------------
Step 8.1 — CopilotKit Setup + Google OAuth2
--------------------------------------------------------------------------------

- Next.js app, CopilotKit provider, AG-UI runtime
- NextAuth.js with Google OAuth2
- user_id from Google session flows to Mem0, Postgres, Kafka alerts

Tech: Next.js, CopilotKit, AG-UI, NextAuth.js

--------------------------------------------------------------------------------
Step 8.2 — Four Input Modes
--------------------------------------------------------------------------------

Text input: standard CopilotKit chat interface

Voice input:
```typescript
const startRecording = async () => {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  const recorder = new MediaRecorder(stream);
  recorder.ondataavailable = async (e) => {
    const formData = new FormData();
    formData.append("audio", e.data, "query.webm");
    const { transcript } = await fetch("/api/transcribe", {
      method: "POST", body: formData
    }).then(r => r.json());
    setQuery(transcript);  // pre-fill chat with transcription
  };
  recorder.start();
};
```

Image upload: drag-and-drop or file picker → sends to /finance/snapshot
PDF upload:   drag-and-drop or file picker → sends to /finance/snapshot

Tech: CopilotKit, Whisper (via /transcribe), Claude Vision (via /finance/snapshot)

--------------------------------------------------------------------------------
Step 8.3 — Finance Dashboard (/finance page)
--------------------------------------------------------------------------------

Cards and components:
  - Financial Health Score card (large, colored by grade)
  - Monthly spending breakdown (bar chart by category)
  - Anomaly flags (highlighted categories with z-score explanation)
  - Goal progress bars (house, car, emergency fund, retirement)
  - Subscription audit table (recurring charges with usage flag)
  - Tax optimizer card (80C remaining, HRA benefit, regime comparison)
  - Debt payoff timeline (snowball vs avalanche comparison chart)
  - Peer benchmarks (your spending % vs cohort %)
  - Morning briefing toggle (enable/disable + time preference)

Tech: Next.js, Recharts / Chart.js, CopilotKit

--------------------------------------------------------------------------------
Step 8.4 — Rebalancing Panel
--------------------------------------------------------------------------------

Components:
  - Current allocation pie chart (sectors)
  - Target allocation overlay
  - Drift visualization (bar chart, over/under target)
  - New investment impact preview (projected allocation after buy)
  - Specific suggestions table: action, sector, amount (₹)
  - "Apply rebalancing" button (sets follow-up alerts)

Tech: Next.js, Recharts, AG-UI (receives REBALANCING_READY event)

--------------------------------------------------------------------------------
Step 8.5 — Main Analysis Chat Interface
--------------------------------------------------------------------------------

New elements vs standard chat:
  - Microphone button (voice input)
  - Image/PDF upload button (triggers /finance/snapshot path)
  - "Personal Finance Context" panel (shows PersonalFinanceSnapshot summary)
  - Code Agent output panel (DCF value, Monte Carlo chart)
  - Rebalancing panel (inline in analysis view)
  - Price alert setter (in HITL review panel)
  - PDF export button (calls /export/pdf)
  - Privacy mode toggle → routes sensitive data through Ollama

Tech: CopilotKit, AG-UI, Next.js

--------------------------------------------------------------------------------
Step 8.6 — Memory and Analysis History Panel
--------------------------------------------------------------------------------

  - Sidebar: past analyses from Postgres analyses table
  - Finance history: month-over-month spending trends
  - Alert history: active and triggered price alerts
  - Memory panel: what Mem0 knows about this user (summarized)

PHASE 8 DELIVERABLE:
  Full interactive UI — all 4 input modes, streaming, HITL, finance dashboard,
  rebalancing panel, PDF export, alert management

================================================================================
PHASE 9 — FASTAPI + DOCKER + CI/CD
Days 59-65
Goal: Fully deployed, production-ready with live URL
================================================================================

--------------------------------------------------------------------------------
Step 9.1 — FastAPI Backend — All Endpoints
--------------------------------------------------------------------------------

POST   /analyze                → main analysis endpoint (streaming)
POST   /analyze/compare        → multi-stock comparison mode
POST   /approve                → HITL approval
POST   /transcribe             → Whisper STT endpoint
POST   /export/pdf             → export memo as PDF

POST   /finance/snapshot       → upload bank statement / receipts (image or PDF)
POST   /finance/goals          → set financial goals
GET    /finance/{user_id}      → get PersonalFinanceSnapshot
GET    /finance/health/{user_id} → get Financial Health Score

GET    /memory/{user_id}       → fetch memory history
GET    /alerts/{user_id}       → list active price alerts
POST   /alerts                 → set price alert
DELETE /alerts/{id}            → cancel alert

POST   /briefing/enable        → enable morning briefing
POST   /briefing/disable       → disable morning briefing
PUT    /briefing/time          → set briefing time preference

GET    /portfolio/{user_id}    → get holdings + rebalance suggestion
POST   /portfolio/holdings     → update portfolio holdings

GET    /health                 → service health check
GET    /mcp/{server}/tools     → list tools per MCP server

Middleware:
  - JWT auth on all routes (python-jose)
  - Per-user token budget in Postgres (token_budgets table)
  - Rate limiting via slowapi (10 requests/minute per user)
  - LiteLLM cost tracking feeds back to token_budgets

Tech: FastAPI, python-jose, slowapi, LiteLLM

--------------------------------------------------------------------------------
Step 9.2 — Docker Compose — All 9 Services
--------------------------------------------------------------------------------

```yaml
services:
  wealthOS-api:
    build: .
    ports: ["8000:8000"]
    depends_on: [wealthOS-db, wealthOS-redis, wealthOS-kafka,
                 wealthOS-qdrant, wealthOS-temporal]

  wealthOS-mcp:
    build: ./mcp_servers
    ports: ["8001:8001"]
    depends_on: [wealthOS-db, wealthOS-redis, wealthOS-kafka]

  wealthOS-frontend:
    build: ./frontend
    ports: ["3000:3000"]
    depends_on: [wealthOS-api]

  wealthOS-db:
    image: pgvector/pgvector:pg16
    ports: ["5432:5432"]

  wealthOS-qdrant:
    image: qdrant/qdrant
    ports: ["6333:6333"]

  wealthOS-redis:
    image: redis:7-alpine
    ports: ["6379:6379"]

  wealthOS-kafka:
    image: confluentinc/cp-kafka
    ports: ["9092:9092"]
    depends_on: [zookeeper]

  wealthOS-ollama:
    image: ollama/ollama
    ports: ["11434:11434"]
    command: ["serve"]
    # pull llama3.2-vision on startup via entrypoint script

  wealthOS-temporal:
    image: temporalio/auto-setup
    ports: ["7233:7233"]
```

--------------------------------------------------------------------------------
Step 9.3 — GitHub Actions CI/CD
--------------------------------------------------------------------------------

On every push to main:
  1. Run pytest on all 11 MCP servers
  2. Run LangGraph unit tests
  3. Run Guardrails AI schema validation tests
  4. Build Docker images for all 9 services
  5. Push to AWS ECR
  6. Deploy to AWS App Runner → live public URL auto-updates

```yaml
- name: Push to ECR
  run: |
    aws ecr get-login-password | \
    docker login --username AWS --password-stdin $ECR_REGISTRY
    docker push $ECR_REGISTRY/wealthOS-api:latest

- name: Deploy to App Runner
  run: |
    aws apprunner start-deployment --service-arn $APP_RUNNER_ARN
```

README badges: build passing / live URL / last deploy time

Tech: GitHub Actions, AWS ECR, AWS App Runner, Docker

PHASE 9 DELIVERABLE:
  docker-compose up --build → all 9 services running
  Live public URL on AWS App Runner
  CI/CD pipeline passing on every push

================================================================================
PHASE 10 — EVALUATION SUITE
Days 66-70
Goal: Real, published numbers for resume and README
================================================================================

--------------------------------------------------------------------------------
Step 10.1 — Data Accuracy Evaluation
--------------------------------------------------------------------------------

- 20 stocks with known ground truth
- Run Data Agent on all 20 → compare against yfinance baseline
- Metrics: price accuracy, P/E accuracy, EPS accuracy
- Target: 99%+ accuracy on all three
- DCF test: Code Agent intrinsic value vs analyst consensus targets
- Target: within 15% of consensus

--------------------------------------------------------------------------------
Step 10.2 — Finance Agent Accuracy
--------------------------------------------------------------------------------

- 10 test users with known transaction histories (manually labeled)
- Run Finance Agent → compare anomaly flags to ground truth labels
- Metrics: precision, recall, F1 on anomaly detection
- Tax optimizer: verify 80C, HRA calculations against Indian IT rules
- Health Score: verify computation matches manual calculation (100% target)
- Target: F1 score > 0.85 on anomaly detection

--------------------------------------------------------------------------------
Step 10.3 — Research Quality Evaluation
--------------------------------------------------------------------------------

LLM-as-Judge (Groq Llama 3.3 70B) scores each Research Report 1-5:
  - Source quality (Browser-Use vs API-only)
  - Recency (headlines from last 7 days)
  - Relevance (findings relate to query)
  - Citation completeness

Target: Research Agent with Browser-Use scores 0.4+ higher than API-only baseline.
Target overall: > 4.0 / 5.0

--------------------------------------------------------------------------------
Step 10.4 — Rebalancing Accuracy
--------------------------------------------------------------------------------

- 10 test portfolios with known holdings and target allocations
- Compute drift manually for each
- Run Rebalancing Agent → compare to manual computation
- Target: 100% accurate drift calculation

--------------------------------------------------------------------------------
Step 10.5 — DSPy vs Baseline vs Fine-tuned Comparison
--------------------------------------------------------------------------------

Three-way W&B Weave comparison across 10 held-out memos:
  - Hand-written prompt (baseline)
  - DSPy-compiled prompt (Phase 5)
  - Fine-tuned Llama 8B (Phase 11)

Publish full comparison table in README (see Phase 5 table format).

--------------------------------------------------------------------------------
Step 10.6 — Locust Load Test
--------------------------------------------------------------------------------

```bash
locust -u 10 -r 2 --headless -t 60s --host http://wealthOS.yourdomain.com
```

Test scenarios:
  - Standard text analysis
  - Voice analysis (audio file upload)
  - Finance upload (bank statement OCR)
  - Alert set + retrieval
  - Multi-stock comparison (3 tickers)

Targets:
  p95 latency        < 90 seconds under 10 concurrent users
  Error rate          0% 5xx
  Kafka alert lag    < 5 seconds
  Morning briefing   < 30 seconds after 8 AM trigger

PHASE 10 DELIVERABLE:
  All evaluation metrics measured and published in README
  W&B Weave comparison chart live

================================================================================
PHASE 11 — MODAL FINE-TUNING + INFERENCE
Days 71-74
Goal: Fine-tune Writer Agent on personalized investment memos with
      PersonalFinanceSnapshot context
================================================================================

--------------------------------------------------------------------------------
Step 11.1 — bge-large Embedding Server (Modal A100)
--------------------------------------------------------------------------------

Already serving from Phase 0 Modal config — confirm it is running and serving
Qdrant embeddings correctly. No new work if Phase 0 was set up correctly.

Tech: Modal, bge-large, A100

--------------------------------------------------------------------------------
Step 11.2 — Writer Agent Fine-tuning (Modal H100, QLoRA)
--------------------------------------------------------------------------------

Training data:
  - 60+ examples: all 6 agent outputs as input → high-quality personalized memo
  - PersonalFinanceSnapshot and RebalanceSuggestion MUST be present in every
    training example — this is what the model needs to learn to use naturally

Training objective:
  Model learns to write: "Given your ₹18,000 monthly surplus and outstanding
  home loan EMI of ₹12,000..." naturally, without being prompted to
  reference personal finance details explicitly.

QLoRA fine-tuning on Llama 3 8B (Modal H100):
  - Base model: meta-llama/Meta-Llama-3-8B-Instruct
  - LoRA rank: 16, alpha: 32
  - Batch size: 4, gradient accumulation: 4
  - Learning rate: 2e-4, epochs: 3

Tech: Modal H100, QLoRA, Llama 3 8B, transformers, peft

--------------------------------------------------------------------------------
Step 11.3 — vLLM Inference Endpoint (Modal A100)
--------------------------------------------------------------------------------

- Serve fine-tuned model with vLLM
- keep_warm=1 → no cold start for demo
- Swap Writer Agent LLM backend from LiteLLM groq-llama to Modal endpoint
- Add modal-writer alias to litellm_config.yaml

```python
# modal_apps/writer_inference.py
import modal

app = modal.App("wealthOS-writer")

@app.function(gpu="A100", keep_warm=1)
@modal.web_endpoint()
def generate(request: dict) -> dict:
    # vLLM inference using fine-tuned model
    ...
```

Tech: Modal A100, vLLM, keep_warm

--------------------------------------------------------------------------------
Step 11.4 — Measure Personalization Improvement
--------------------------------------------------------------------------------

- Run held-out test memos through all 3 Writer versions
- Measure personalization sub-score in W&B Weave
- Target: fine-tuned model scores highest on personalization dimension
- Update README comparison table with final numbers

Estimated Modal credits: ~$25-35 total from $150-200 budget

PHASE 11 DELIVERABLE:
  Fine-tuned Writer Agent live on Modal A100 endpoint
  W&B Weave three-way comparison chart complete
  README evaluation table fully populated

================================================================================
SECTION 4 — EVALUATION TARGETS SUMMARY
================================================================================

Metric                                        | Target
----------------------------------------------|---------------------------
Data Agent accuracy (price, P/E, EPS)         | 99%+
Finance anomaly detection F1                  | > 0.85
Health Score computation accuracy             | 100% vs manual
Rebalancing drift calculation accuracy        | 100% vs manual
DCF vs analyst consensus                      | within 15%
Research quality score (LLM-as-Judge)         | > 4.0 / 5.0
Writer quality — baseline prompt              | baseline
Writer quality — DSPy compiled                | +0.3 to +0.5 over baseline
Writer quality — fine-tuned                   | best
p95 latency (10 concurrent users)             | < 90 seconds
Error rate under load                         | 0% 5xx
Kafka alert consumer lag                      | < 5 seconds
Morning briefing delivery                     | < 30 seconds after 8 AM

================================================================================
SECTION 5 — API ENDPOINTS REFERENCE
================================================================================

POST   /analyze                → main analysis endpoint (streaming)
POST   /analyze/compare        → multi-stock comparison mode
POST   /approve                → HITL approval
POST   /transcribe             → Whisper STT endpoint
POST   /export/pdf             → export memo as formatted PDF

POST   /finance/snapshot       → upload bank statement / receipts (image or PDF)
POST   /finance/goals          → set/update financial goals
GET    /finance/{user_id}      → get PersonalFinanceSnapshot
GET    /finance/health/{user_id} → get Financial Health Score

GET    /memory/{user_id}       → fetch memory history
GET    /alerts/{user_id}       → list active price alerts
POST   /alerts                 → set price alert
DELETE /alerts/{id}            → cancel alert

POST   /briefing/enable        → enable morning briefing
POST   /briefing/disable       → disable morning briefing
PUT    /briefing/time          → set briefing time preference

GET    /portfolio/{user_id}    → get holdings + rebalance suggestion
POST   /portfolio/holdings     → update portfolio holdings

GET    /health                 → service health check
GET    /mcp/{server}/tools     → list tools per MCP server

================================================================================
SECTION 6 — RESUME TALKING POINTS
================================================================================

Resume Bullet                                    | Interview Question
-------------------------------------------------|---------------------------
Built 7-agent personal financial intelligence    | Walk me through your
platform — Finance, Research, Data, Risk, Code,  | multi-agent architecture
Rebalancing, Writer — coordinated via LangGraph  |
+ Temporal with Google A2A protocol              |

Four input modalities: text, voice (Whisper STT),| How did you handle
images (receipts via Claude 3.5 vision), PDFs    | multiple input types?
(bank statements) — all normalized through a     |
single InputRouter before entering the pipeline  |

Finance Agent computes a Financial Health Score  | How does personal finance
(0-100) from surplus ratio, debt burden, goal    | context enrich your
progress, tax utilization, and insurance coverage| analysis?
— injected into every investment recommendation  |

Rebalancing Agent analyzes current portfolio     | How is your advice
holdings against target allocation, projects the | actually personalized?
impact of each new investment, and suggests      |
specific buy/sell actions with rupee amounts     |

Temporal-powered morning briefing runs at 8 AM  | What makes WealthOS a
daily without user interaction — proactively     | proactive assistant vs
surfaces watchlist moves, spending anomalies,    | just a tool?
near-trigger alerts, and macro news via          |
WhatsApp/Gmail                                   |

Research Agent navigates live NSE/Moneycontrol/  | What is Browser-Use?
analyst pages using Browser-Use — real browser,  | How does it differ
not cached APIs                                  | from scraping?

LiteLLM gateway unifies 6 LLM backends — Groq,  | How do you handle
Bedrock, DeepSeek R1, o3-mini, Ollama, Modal —  | multi-provider LLM
with automatic fallback and per-user cost        | infrastructure?
tracking                                         |

Code Agent (Smolagents + E2B) writes and         | Why not just have the
executes real Python DCF models and Monte Carlo  | LLM compute the DCF?
simulations — verifiable numbers, not hallucinated|

DSPy-compiled Writer Agent prompts — 30-example  | What is DSPy? Why not
golden dataset, BootstrapFewShot optimization,   | just write better
X.X point quality improvement in W&B Weave      | prompts?

Guardrails AI + PydanticAI dual-layer validation | How do you prevent
on every agent output — schema enforcement,      | hallucinations in
range checks, topic restriction before Writer    | financial data?
receives data                                    |

Three-layer observability: LangSmith (node       | How do you monitor and
traces), AgentOps (agent decisions + Browser-Use | debug a 7-agent system?
steps), W&B Weave (eval comparison across        |
prompt strategies)                               |

PDF export with Composio Google Drive push,      | What is Composio? Why
WhatsApp + Gmail alerts via Composio             | use it over building
                                                 | integrations yourself?

Locust load tests: p95 < 90s, 0% 5xx under 10  | How do you evaluate and
concurrent users. Finance anomaly F1 > 0.85.    | benchmark your system?
Rebalancing drift 100% accurate.                 |

================================================================================
SECTION 7 — PROJECT STRUCTURE
================================================================================

wealthOS/
├── agents/
│   ├── finance_agent.py
│   ├── research_agent.py
│   ├── data_agent.py
│   ├── risk_agent.py
│   ├── code_agent.py
│   ├── rebalancing_agent.py
│   └── writer_agent.py
├── mcp_servers/
│   ├── yfinance_server.py
│   ├── sec_edgar_server.py
│   ├── news_server.py
│   ├── finance_server.py
│   ├── calculator_server.py
│   ├── tax_server.py
│   ├── portfolio_server.py
│   ├── macro_server.py
│   ├── alert_server.py
│   ├── competitor_server.py
│   └── qdrant_server.py
├── graph/
│   ├── state.py
│   └── nodes.py
├── rag/
│   ├── indexer.py
│   └── query_engine.py
├── input/
│   ├── router.py
│   ├── whisper_handler.py
│   ├── vision_handler.py
│   └── pdf_processor.py
├── memory/
│   └── mem0_client.py
├── gateway/
│   └── litellm_config.yaml
├── workflows/
│   ├── temporal_workflows.py
│   └── morning_briefing.py
├── api/
│   ├── main.py
│   └── export.py
├── health/
│   └── score.py
├── services/
│   └── alert_consumer.py
├── frontend/
├── eval/
│   ├── writer_golden_dataset.json
│   └── compiled_writer.json
├── modal_apps/
│   ├── embedder.py
│   ├── finetune_writer.py
│   └── writer_inference.py
└── .github/workflows/
    └── ci.yml

================================================================================
SECTION 8 — POSSIBLE UPGRADES (POST-LAUNCH)
================================================================================

Upgrade                  | What It Adds                          | Impact
-------------------------|---------------------------------------|----------
Ollama privacy mode      | All personal finance data processed   | Very High
(full)                   | locally — zero cloud exposure,        |
                         | GDPR/data privacy story               |
Multi-currency support   | NRI users with USD + INR portfolios   | High
AutoGen alternative      | Rebuild Risk Agent using Microsoft    | High
                         | AutoGen — compare outputs directly    |
Credit score integration | Pull CIBIL data → factor into risk   | Medium
                         | capacity score                        |
Temporal saga pattern    | Multi-step financial workflow with    | Medium
                         | compensating transactions             |
Real-time P&L dashboard  | WebSocket stream → live portfolio P&L | High
                         | against holdings                      |
Tax filing export        | Export 80C/80D summary as PDF for CA | High
Multi-language support   | Hindi / regional language queries     | High
RAPTOR RAG               | Advanced hierarchical RAG over filings| Medium

================================================================================

WealthOS Blueprint v2.0
7 agents · 4 input types · Financial Health Score · Morning Briefing
Portfolio Rebalancing · PDF Export · WhatsApp Alerts · Multi-Stock Comparison

Build it. Measure everything. Then apply.

================================================================================