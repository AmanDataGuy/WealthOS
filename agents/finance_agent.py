"""
================================================================================
  WealthOS v2.0 — Finance Agent
  agents/finance_agent.py
================================================================================

  The first agent that runs before any investment analysis.
  Answers one fundamental question before anything else:

      "Can you even afford to invest right now?"

  It builds a PersonalFinanceSnapshot — a complete picture of the user's
  financial health — that every other agent (Risk, Research, Writer) uses
  as context.

  WHY NO FRAMEWORK HERE
  ─────────────────────
  Frameworks like Agno / LangChain shine when an LLM needs to dynamically
  decide which tool to call next. The Finance Agent doesn't need that —
  it always runs the same 4 steps in the same order:

      fetch → parse → detect → score → snapshot

  Pure Python is simpler, faster, and has zero hidden dependencies.
  Agno will be used in the Research Agent where dynamic tool routing
  actually matters.

  WHAT IT DOES
  ────────────
    Step 1  →  Fetch / parse transactions  (DB or vision OCR)
    Step 2  →  Detect spending anomalies   (z-score, pure Python)
    Step 3  →  Compute health score        (weighted formula, pure Python)
    Step 4  →  Assemble final snapshot     (typed Pydantic output)

  COLD START LOGIC
  ────────────────
    No data at all       →  ask user for input      (confidence: none)
    Manual numbers only  →  rough snapshot          (confidence: low)
    Fresh upload         →  parse + save to DB      (confidence: medium)
    DB has history       →  full analysis           (confidence: high)

================================================================================
"""

# ── Standard Library ──────────────────────────────────────────────────────────
import os
import json
import statistics
from datetime import datetime, timezone
from typing import Optional

# ── Third-Party ───────────────────────────────────────────────────────────────
import httpx                             # HTTP calls to finance MCP
from pydantic import BaseModel, Field   # typed data models

# ── Config ────────────────────────────────────────────────────────────────────
FINANCE_MCP_URL = os.getenv("FINANCE_MCP_URL", "http://localhost:8001")


# ==============================================================================
#  SECTION 1 — Data Models
#
#  Every function in this file returns one of these typed shapes.
#  No raw dicts. No ambiguous strings.
#  If the data doesn't fit the schema, it fails loudly and immediately.
# ==============================================================================

class Transaction(BaseModel):
    """A single line item — one merchant, one amount, one moment in time."""

    merchant : str
    amount   : float
    date     : str                        # ISO format  YYYY-MM-DD
    category : str                        # food | transport | emi | salary | etc.
    source   : str = "db"                 # db | receipt | bank_statement


class AnomalyFlag(BaseModel):
    """Something looks off — spending in this category is unusually high."""

    category        : str
    expected_amount : float               # average for this category
    actual_amount   : float               # what we actually saw this time
    severity        : str                 # low | medium | high
    note            : str                 # plain-English explanation for the user


class HealthScore(BaseModel):
    """
    A 0–100 score across 5 dimensions of financial health.
    Think of it like a credit score — but for habits, not history.

    Dimensions & Weights  (blueprint Section 1.4)
    ┌──────────────────────┬────────┐
    │ Dimension            │ Weight │
    ├──────────────────────┼────────┤
    │ Savings Rate         │  30%   │
    │ Debt-to-Income       │  25%   │
    │ Expense Stability    │  20%   │
    │ Goal Progress        │  15%   │
    │ Emergency Fund       │  10%   │
    └──────────────────────┴────────┘
    """

    overall           : float             # 0 – 100
    grade             : str               # A / B / C / D / F

    savings_rate      : float             # sub-score 0–100
    debt_to_income    : float
    expense_stability : float
    goal_progress     : float
    emergency_fund    : float

    verdict           : str               # one-line human-readable summary


class PersonalFinanceSnapshot(BaseModel):
    """
    The final output of this agent.

    Every downstream agent — Risk, Research, Writer — reads this object
    before doing anything. Nothing gets invested without this being built first.
    The `data_confidence` field tells them how much to trust the numbers.
    """

    user_id          : str
    generated_at : str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


    # ── Core financials ───────────────────────────────────────────────────────
    monthly_income   : float = 0.0
    monthly_expenses : float = 0.0
    monthly_surplus  : float = 0.0       # income - expenses
    savings_rate_pct : float = 0.0       # surplus / income x 100

    # ── Transaction detail ────────────────────────────────────────────────────
    transactions     : list[Transaction]  = []
    anomalies        : list[AnomalyFlag]  = []
    top_categories   : dict[str, float]   = {}   # { category: total_spent }

    # ── Health ────────────────────────────────────────────────────────────────
    health_score     : Optional[HealthScore] = None

    # ── Confidence — downstream agents check this before trusting the data ────
    data_confidence  : str = "none"      # none | low | medium | high
    data_source      : str = "none"      # db | upload | manual

    # ── Status ────────────────────────────────────────────────────────────────
    status           : str = "ok"        # ok | insufficient_data | error
    message          : str = ""          # shown to the user when status != ok


# ==============================================================================
#  SECTION 2 — Tool Functions
#
#  Plain Python functions. Each does exactly one job.
#  No decorators needed — we call them directly in the orchestrator below.
#  Easy to unit test, easy to swap out, easy to read.
# ==============================================================================

# ── Step 1a — Scan a Receipt ───────────────────────────────────────────────────

def scan_receipt(image_path: str) -> Transaction:
    """
    ## Scan Receipt

    Receipt OCR requires a vision model — currently not configured.
    Returns a zero-value placeholder transaction.
    """
    print("[Finance Agent] Ollama not configured — skipping receipt OCR")
    return Transaction(
        merchant = "Unknown",
        amount   = 0.0,
        date     = datetime.now(timezone.utc).date().isoformat(),
        category = "other",
        source   = "receipt"
    )


# ── Step 1b — Parse a Bank Statement PDF ──────────────────────────────────────

def parse_bank_statement(pdf_path: str) -> list[Transaction]:
    """
    ## Parse Bank Statement

    Bank statement PDF parsing requires a vision model — currently not configured.
    Returns an empty list.
    """
    print("[Finance Agent] Ollama not configured — skipping bank statement parsing")
    return []



# ── Step 1c — Fetch from Postgres via finance_server MCP ──────────────────────

def get_transactions_from_db(user_id: str) -> list[Transaction]:
    """
    ## Fetch Transactions from Database

    Calls the finance_server MCP (built in Phase 1) to pull this user's
    stored transaction history from Postgres.

    Returns an empty list for new users — cold start logic handles that.
    """

    try:
        response = httpx.get(
            f"{FINANCE_MCP_URL}/transactions/{user_id}",
            timeout=10
        )
        if response.status_code != 200:
            return []

        rows = response.json().get("transactions", [])

        return [
            Transaction(
                merchant = row["merchant"],
                amount   = float(row["amount"]),
                date     = row["date"],
                category = row["category"],
                source   = "db"
            )
            for row in rows
        ]

    except httpx.RequestError:
        # MCP server not reachable — fail gracefully, don't crash the pipeline
        return []


# ── Step 1d — Save new transactions back to DB ─────────────────────────────────

def save_transactions_to_db(user_id: str, transactions: list[Transaction]) -> bool:
    """
    ## Save Transactions to Database

    After parsing an upload, write the new transactions back to Postgres
    so the next session has DB data and gets a high-confidence snapshot.
    """

    try:
        payload = {
            "user_id":      user_id,
            "transactions": [t.model_dump() for t in transactions]
        }
        response = httpx.post(
            f"{FINANCE_MCP_URL}/transactions",
            json=payload,
            timeout=10
        )
        return response.status_code == 200

    except httpx.RequestError:
        return False


# ── Step 2 — Detect Anomalies ─────────────────────────────────────────────────

def detect_anomalies(transactions: list[Transaction]) -> list[AnomalyFlag]:
    """
    ## Detect Spending Anomalies

    Finds unusual spending using z-score statistics — pure Python, no LLM.

    How it works:
    - Groups transactions by category
    - Computes mean and standard deviation per category
    - Flags anything more than 2 standard deviations above the mean
    - Assigns severity:  low (2–2.5 sigma)  |  medium (2.5–3 sigma)  |  high (3+ sigma)

    Needs at least 3 data points per category to compute meaningful stats.
    Salary entries are skipped — income spikes are not anomalies.
    """

    anomalies    = []
    by_category  : dict[str, list[float]] = {}

    for txn in transactions:
        if txn.category == "salary":
            continue
        by_category.setdefault(txn.category, []).append(txn.amount)

    for category, amounts in by_category.items():

        if len(amounts) < 3:
            continue                           # not enough data for reliable stats

        mean  = statistics.mean(amounts)
        stdev = statistics.stdev(amounts)

        if stdev == 0:
            continue                           # all amounts identical — nothing to flag

        for amount in amounts:
            z = (amount - mean) / stdev

            if z < 2.0:
                continue                       # within normal range, all good

            severity = "high" if z >= 3.0 else "medium" if z >= 2.5 else "low"

            anomalies.append(AnomalyFlag(
                category        = category,
                expected_amount = round(mean, 2),
                actual_amount   = round(amount, 2),
                severity        = severity,
                note            = (
                    f"Spent Rs.{amount:.0f} in {category} — "
                    f"Rs.{amount - mean:.0f} above your usual average of Rs.{mean:.0f}"
                )
            ))

    return anomalies


# ── Step 3 — Compute Health Score ─────────────────────────────────────────────

def compute_health_score(
    monthly_income   : float,
    monthly_expenses : float,
    transactions     : list[Transaction],
    target_savings   : float = 0.0,
    emergency_months : float = 0.0
) -> HealthScore:
    """
    ## Compute Financial Health Score

    Scores the user across 5 dimensions using blueprint Section 1.4 weights.
    Pure math — no LLM involved. Fast and deterministic.
    """

    surplus = monthly_income - monthly_expenses

    # ── Savings Rate (30%) ────────────────────────────────────────────────────
    # Target: saving 20%+ of income. Below 5% is a red flag.
    savings_pct   = (surplus / monthly_income * 100) if monthly_income > 0 else 0
    savings_score = min(100, max(0, (savings_pct / 20) * 100))

    # ── Debt-to-Income (25%) ──────────────────────────────────────────────────
    # Total EMI as % of income. Under 30% is healthy. Above 50% is dangerous.
    emi_total  = sum(t.amount for t in transactions if t.category == "emi")
    dti_pct    = (emi_total / monthly_income * 100) if monthly_income > 0 else 0
    dti_score  = min(100, max(0, (1 - dti_pct / 50) * 100))

    # ── Expense Stability (20%) ───────────────────────────────────────────────
    # Are monthly expenses predictable? High variance = low score.
    by_month: dict[str, float] = {}
    for txn in transactions:
        if txn.category != "salary":
            month = txn.date[:7]              # group by YYYY-MM
            by_month[month] = by_month.get(month, 0) + txn.amount

    if len(by_month) >= 2:
        monthly_totals  = list(by_month.values())
        avg_monthly     = statistics.mean(monthly_totals)
        std_monthly     = statistics.stdev(monthly_totals)
        coeff_variation = (std_monthly / avg_monthly) if avg_monthly > 0 else 1
        stability_score = min(100, max(0, (1 - coeff_variation) * 100))
    else:
        stability_score = 50                  # not enough months — assume neutral

    # ── Goal Progress (15%) ───────────────────────────────────────────────────
    # Are they actually hitting their savings target each month?
    goal_score = min(100, (surplus / target_savings) * 100) if target_savings > 0 else 50

    # ── Emergency Fund (10%) ──────────────────────────────────────────────────
    # 6 months of expenses covered = full marks. 0 months = zero.
    emergency_score = min(100, (emergency_months / 6) * 100)

    # ── Weighted Final Score ───────────────────────────────────────────────────
    overall = round(
        savings_score   * 0.30 +
        dti_score       * 0.25 +
        stability_score * 0.20 +
        goal_score      * 0.15 +
        emergency_score * 0.10,
        1
    )

    grade = (
        "A" if overall >= 80 else
        "B" if overall >= 65 else
        "C" if overall >= 50 else
        "D" if overall >= 35 else
        "F"
    )

    verdicts = {
        "A": "Excellent financial health. You have a strong base to invest from.",
        "B": "Good shape. A few optimisations could free up more capital.",
        "C": "Moderate health. Address your top spending anomaly before investing.",
        "D": "Needs attention. Focus on reducing EMI burden and building surplus.",
        "F": "Critical. Investment is not advisable until financial health improves.",
    }

    return HealthScore(
        overall           = overall,
        grade             = grade,
        savings_rate      = round(savings_score,   1),
        debt_to_income    = round(dti_score,        1),
        expense_stability = round(stability_score,  1),
        goal_progress     = round(goal_score,       1),
        emergency_fund    = round(emergency_score,  1),
        verdict           = verdicts[grade]
    )


# ── Step 4 — Assemble Snapshot ────────────────────────────────────────────────

def _build_snapshot(
    user_id          : str,
    transactions     : list[Transaction],
    data_source      : str,
    data_confidence  : str,
    manual_income    : float = 0.0,
    target_savings   : float = 0.0,
    emergency_months : float = 0.0
) -> PersonalFinanceSnapshot:
    """
    Internal helper — takes a flat list of transactions and builds
    the full PersonalFinanceSnapshot. Called by run_finance_agent()
    once the right data path has been chosen.
    """

    # Derive income and expenses from transactions
    income   = sum(t.amount for t in transactions if t.category == "salary")
    expenses = sum(t.amount for t in transactions if t.category != "salary")

    # Fall back to manual figure if transaction data has no salary entries
    if income == 0 and manual_income > 0:
        income = manual_income

    surplus      = income - expenses
    savings_pct  = (surplus / income * 100) if income > 0 else 0.0

    # Top 5 spending categories
    category_totals: dict[str, float] = {}
    for txn in transactions:
        if txn.category != "salary":
            category_totals[txn.category] = category_totals.get(txn.category, 0) + txn.amount

    top_categories = dict(
        sorted(category_totals.items(), key=lambda x: x[1], reverse=True)[:5]
    )

    anomalies = detect_anomalies(transactions)

    score = compute_health_score(
        monthly_income   = income,
        monthly_expenses = expenses,
        transactions     = transactions,
        target_savings   = target_savings,
        emergency_months = emergency_months
    )

    return PersonalFinanceSnapshot(
        user_id          = user_id,
        monthly_income   = round(income,      2),
        monthly_expenses = round(expenses,    2),
        monthly_surplus  = round(surplus,     2),
        savings_rate_pct = round(savings_pct, 2),
        transactions     = transactions,
        anomalies        = anomalies,
        top_categories   = top_categories,
        health_score     = score,
        data_confidence  = data_confidence,
        data_source      = data_source,
        status           = "ok"
    )


# ==============================================================================
#  SECTION 3 — Orchestrator  (replaces the Agno agent)
#
#  Pure Python. Calls the tool functions above in the right order.
#  No framework, no hidden dependencies, no magic.
#  In Phase 4, LangGraph calls run_finance_agent() as one node.
# ==============================================================================

def run_finance_agent(
    user_id          : str,
    uploads          : list[str] = [],    # file paths — receipts or PDFs
    manual_income    : float     = 0.0,   # fallback if no transaction data
    manual_expenses  : float     = 0.0,
    target_savings   : float     = 0.0,   # user's monthly savings goal
    emergency_months : float     = 0.0    # months of expenses in emergency fund
) -> PersonalFinanceSnapshot:
    """
    ## Run Finance Agent

    The single entry point for this agent.
    Determines which data path to follow, runs the 4-step pipeline,
    and returns a fully typed PersonalFinanceSnapshot.

    ─────────────────────────────────────────────────────────────────────────
    Usage:

        # Existing user — reads from DB
        snapshot = run_finance_agent("user_123")

        # New user uploading a bank statement
        snapshot = run_finance_agent("user_123", uploads=["statement.pdf"])

        # New user uploading receipts
        snapshot = run_finance_agent("user_123", uploads=["grocery.jpg", "fuel.png"])

        # Fallback — manual numbers only
        snapshot = run_finance_agent("user_123", manual_income=80000, manual_expenses=55000)

    ─────────────────────────────────────────────────────────────────────────
    In Phase 4, LangGraph calls this as a single node:

        def finance_node(state: WealthOSState) -> WealthOSState:
            snapshot = run_finance_agent(state["user_id"], state["uploads"])
            return {**state, "personal_finance": snapshot}
    ─────────────────────────────────────────────────────────────────────────
    """

    print(f"\n[Finance Agent] Starting for user: {user_id}")

    # ── Cold Start Check ──────────────────────────────────────────────────────
    db_transactions = get_transactions_from_db(user_id)

    has_db_data = len(db_transactions) > 0
    has_uploads = len(uploads) > 0
    has_manual  = manual_income > 0

    print(f"[Finance Agent] DB transactions : {len(db_transactions)}")
    print(f"[Finance Agent] Uploads         : {len(uploads)}")
    print(f"[Finance Agent] Manual income   : {has_manual}")

    # ── Path A — No data at all ───────────────────────────────────────────────
    if not has_db_data and not has_uploads and not has_manual:
        print("[Finance Agent] Cold start — no data available")
        return PersonalFinanceSnapshot(
            user_id         = user_id,
            status          = "insufficient_data",
            data_confidence = "none",
            data_source     = "none",
            message         = (
                "No transaction history found. "
                "Please upload a bank statement PDF or at least 3 months of receipts "
                "to get your full financial analysis."
            )
        )

    # ── Path B — Manual numbers only — rough snapshot ─────────────────────────
    if not has_db_data and not has_uploads and has_manual:
        print("[Finance Agent] Manual input path")

        surplus     = manual_income - manual_expenses
        savings_pct = (surplus / manual_income * 100) if manual_income > 0 else 0

        score = compute_health_score(
            monthly_income   = manual_income,
            monthly_expenses = manual_expenses,
            transactions     = [],
            target_savings   = target_savings,
            emergency_months = emergency_months
        )

        return PersonalFinanceSnapshot(
            user_id          = user_id,
            monthly_income   = manual_income,
            monthly_expenses = manual_expenses,
            monthly_surplus  = surplus,
            savings_rate_pct = round(savings_pct, 2),
            health_score     = score,
            data_confidence  = "low",
            data_source      = "manual",
            status           = "ok",
            message          = (
                "Analysis based on manually entered figures. "
                "Upload bank statements for transaction-level insights."
            )
        )

    # ── Path C — Uploads or DB data — run full pipeline ───────────────────────
    upload_transactions: list[Transaction] = []

    if has_uploads:
        print(f"[Finance Agent] Parsing {len(uploads)} upload(s)")

        for file_path in uploads:
            ext = file_path.lower().split(".")[-1]

            if ext == "pdf":
                print(f"[Finance Agent]   Parsing PDF       : {file_path}")
                upload_transactions.extend(parse_bank_statement(file_path))

            elif ext in ("jpg", "jpeg", "png", "webp"):
                print(f"[Finance Agent]   Scanning receipt  : {file_path}")
                upload_transactions.append(scan_receipt(file_path))

            else:
                print(f"[Finance Agent]   Skipping unsupported file: {file_path}")

        # Save to DB so next session is high-confidence
        if upload_transactions:
            saved = save_transactions_to_db(user_id, upload_transactions)
            print(f"[Finance Agent] Saved {len(upload_transactions)} transactions to DB: {saved}")

    # Merge DB history + newly parsed uploads
    all_transactions = db_transactions + upload_transactions

    confidence  = "high" if has_db_data else "medium"
    data_source = "db"   if has_db_data else "upload"

    print(f"[Finance Agent] Total transactions : {len(all_transactions)}")
    print(f"[Finance Agent] Confidence         : {confidence}")

    return _build_snapshot(
        user_id          = user_id,
        transactions     = all_transactions,
        data_source      = data_source,
        data_confidence  = confidence,
        manual_income    = manual_income,
        target_savings   = target_savings,
        emergency_months = emergency_months
    )


# ==============================================================================
#  Quick Test — run directly to verify everything loads and math works
#  python -m agents.finance_agent
# ==============================================================================

if __name__ == "__main__":

    print("\n" + "=" * 62)
    print("  WealthOS — Finance Agent  |  Startup Check")
    print("=" * 62)

    # Simulate a new user with no DB data — manual input path
    snapshot = run_finance_agent(
        user_id          = "test_user_001",
        manual_income    = 85000,
        manual_expenses  = 62000,
        target_savings   = 15000,
        emergency_months = 2.5
    )

    print(f"\n  {'User':<20}: {snapshot.user_id}")
    print(f"  {'Income':<20}: Rs.{snapshot.monthly_income:>10,.0f}")
    print(f"  {'Expenses':<20}: Rs.{snapshot.monthly_expenses:>10,.0f}")
    print(f"  {'Surplus':<20}: Rs.{snapshot.monthly_surplus:>10,.0f}")
    print(f"  {'Savings Rate':<20}: {snapshot.savings_rate_pct:>9.1f}%")
    print(f"  {'Data Source':<20}: {snapshot.data_source}")
    print(f"  {'Confidence':<20}: {snapshot.data_confidence}")

    if snapshot.health_score:
        hs = snapshot.health_score
        print(f"\n  {'Health Score':<20}: {hs.overall} / 100  [ Grade {hs.grade} ]")
        print(f"  {'  Savings Rate':<20}: {hs.savings_rate}")
        print(f"  {'  Debt-to-Income':<20}: {hs.debt_to_income}")
        print(f"  {'  Stability':<20}: {hs.expense_stability}")
        print(f"  {'  Goal Progress':<20}: {hs.goal_progress}")
        print(f"  {'  Emergency Fund':<20}: {hs.emergency_fund}")
        print(f"\n  Verdict : {hs.verdict}")

    print(f"\n  {'Status':<20}: {snapshot.status}")
    if snapshot.message:
        print(f"  {'Message':<20}: {snapshot.message}")

    print("\n" + "=" * 62 + "\n")