import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def test():
    server = StdioServerParameters(
        command="python",
        args=["mcp_servers/news_server.py"]
    )

    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # Test 1 — search news
            print("\n--- search_news ---")
            result = await session.call_tool("search_news", {"query": "Reliance Industries", "days": 7})
            print(result.content[0].text[:500])

            # Test 2 — get headlines
            print("\n--- get_headlines ---")
            result = await session.call_tool("get_headlines", {"ticker": "RELIANCE.NS"})
            print(result.content[0].text[:500])

            # Test 3 — get sentiment
            print("\n--- get_sentiment ---")
            result = await session.call_tool("get_sentiment", {"ticker": "RELIANCE.NS"})
            print(result.content[0].text[:500])

            # Test 4 — reddit sentiment
            print("\n--- get_reddit_sentiment ---")
            result = await session.call_tool("get_reddit_sentiment", {"ticker": "Reliance Industries"})
            print(result.content[0].text[:500])

asyncio.run(test())