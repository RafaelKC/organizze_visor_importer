import asyncio
from types import SimpleNamespace

import pytest

from visorsync.mcp_clients.base import BaseMcpClient, RateLimitError


class _FakeSession:
    def __init__(self, calls: list[int]):
        self._calls = calls

    async def call_tool(self, name, arguments):
        self._calls.append(1)
        text = "You've reached the limit of 200 changes per hour through the assistant. Try again in a few minutes."
        content = SimpleNamespace(text=text)
        return SimpleNamespace(isError=False, content=[content])


def test_rate_limit_response_raises_immediately_without_retrying():
    client = BaseMcpClient("https://example.invalid/mcp", lambda: "token")
    calls: list[int] = []
    client.session = _FakeSession(calls)

    async def run():
        with pytest.raises(RateLimitError):
            await client.call_tool("create_manual_transaction", {})

    asyncio.run(run())
    assert len(calls) == 1  # no retry loop burned on a rate-limit response
