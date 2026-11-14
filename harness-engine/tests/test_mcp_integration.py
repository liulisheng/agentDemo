import pytest

mcp = pytest.importorskip("mcp")
anyio = pytest.importorskip("anyio")

from harness_engine.mcp_server import McpToolServer
from harness_engine.tools import build_tool_registry


def test_official_mcp_client_discovers_and_calls_echo():
    server = McpToolServer(build_tool_registry(("echo",)))
    server.start()

    async def check():
        async with mcp.Client(server._server) as client:
            tools = await client.list_tools()
            result = await client.call_tool("echo", {"text": "mcp-ok"})
            assert [tool.name for tool in tools.tools] == ["echo"]
            assert [item.text for item in result.content] == ['{"text": "mcp-ok"}']

    try:
        anyio.run(check)
    finally:
        server.close()
