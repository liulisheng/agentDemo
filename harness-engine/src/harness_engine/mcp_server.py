"""Official MCP transport boundary for the trusted tool registry.

The registry remains usable without MCP installed.  Only ``start`` and
``serve_stdio`` require the optional official SDK; no protocol framing is
implemented in this module.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable, Mapping
from typing import Any

from .core import LifecycleEngine
from .tools import ToolError, ToolRegistry, build_tool_registry


class McpUnavailableError(RuntimeError):
    """Raised when the official MCP Python package is not installed."""


def stdio_command(enabled: Iterable[str]) -> tuple[str, ...]:
    """Build a shell-free command for this process's Python interpreter."""

    command = [sys.executable, "-m", "harness_engine.mcp_server"]
    for name in enabled:
        normalized = str(name).strip()
        if not normalized or any(character.isspace() for character in normalized):
            raise ValueError("enabled tool names must be nonblank and whitespace-free")
        command.extend(("--enable", normalized))
    return tuple(command)


def _load_mcp_server() -> Any:
    try:
        from mcp.server import Server
    except ImportError as exc:
        raise McpUnavailableError(
            "official MCP package is not installed; install harness-engine[mcp]"
        ) from exc
    return Server


class McpToolServer(LifecycleEngine):
    """Expose a ToolRegistry through the official MCP low-level server."""

    def __init__(self, registry: ToolRegistry, name: str = "harness-tools") -> None:
        super().__init__()
        if not name.strip():
            raise ValueError("MCP server name must not be blank")
        self.registry = registry
        self.name = name
        self._server: Any | None = None

    def list_tools(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            {
                "name": tool.name,
                "description": tool.description,
                "inputSchema": dict(tool.input_schema),
            }
            for tool in self.registry.list_tools()
        )

    def call_tool(self, name: str, arguments: Mapping[str, Any]) -> Any:
        return self.registry.invoke(name, arguments)

    def _start_impl(self) -> None:
        server_type = _load_mcp_server()
        self._server = server_type(
            self.name,
            on_list_tools=self._handle_list_tools,
            on_call_tool=self._handle_call_tool,
        )

    def _close_impl(self) -> None:
        self._server = None

    async def _handle_list_tools(self, _ctx: Any, _params: Any) -> Any:
        from mcp.types import ListToolsResult, Tool

        return ListToolsResult(
            tools=[
                Tool(
                    name=definition["name"],
                    description=definition["description"],
                    input_schema=definition["inputSchema"],
                )
                for definition in self.list_tools()
            ]
        )

    async def _handle_call_tool(self, _ctx: Any, params: Any) -> Any:
        from mcp.types import CallToolResult, TextContent

        name = str(params.name)
        arguments = params.arguments or {}
        try:
            result = self.call_tool(name, arguments)
            text = result if isinstance(result, str) else json.dumps(
                result,
                ensure_ascii=False,
                default=str,
            )
            return CallToolResult(
                content=[TextContent(type="text", text=text)],
            )
        except ToolError as exc:
            return CallToolResult(
                content=[TextContent(type="text", text=str(exc))],
                is_error=True,
            )

    def serve_stdio(self) -> None:
        """Run the already-started official MCP server over stdio."""

        self._require_started()
        if self._server is None:  # pragma: no cover - guarded by start()
            raise McpUnavailableError("MCP server is not initialized")
        try:
            import anyio
            from mcp.server.stdio import stdio_server
        except ImportError as exc:  # pragma: no cover - start already imports mcp
            raise McpUnavailableError(
                "official MCP stdio transport is unavailable"
            ) from exc

        async def run() -> None:
            async with stdio_server() as streams:
                await self._server.run(
                    streams[0],
                    streams[1],
                    self._server.create_initialization_options(),
                )

        anyio.run(run)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="harness-mcp-tools")
    parser.add_argument(
        "--enable",
        action="append",
        default=[],
        help="entry-point name to enable; may be repeated",
    )
    args = parser.parse_args(argv)
    registry = build_tool_registry(args.enable)
    server = McpToolServer(registry)
    try:
        server.start()
        server.serve_stdio()
    except McpUnavailableError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    finally:
        server.close()
    return 0


__all__ = ["McpToolServer", "McpUnavailableError", "main", "stdio_command"]


if __name__ == "__main__":  # pragma: no cover - process entry point
    raise SystemExit(main())
