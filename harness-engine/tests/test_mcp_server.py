import pytest

from harness_engine import mcp_server
from harness_engine.core import EngineState
from harness_engine.mcp_server import (
    McpToolServer,
    McpUnavailableError,
    stdio_command,
)
from harness_engine.tools import ToolDefinition, ToolRegistry


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="echo",
            description="echo text",
            input_schema={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
            handler=lambda arguments: {"text": arguments["text"]},
        )
    )
    return registry


def test_mcp_server_lists_and_delegates_to_registry():
    server = McpToolServer(_registry())

    assert server.list_tools() == (
        {
            "name": "echo",
            "description": "echo text",
            "inputSchema": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        },
    )
    assert server.call_tool("echo", {"text": "hello"}) == {"text": "hello"}


def test_mcp_server_reports_missing_optional_dependency_without_state_change(
    monkeypatch,
):
    server = McpToolServer(_registry())

    def missing_dependency():
        raise McpUnavailableError("official MCP package is not installed")

    monkeypatch.setattr("harness_engine.mcp_server._load_mcp_server", missing_dependency)

    with pytest.raises(McpUnavailableError, match="not installed"):
        server.start()

    assert server.state is EngineState.CREATED


def test_mcp_server_lifecycle_is_idempotent_with_loaded_sdk(monkeypatch):
    class FakeSdkServer:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    monkeypatch.setattr(
        "harness_engine.mcp_server._load_mcp_server",
        lambda: FakeSdkServer,
    )
    server = McpToolServer(_registry())

    server.start()
    server.start()
    assert server.state is EngineState.STARTED
    server.close()
    server.close()
    assert server.state is EngineState.CLOSED


def test_mcp_main_uses_explicit_tool_allowlist(monkeypatch):
    calls = []

    class FakeProcess:
        def __init__(self, registry):
            self.registry = registry

        def start(self):
            calls.append(("start", self.registry.allowlist))

        def serve_stdio(self):
            calls.append(("serve", tuple(tool.name for tool in self.registry.list_tools())))

        def close(self):
            calls.append(("close",))

    monkeypatch.setattr(mcp_server, "McpToolServer", FakeProcess)

    assert mcp_server.main(["--enable", "echo"]) == 0
    assert calls == [
        ("start", ("echo",)),
        ("serve", ("echo",)),
        ("close",),
    ]


def test_stdio_command_uses_current_python_and_explicit_allowlist(monkeypatch):
    monkeypatch.setattr(mcp_server.sys, "executable", r"C:\Python312\python.exe")

    assert stdio_command(("echo", "other")) == (
        r"C:\Python312\python.exe",
        "-m",
        "harness_engine.mcp_server",
        "--enable",
        "echo",
        "--enable",
        "other",
    )
