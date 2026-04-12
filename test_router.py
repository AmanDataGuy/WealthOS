# test_router.py
import asyncio
from input.router import InputRouter

async def test():
    router = InputRouter()

    # test text input
    result = await router.route("text", {"query": "Should I buy Reliance stock?"})
    print(f"Source: {result.source}")
    print(f"Query: {result.query}")

asyncio.run(test())