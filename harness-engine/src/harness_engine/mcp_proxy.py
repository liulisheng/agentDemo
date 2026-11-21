"""按单个 Run allowlist 过滤上游 MCP Stdio 工具目录和调用。"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
from collections.abc import Mapping, Sequence
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any


class McpProxyPolicyError(RuntimeError):
    """Profile 目录漂移或 Run 工具权限不匹配。"""


@dataclass(frozen=True, slots=True)
class McpProxyConfig:
    """仅含非密钥 Stdio 参数的单 Run proxy 配置。"""

    command: str
    args: tuple[str, ...]
    cwd: str
    profile_tool_names: tuple[str, ...]
    run_allowed_tool_names: tuple[str, ...]
    profile_catalog_sha256: str
    expected_tool_schema_sha256: Mapping[str, str]
    environment_names: tuple[str, ...] = ()
    startup_timeout_ms: int = 10_000
    tool_timeout_ms: int = 60_000

    def __post_init__(self) -> None:
        if not Path(self.command).is_absolute():
            raise ValueError("MCP proxy command must be absolute")
        if not Path(self.cwd).is_absolute():
            raise ValueError("MCP proxy cwd must be absolute")
        if any(not isinstance(argument, str) or "\x00" in argument for argument in self.args):
            raise ValueError("MCP proxy arguments contain invalid text")
        profile_names = tuple(self.profile_tool_names)
        allowed_names = tuple(self.run_allowed_tool_names)
        if not profile_names or len(set(profile_names)) != len(profile_names):
            raise ValueError("MCP proxy profile tool names must be nonempty and unique")
        if len(set(allowed_names)) != len(allowed_names):
            raise ValueError("MCP proxy Run allowlist must be unique")
        if not set(allowed_names) <= set(profile_names):
            raise ValueError("MCP proxy Run allowlist must be a subset of its Profile")
        for digest in (self.profile_catalog_sha256, *self.expected_tool_schema_sha256.values()):
            if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
                raise ValueError("MCP proxy digests must be lowercase SHA-256 values")
        if set(self.expected_tool_schema_sha256) != set(allowed_names):
            raise ValueError("MCP proxy must pin a schema digest for every Run tool")
        environment_names = tuple(self.environment_names)
        if len(set(environment_names)) != len(environment_names) or any(
            re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) is None
            for name in environment_names
        ):
            raise ValueError("MCP proxy environment variable names are invalid")
        if self.startup_timeout_ms <= 0 or self.tool_timeout_ms <= 0:
            raise ValueError("MCP proxy timeouts must be positive")
        object.__setattr__(self, "profile_tool_names", profile_names)
        object.__setattr__(self, "run_allowed_tool_names", allowed_names)
        object.__setattr__(self, "environment_names", environment_names)
        object.__setattr__(
            self,
            "expected_tool_schema_sha256",
            MappingProxyType(dict(self.expected_tool_schema_sha256)),
        )

    def require_allowed(self, name: str) -> None:
        """在触达上游服务前再次执行当前 Run 工具 allowlist。"""

        if name not in self.run_allowed_tool_names:
            raise McpProxyPolicyError("MCP tool is outside this Run allowlist")

    def to_public_dict(self) -> dict[str, object]:
        """返回可写入 Run 临时配置的字段；不包含 SecretRef 或凭据值。"""

        return {
            "command": self.command,
            "args": list(self.args),
            "cwd": self.cwd,
            "profile_tool_names": list(self.profile_tool_names),
            "run_allowed_tool_names": list(self.run_allowed_tool_names),
            "profile_catalog_sha256": self.profile_catalog_sha256,
            "expected_tool_schema_sha256": dict(self.expected_tool_schema_sha256),
            "environment_names": list(self.environment_names),
            "startup_timeout_ms": self.startup_timeout_ms,
            "tool_timeout_ms": self.tool_timeout_ms,
        }

    @classmethod
    def from_public_dict(cls, value: Mapping[str, object]) -> McpProxyConfig:
        """从受控临时配置恢复 proxy 启动参数。"""

        expected = value.get("expected_tool_schema_sha256")
        if not isinstance(expected, Mapping):
            raise TypeError("MCP proxy expected schema digest map is invalid")
        return cls(
            command=str(value.get("command", "")),
            args=tuple(value.get("args", ())),
            cwd=str(value.get("cwd", "")),
            profile_tool_names=tuple(value.get("profile_tool_names", ())),
            run_allowed_tool_names=tuple(value.get("run_allowed_tool_names", ())),
            profile_catalog_sha256=str(value.get("profile_catalog_sha256", "")),
            expected_tool_schema_sha256={str(key): str(digest) for key, digest in expected.items()},
            environment_names=tuple(value.get("environment_names", ())),
            startup_timeout_ms=int(value.get("startup_timeout_ms", 10_000)),
            tool_timeout_ms=int(value.get("tool_timeout_ms", 60_000)),
        )


@dataclass(frozen=True, slots=True)
class McpProxyTool:
    """经过 Profile 摘要校验且通过 Run allowlist 的工具定义。"""

    name: str
    description: str
    input_schema: Mapping[str, object]


@dataclass(frozen=True, slots=True, repr=False)
class McpProxyLaunch:
    """单 Run proxy 的非敏感配置与短生命周期 Secret 环境值。"""

    server_name: str
    config: McpProxyConfig
    environment: Mapping[str, str] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if re.fullmatch(r"[A-Za-z0-9_-]{1,32}", self.server_name) is None:
            raise ValueError("MCP proxy server_name is invalid")
        values = dict(self.environment)
        if set(values) != set(self.config.environment_names):
            raise ValueError("MCP proxy environment aliases must match its Profile")
        if any(not isinstance(value, str) or "\x00" in value for value in values.values()):
            raise ValueError("MCP proxy environment values contain invalid text")
        object.__setattr__(self, "environment", MappingProxyType(values))

    def __repr__(self) -> str:
        return (
            "McpProxyLaunch("
            f"server_name={self.server_name!r}, profile_tools={self.config.profile_tool_names!r}, "
            "environment=<redacted>)"
        )


def _schema_sha256(schema: Mapping[str, object]) -> str:
    canonical = json.dumps(
        dict(schema),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def schema_sha256(schema: Mapping[str, object]) -> str:
    """返回与 Profile/ToolVersion 相同规范 JSON 的 Schema 摘要。"""

    return _schema_sha256(schema)


def _catalog_sha256(tools: Sequence[McpProxyTool]) -> str:
    catalog = [
        {"name": tool.name, "input_schema": dict(tool.input_schema)}
        for tool in sorted(tools, key=lambda item: item.name)
    ]
    canonical = json.dumps(
        catalog,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def filter_tool_catalog(
    discovered: Sequence[Any], config: McpProxyConfig
) -> tuple[McpProxyTool, ...]:
    """校验 Profile 全目录，再只暴露固定 Run allowlist 中的 Schema。"""

    by_name: dict[str, McpProxyTool] = {}
    for item in discovered:
        name = getattr(item, "name", None)
        schema = getattr(item, "input_schema", None)
        if schema is None:
            schema = getattr(item, "inputSchema", None)
        if not isinstance(name, str) or not isinstance(schema, Mapping):
            raise McpProxyPolicyError("MCP tools/list returned an invalid tool definition")
        if name in by_name:
            raise McpProxyPolicyError("MCP tools/list returned duplicate tool names")
        try:
            normalized_schema = json.loads(
                json.dumps(schema, ensure_ascii=False, sort_keys=True, allow_nan=False)
            )
        except (TypeError, ValueError) as error:
            raise McpProxyPolicyError("MCP tool input schema is not valid JSON") from error
        if not isinstance(normalized_schema, dict):
            raise McpProxyPolicyError("MCP tool input schema must be an object")
        by_name[name] = McpProxyTool(
            name=name,
            description=str(getattr(item, "description", "") or ""),
            input_schema=MappingProxyType(normalized_schema),
        )

    missing_profile_tools = set(config.profile_tool_names) - set(by_name)
    if missing_profile_tools:
        raise McpProxyPolicyError("MCP Profile tool catalog is incomplete")
    profile_tools = tuple(by_name[name] for name in config.profile_tool_names)
    if _catalog_sha256(profile_tools) != config.profile_catalog_sha256:
        raise McpProxyPolicyError("MCP Profile catalog digest changed")

    visible: list[McpProxyTool] = []
    for name in config.run_allowed_tool_names:
        tool = by_name[name]
        if _schema_sha256(tool.input_schema) != config.expected_tool_schema_sha256[name]:
            raise McpProxyPolicyError("MCP Run tool schema digest changed")
        visible.append(tool)
    return tuple(visible)


class McpStdioProxy:
    """使用官方 MCP SDK 代理上游 Stdio Server，并在模型边界过滤工具。"""

    def __init__(self, config: McpProxyConfig) -> None:
        self.config = config
        self._session: Any | None = None
        self._tools: tuple[McpProxyTool, ...] = ()

    async def serve_stdio(self) -> None:
        try:
            from mcp import ClientSession
            from mcp.client.stdio import StdioServerParameters, stdio_client
            from mcp.server import Server
            from mcp.server.stdio import stdio_server
            from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool
        except ImportError as error:
            raise McpProxyPolicyError("official MCP SDK is not installed") from error

        environment: dict[str, str] = {}
        for name in self.config.environment_names:
            value = os.environ.get(name)
            if value is None or "\x00" in value:
                raise McpProxyPolicyError("an MCP Run environment value is unavailable")
            environment[name] = value

        async with AsyncExitStack() as stack:
            streams = await asyncio.wait_for(
                stack.enter_async_context(
                    stdio_client(
                        StdioServerParameters(
                            command=self.config.command,
                            args=list(self.config.args),
                            env=environment,
                            cwd=self.config.cwd,
                        )
                    )
                ),
                timeout=self.config.startup_timeout_ms / 1000,
            )
            session = await stack.enter_async_context(ClientSession(*streams))
            self._session = session
            await asyncio.wait_for(
                session.initialize(), timeout=self.config.startup_timeout_ms / 1000
            )
            discovered = await asyncio.wait_for(
                session.list_tools(), timeout=self.config.startup_timeout_ms / 1000
            )
            self._tools = filter_tool_catalog(discovered.tools, self.config)

            async def list_tools(_context: Any, _params: Any) -> Any:
                return ListToolsResult(
                    tools=[
                        Tool(
                            name=tool.name,
                            description=tool.description,
                            input_schema=dict(tool.input_schema),
                        )
                        for tool in self._tools
                    ]
                )

            async def call_tool(_context: Any, params: Any) -> Any:
                name = str(getattr(params, "name", ""))
                try:
                    self.config.require_allowed(name)
                except McpProxyPolicyError:
                    return CallToolResult(
                        content=[TextContent(type="text", text="工具不在当前 Run allowlist 中")],
                        is_error=True,
                    )
                arguments = getattr(params, "arguments", None) or {}
                if not isinstance(arguments, Mapping):
                    return CallToolResult(
                        content=[TextContent(type="text", text="工具参数必须是 JSON 对象")],
                        is_error=True,
                    )
                try:
                    return await asyncio.wait_for(
                        session.call_tool(name, dict(arguments)),
                        timeout=self.config.tool_timeout_ms / 1000,
                    )
                except TimeoutError:
                    return CallToolResult(
                        content=[TextContent(type="text", text="MCP 工具调用超时")],
                        is_error=True,
                    )

            server = Server(
                "run-scoped-mcp-proxy",
                on_list_tools=list_tools,
                on_call_tool=call_tool,
            )
            async with stdio_server() as streams:
                await server.run(
                    streams[0],
                    streams[1],
                    server.create_initialization_options(),
                )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="harness-mcp-proxy")
    parser.add_argument("--config", required=True)
    args = parser.parse_args(argv)
    try:
        value = json.loads(Path(args.config).read_text(encoding="utf-8"))
        if not isinstance(value, Mapping):
            raise TypeError("MCP proxy config must be a JSON object")
        config = McpProxyConfig.from_public_dict(value)
        import anyio

        anyio.run(McpStdioProxy(config).serve_stdio)
        return 0
    except Exception:  # noqa: BLE001 — 子进程错误可能携带参数或密钥，stderr 只输出稳定错误。
        print("Run-scoped MCP Stdio proxy failed", file=sys.stderr)
        return 2


__all__ = [
    "McpProxyConfig",
    "McpProxyLaunch",
    "McpProxyPolicyError",
    "McpProxyTool",
    "McpStdioProxy",
    "filter_tool_catalog",
    "main",
    "schema_sha256",
]


if __name__ == "__main__":  # pragma: no cover - executed as a Stdio child process
    raise SystemExit(main())
