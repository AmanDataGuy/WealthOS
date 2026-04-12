import os
from dotenv import load_dotenv
load_dotenv()

# ── Test 1: yfinance ──────────────────────────────────
def test_yfinance():
    import yfinance as yf
    stock = yf.Ticker("RELIANCE.NS")
    price = stock.info.get("currentPrice")
    print(f"✅ yfinance works — Reliance price: ₹{price}")

# ── Test 2: E2B ───────────────────────────────────────
def test_e2b():
    from e2b_code_interpreter import Sandbox
    sandbox = Sandbox()
    result = sandbox.run_code("print(2 + 2)")
    print(f"✅ E2B works — 2+2 = {result.logs.stdout[0].strip()}")
    sandbox.kill()

# ── Test 3: Firecrawl ─────────────────────────────────
def test_firecrawl():
    from firecrawl import Firecrawl
    app = Firecrawl(api_key=os.getenv("FIRECRAWL_API_KEY"))
    result = app.scrape("https://www.moneycontrol.com")
    print(f"✅ Firecrawl works — got {len(str(result))} characters")

# ── Run all tests ─────────────────────────────────────
if __name__ == "__main__":
    print("\n🔍 Running Phase 0 Tool Verification...\n")
    test_yfinance()
    test_e2b()
    test_firecrawl()
    print("\n✅ All tools verified!\n")