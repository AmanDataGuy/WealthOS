-- WealthOS — PostgreSQL schema initialisation
-- Run once against an empty database:
--   psql -h localhost -U wealthos -d wealthos -f scripts/init_db.sql
--
-- Safe to re-run: every statement uses IF NOT EXISTS / ON CONFLICT DO NOTHING.

-- ──────────────────────────────────────────────────────────────────────────────
--  1. transactions
--     Used by: finance_server.py  (get_transactions, analyze_spending, get_surplus)
--     Also written by: finance_agent.py  (save_transactions_to_db via HTTP)
-- ──────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS transactions (
    id          UUID          DEFAULT gen_random_uuid() PRIMARY KEY,
    user_id     UUID          NOT NULL,
    date        DATE          NOT NULL,
    description TEXT,
    amount      NUMERIC(15,2) NOT NULL,
    type        TEXT          NOT NULL CHECK (type IN ('credit', 'debit')),
    category    TEXT,
    source      TEXT          DEFAULT 'db',
    created_at  TIMESTAMPTZ   DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_transactions_user_date
    ON transactions(user_id, date DESC);


-- ──────────────────────────────────────────────────────────────────────────────
--  2. subscriptions
--     Used by: finance_server.py  (get_subscriptions)
-- ──────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS subscriptions (
    id           UUID          DEFAULT gen_random_uuid() PRIMARY KEY,
    user_id      UUID          NOT NULL,
    name         TEXT          NOT NULL,
    amount       NUMERIC(15,2) NOT NULL,
    frequency    TEXT          NOT NULL DEFAULT 'monthly'
                               CHECK (frequency IN ('monthly', 'weekly', 'yearly')),
    last_charged DATE,
    is_flagged   BOOLEAN       DEFAULT FALSE,
    created_at   TIMESTAMPTZ   DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_subscriptions_user
    ON subscriptions(user_id);


-- ──────────────────────────────────────────────────────────────────────────────
--  3. financial_goals
--     Used by: finance_server.py  (get_goals)
-- ──────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS financial_goals (
    id             UUID          DEFAULT gen_random_uuid() PRIMARY KEY,
    user_id        UUID          NOT NULL,
    name           TEXT          NOT NULL,
    target_amount  NUMERIC(15,2) NOT NULL,
    current_amount NUMERIC(15,2) DEFAULT 0,
    deadline_date  DATE,
    created_at     TIMESTAMPTZ   DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_financial_goals_user
    ON financial_goals(user_id);


-- ──────────────────────────────────────────────────────────────────────────────
--  4. emis
--     Used by: finance_server.py  (get_emis — checks table existence at runtime)
--     The server handles missing table gracefully, but create it here anyway.
-- ──────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS emis (
    id                  UUID          DEFAULT gen_random_uuid() PRIMARY KEY,
    user_id             UUID          NOT NULL,
    loan_name           TEXT          NOT NULL,
    lender              TEXT,
    principal_amount    NUMERIC(15,2),
    outstanding_balance NUMERIC(15,2),
    monthly_emi         NUMERIC(15,2) NOT NULL,
    interest_rate       NUMERIC(6,2),
    tenure_months       INTEGER,
    emi_date            INTEGER       CHECK (emi_date BETWEEN 1 AND 31),
    loan_type           TEXT          CHECK (loan_type IN ('home','car','personal','education','other')),
    is_active           BOOLEAN       DEFAULT TRUE,
    created_at          TIMESTAMPTZ   DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_emis_user
    ON emis(user_id, is_active);


-- ──────────────────────────────────────────────────────────────────────────────
--  5. financial_facts
--     Used by: data_agent.py  (fetch_from_db, compute_growth_metrics)
--     One row per (ticker, metric, fiscal_year).
--     Metrics: total_revenue, gross_profit, operating_income, net_income,
--              ebitda, total_assets, total_debt, cash_equivalents,
--              free_cash_flow, operating_cash_flow, eps_diluted
-- ──────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS financial_facts (
    id          SERIAL        PRIMARY KEY,
    ticker      TEXT          NOT NULL,
    metric      TEXT          NOT NULL,
    value       NUMERIC(22,4),
    fiscal_year INTEGER,
    source      TEXT          DEFAULT 'yfinance',
    updated_at  TIMESTAMPTZ   DEFAULT NOW(),
    UNIQUE (ticker, metric, fiscal_year)
);

CREATE INDEX IF NOT EXISTS idx_financial_facts_ticker_metric
    ON financial_facts(ticker, metric);


-- ──────────────────────────────────────────────────────────────────────────────
--  6. portfolio_holdings
--     Used by: portfolio_server.py  (get_holdings, add_holding, remove_holding)
--     Note: user_id is TEXT here, not UUID — portfolio_server passes it as plain
--     string (no UUID parsing). Keep consistent with how the server uses it.
-- ──────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS portfolio_holdings (
    id            SERIAL        PRIMARY KEY,
    user_id       TEXT          NOT NULL,
    ticker        TEXT          NOT NULL,
    quantity      NUMERIC(15,4) NOT NULL,
    avg_buy_price NUMERIC(15,4) NOT NULL,
    sector        TEXT          DEFAULT 'Unknown',
    asset_type    TEXT          DEFAULT 'equity',
    added_at      TIMESTAMPTZ   DEFAULT NOW(),
    UNIQUE (user_id, ticker)
);

CREATE INDEX IF NOT EXISTS idx_portfolio_holdings_user
    ON portfolio_holdings(user_id);


-- ──────────────────────────────────────────────────────────────────────────────
--  7. tracked_symbols
--     Used by: research_agent.py  (get_tracked_symbols)
-- ──────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS tracked_symbols (
    id       SERIAL      PRIMARY KEY,
    user_id  UUID        NOT NULL,
    symbol   TEXT        NOT NULL,
    added_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (user_id, symbol)
);

CREATE INDEX IF NOT EXISTS idx_tracked_symbols_user
    ON tracked_symbols(user_id);


-- ──────────────────────────────────────────────────────────────────────────────
--  Seed one test user so you can run a query immediately without inserting data.
--  UUID is hardcoded so it matches the example curl in setup.md.
-- ──────────────────────────────────────────────────────────────────────────────

INSERT INTO portfolio_holdings (user_id, ticker, quantity, avg_buy_price, sector, asset_type)
VALUES ('00000000-0000-0000-0000-000000000001', 'TCS.NS', 10, 3200.00, 'Technology', 'equity')
ON CONFLICT DO NOTHING;

INSERT INTO tracked_symbols (user_id, symbol)
VALUES ('00000000-0000-0000-0000-000000000001', 'TCS.NS')
ON CONFLICT DO NOTHING;
