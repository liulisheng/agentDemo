"""MCP Stdio Host 的真实进程边界契约测试。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import pytest

from agent_platform.domain.mcp import McpProfileStatus, McpServerProfile

try:
    import mcp  # noqa: F401
except ImportError:
    pytest.skip("需要安装固定版本的 MCP SDK 才能执行 Stdio 进程集成测试", allow_module_level=True)


_ECHO_SCHEMA = {
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
    "additionalProperties": False,
}
_EMPTY_SCHEMA = {"type": "object", "properties": {}, "additionalProperties": False}
_SERVER_SCRIPT = """
import asyncio
import json
import os
from pathlib import Path
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool

Path(os.environ["PID_FILE"]).write_text(str(os.getpid()), encoding="utf-8")
server = Server("test-mcp")
schemas = {
    "echo": {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
        "additionalProperties": False,
    },
    "secret_probe": {"type": "object", "properties": {}, "additionalProperties": False},
    "slow": {"type": "object", "properties": {}, "additionalProperties": False},
}

async def list_tools(_context, _params):
    return ListToolsResult(tools=[
        Tool(name="echo", description="回传文本。", input_schema=schemas["echo"]),
        Tool(name="secret_probe", description="检查凭据到达。", input_schema=schemas["secret_probe"]),
        Tool(name="slow", description="用于超时验证。", input_schema=schemas["slow"]),
    ])

async def call_tool(_context, params):
    if params.name == "echo":
        result = {"text": params.arguments["text"]}
    elif params.name == "secret_probe":
        result = {"present": os.environ.get("MCP_TEST_TOKEN") == "test-token"}
    else:
        await asyncio.sleep(10)
        result = {"completed": True}
    return CallToolResult(content=[TextContent(type="text", text=json.dumps(result))])

server = Server("test-mcp", on_list_tools=list_tools, on_call_tool=call_tool)

async def main():
    async with stdio_server() as streams:
        await server.run(streams[0], streams[1], server.create_initialization_options())

asyncio.run(main())
"""


def _catalog_digest() -> str:
    catalog = [
        {"name": "echo", "input_schema": _ECHO_SCHEMA},
        {"name": "secret_probe", "input_schema": _EMPTY_SCHEMA},
        {"name": "slow", "input_schema": _EMPTY_SCHEMA},
    ]
    return hashlib.sha256(
        json.dumps(catalog, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _profile(tmp_path: Path, *, digest: str | None = None, command: str | None = None) -> McpServerProfile:
    return McpServerProfile(
        platform_id="platform-1",
        tenant_id="tenant-1",
        profile_id="profile-1",
        version=1,
        name="test-mcp",
        transport="stdio",
        command=command or sys.executable,
        args=("-c", _SERVER_SCRIPT),
        working_directory_ref="workspace://tenant-1/test",
        environment_secret_refs={
            "PID_FILE": "secret://test/pid-file",
            "MCP_TEST_TOKEN": "secret://test/token",
        },
        enabled_tool_names=("echo", "secret_probe", "slow"),
        tool_catalog_sha256=digest or _catalog_digest(),
        startup_timeout_ms=5_000,
        tool_timeout_ms=5_000,
        status=McpProfileStatus.ACTIVE,
    )


class _Secrets:
    def __init__(self, pid_file: Path) -> None:
        self.pid_file = pid_file
        self.resolved: list[str] = []

    def resolve(self, secret_ref: str, *, tenant_id: str) -> str:
        assert tenant_id == "tenant-1"
        self.resolved.append(secret_ref)
        return {
            "secret://test/pid-file": str(self.pid_file),
            "secret://test/token": "test-token",
        }[secret_ref]


class _Directories:
    def __init__(self, path: Path) -> None:
        self.path = path

    def resolve(self, working_directory_ref: str, *, tenant_id: str) -> Path:
        assert working_directory_ref == "workspace://tenant-1/test"
        assert tenant_id == "tenant-1"
        return self.path


def _host(tmp_path: Path, *, digest: str | None = None, command: str | None = None, timeout_ms: int = 5_000):
    try:
        from agent_platform.adapters.mcp import stdio
    except ImportError as exc:
        pytest.fail(f"MCP Stdio Host is not implemented: {exc}")
    secrets = _Secrets(tmp_path / "server.pid")
    profile = _profile(tmp_path, digest=digest, command=command)
    profile = McpServerProfile(
        **{
            field: getattr(profile, field)
            for field in profile.__dataclass_fields__
            if field != "tool_timeout_ms"
        },
        tool_timeout_ms=timeout_ms,
    )
    host = stdio.McpStdioHost(
        profile,
        directory_resolver=_Directories(tmp_path),
        secret_resolver=secrets,
        allowed_commands=(sys.executable,),
        allowed_working_directory_roots=(tmp_path,),
    )
    return host, secrets, stdio


def _wait_for_pid(pid_file: Path) -> int:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if pid_file.exists():
            return int(pid_file.read_text(encoding="utf-8"))
        time.sleep(0.01)
    raise AssertionError("MCP 子进程未启动")


async def _wait_for_pid_async(pid_file: Path) -> int:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if pid_file.exists():
            return int(pid_file.read_text(encoding="utf-8"))
        await asyncio.sleep(0.01)
    raise AssertionError("MCP 子进程未启动")


def _assert_process_stopped(pid: int) -> None:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.01)
    pytest.fail(f"MCP 子进程 {pid} 未退出")


def test_stdio_host_uses_allowlisted_process_secret_refs_and_published_tools(tmp_path: Path) -> None:
    host, secrets, stdio = _host(tmp_path)

    async def run() -> None:
        async with host:
            assert tuple(tool.name for tool in host.tools) == ("echo", "secret_probe", "slow")
            echo = await host.call_tool("echo", {"text": "stdio-ok"})
            secret_probe = await host.call_tool("secret_probe", {})
            assert echo.content[0].text == '{"text": "stdio-ok"}'
            assert secret_probe.content[0].text == '{"present": true}'
            with pytest.raises(stdio.McpHostPolicyError):
                await host.call_tool("unpublished-tool", {})

    asyncio.run(run())
    assert len(secrets.resolved) == 2
    _assert_process_stopped(_wait_for_pid(tmp_path / "server.pid"))


def test_stdio_host_resolves_a_filtered_run_launch_without_starting_a_process(
    tmp_path: Path,
) -> None:
    host, _secrets, _stdio = _host(tmp_path)

    launch = host.resolve_launch_config()

    assert launch.command == Path(sys.executable)
    assert launch.args == ("-c", _SERVER_SCRIPT)
    assert launch.cwd == tmp_path.resolve()
    assert launch.environment["MCP_TEST_TOKEN"] == "test-token"
    assert "test-token" not in repr(launch)
    assert not (tmp_path / "server.pid").exists()


def test_stdio_host_rejects_a_tool_schema_drift_and_reaps_the_server(tmp_path: Path) -> None:
    host, _, stdio = _host(tmp_path, digest="0" * 64)

    async def run() -> None:
        with pytest.raises(stdio.McpCatalogDriftError):
            await host.start()

    asyncio.run(run())
    _assert_process_stopped(_wait_for_pid(tmp_path / "server.pid"))


def test_stdio_host_refuses_unapproved_executable_before_resolving_secrets(tmp_path: Path) -> None:
    host, secrets, stdio = _host(tmp_path, command="/bin/echo")

    async def run() -> None:
        with pytest.raises(stdio.McpHostPolicyError):
            await host.start()

    asyncio.run(run())
    assert secrets.resolved == []
    assert not (tmp_path / "server.pid").exists()


def test_stdio_host_timeout_terminates_the_mcp_process_tree(tmp_path: Path) -> None:
    host, _, stdio = _host(tmp_path, timeout_ms=50)

    async def run() -> None:
        await host.start()
        pid = _wait_for_pid(tmp_path / "server.pid")
        with pytest.raises(stdio.McpToolTimeoutError):
            await host.call_tool("slow", {})
        _assert_process_stopped(pid)

    asyncio.run(run())


def test_stdio_host_cancellation_cleans_up_the_child_process_tree(tmp_path: Path) -> None:
    host, _, _ = _host(tmp_path, timeout_ms=5_000)

    async def run() -> None:
        await host.start()
        pid = _wait_for_pid(tmp_path / "server.pid")
        asyncio.get_running_loop().call_later(0.05, asyncio.current_task().cancel)
        with pytest.raises(asyncio.CancelledError):
            await host.call_tool("slow", {})
        _assert_process_stopped(pid)

    asyncio.run(run())


def test_stdio_host_abort_cleans_up_when_the_run_lease_is_lost(tmp_path: Path) -> None:
    host, _, _ = _host(tmp_path)

    async def run() -> None:
        async def owner() -> None:
            try:
                async with host:
                    await host.call_tool("slow", {})
            except asyncio.CancelledError:
                return

        owner_task = asyncio.create_task(owner())
        pid = await _wait_for_pid_async(tmp_path / "server.pid")
        await asyncio.sleep(0.05)
        try:
            await host.abort()
            await owner_task
        finally:
            if not owner_task.done():
                owner_task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await owner_task
        assert not host.is_running
        _assert_process_stopped(pid)

    asyncio.run(run())
