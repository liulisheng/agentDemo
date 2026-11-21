"""使用官方 MCP Stdio 客户端管理受控的本地 MCP 子进程。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Self

from agent_platform.domain.mcp import McpProfileStatus, McpServerProfile
from agent_platform.ports.mcp import McpSecretResolver, McpWorkingDirectoryResolver


class McpHostError(RuntimeError):
    """MCP 子进程初始化或工具调用的稳定错误基类。"""


class McpHostPolicyError(McpHostError):
    """启动参数、工具权限或工作目录违反 Worker 策略。"""


class McpHostStartupError(McpHostError):
    """MCP Stdio 服务未能在启动时限内完成握手。"""


class McpCatalogDriftError(McpHostError):
    """服务端工具目录或输入 Schema 与已发布 Profile 不一致。"""


class McpToolTimeoutError(McpHostError):
    """MCP 工具调用超过 Profile 的单次时限。"""


class McpSdkUnavailableError(McpHostError):
    """Worker 镜像没有安装固定版本的官方 MCP SDK。"""


@dataclass(frozen=True, slots=True)
class McpToolDefinition:
    """Worker 允许传给 Runtime 的 MCP 工具定义。"""

    name: str
    description: str
    input_schema: Mapping[str, Any]


@dataclass(frozen=True, slots=True, repr=False)
class McpStdioLaunchConfig:
    """由 Worker 完成 allowlist/SecretRef 校验后的单 Run Stdio 启动参数。"""

    command: Path
    args: tuple[str, ...]
    cwd: Path
    environment: Mapping[str, str]

    def __repr__(self) -> str:
        return (
            "McpStdioLaunchConfig("
            f"command={str(self.command)!r}, cwd={str(self.cwd)!r}, "
            f"args_count={len(self.args)}, environment=<redacted>)"
        )


def compute_tool_catalog_sha256(tools: Sequence[McpToolDefinition]) -> str:
    """按名称排序后，仅对工具名与输入 Schema 计算稳定摘要。"""

    catalog = [
        {"name": tool.name, "input_schema": dict(tool.input_schema)}
        for tool in sorted(tools, key=lambda item: item.name)
    ]
    encoded = json.dumps(
        catalog,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class McpStdioHost:
    """以固定 Profile、命令白名单和 cwd 根目录启动一个 MCP Stdio 服务。"""

    def __init__(
        self,
        profile: McpServerProfile,
        *,
        directory_resolver: McpWorkingDirectoryResolver,
        secret_resolver: McpSecretResolver,
        allowed_commands: Sequence[str | Path],
        allowed_working_directory_roots: Sequence[str | Path],
    ) -> None:
        self._profile = profile
        self._directory_resolver = directory_resolver
        self._secret_resolver = secret_resolver
        self._allowed_commands = frozenset(
            self._command_path(command)
            for command in allowed_commands
        )
        self._allowed_cwd_roots = tuple(
            self._resolve_existing_path(root, "允许的 MCP 工作目录根")
            for root in allowed_working_directory_roots
        )
        self._stack: AsyncExitStack | None = None
        self._session: Any | None = None
        self._tools: Mapping[str, McpToolDefinition] = MappingProxyType({})
        self._closed = True
        self._owner_task: asyncio.Task[Any] | None = None
        self._close_lock = asyncio.Lock()

    def __repr__(self) -> str:
        return (
            "McpStdioHost("
            f"profile_id={self._profile.profile_id!r}, "
            f"version={self._profile.version}, "
            f"enabled_tool_names={self._profile.enabled_tool_names!r}, "
            "args=<redacted>, environment=<redacted>)"
        )

    @property
    def tools(self) -> tuple[McpToolDefinition, ...]:
        """返回 Profile 明确启用且 Schema 已校验的工具目录。"""

        return tuple(self._tools[name] for name in sorted(self._tools))

    @property
    def is_running(self) -> bool:
        return not self._closed and self._session is not None

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def start(self) -> None:
        """验证 Profile 后启动服务，并在限定时间内核对 MCP tools/list。"""

        current_task = asyncio.current_task()
        if current_task is None:
            raise McpHostPolicyError("MCP Stdio Host 必须在 Worker async Run 任务中启动")
        if self.is_running:
            self._require_owner_task(current_task)
            return
        launch = self.resolve_launch_config()
        self._owner_task = current_task
        try:
            await asyncio.wait_for(
                self._initialize(launch.command, launch.cwd, launch.environment),
                timeout=self._profile.startup_timeout_ms / 1000,
            )
        except TimeoutError as exc:
            await self._close_shielded()
            raise McpHostStartupError("MCP Stdio 服务启动或目录核对超时") from exc
        except McpHostError:
            await self._close_shielded()
            raise
        except asyncio.CancelledError:
            await self._close_shielded()
            raise
        except Exception as exc:
            await self._close_shielded()
            raise McpHostStartupError("MCP Stdio 服务启动或握手失败") from exc

    def resolve_launch_config(self) -> McpStdioLaunchConfig:
        """在不启动子进程的情况下解析并校验单 Run 的 Profile 启动参数。"""

        if self._profile.status is not McpProfileStatus.ACTIVE:
            raise McpHostPolicyError("MCP Profile 不是 ACTIVE 状态")
        command = self._validate_command(self._profile.command)
        cwd = self._resolve_cwd(self._profile.working_directory_ref)
        environment = self._resolve_environment()
        return McpStdioLaunchConfig(
            command=command,
            args=self._profile.args,
            cwd=cwd,
            environment=environment,
        )

    async def call_tool(self, name: str, arguments: Mapping[str, Any]) -> Any:
        """只调用目录摘要已校验且由当前 Profile 启用的工具。"""

        if not self.is_running or self._session is None:
            raise McpHostPolicyError("MCP Stdio 服务尚未启动或已关闭")
        self._require_owner_task(asyncio.current_task())
        if name not in self._tools:
            raise McpHostPolicyError("工具不在当前 MCP Profile 的启用目录中")
        if not isinstance(arguments, Mapping):
            raise McpHostPolicyError("MCP 工具参数必须是 JSON 对象")
        try:
            return await asyncio.wait_for(
                self._session.call_tool(name, dict(arguments)),
                timeout=self._profile.tool_timeout_ms / 1000,
            )
        except TimeoutError as exc:
            await self._close_shielded()
            raise McpToolTimeoutError("MCP 工具调用超时，服务进程已清理") from exc
        except asyncio.CancelledError:
            await self._close_shielded()
            raise

    async def abort(self) -> None:
        """Run 取消或 Worker 租约丢失时取消 owner task，由其回收进程树。"""

        owner_task = self._owner_task
        if owner_task is not None and owner_task is not asyncio.current_task():
            if not owner_task.done():
                owner_task.cancel("MCP Run lease lost")
            return
        await self.close()

    async def close(self) -> None:
        """关闭官方 MCP 会话；SDK 在 POSIX 进程组/Windows Job Object 回收子树。"""

        if self._stack is not None:
            self._require_owner_task(asyncio.current_task())
        async with self._close_lock:
            stack = self._stack
            self._stack = None
            self._session = None
            self._tools = MappingProxyType({})
            self._closed = True
            self._owner_task = None
            if stack is not None:
                await stack.aclose()

    async def _close_shielded(self) -> None:
        # close 必须在进入官方 MCP task group 的同一任务和 cancel scope 中运行；
        # asyncio.shield 会切换任务，嵌套 AnyIO shield 又会破坏其 cancel scope 顺序。
        await self.close()

    async def _initialize(
        self, command: Path, cwd: Path, environment: Mapping[str, str]
    ) -> None:
        try:
            from mcp import ClientSession
            from mcp.client.stdio import StdioServerParameters, stdio_client
        except ImportError as exc:
            raise McpSdkUnavailableError(
                "Worker 必须安装 MCP Stdio 可选依赖"
            ) from exc

        stack = AsyncExitStack()
        self._stack = stack
        self._closed = False
        await stack.__aenter__()
        try:
            streams = await stack.enter_async_context(
                stdio_client(
                    StdioServerParameters(
                        command=str(command),
                        args=list(self._profile.args),
                        env=dict(environment),
                        cwd=cwd,
                    )
                )
            )
            session = await stack.enter_async_context(ClientSession(*streams))
            self._session = session
            await session.initialize()
            result = await session.list_tools()
            self._tools = self._validate_catalog(result.tools)
        except BaseException:
            await self.close()
            raise

    def _validate_command(self, command: str) -> Path:
        path = self._command_path(command)
        if path not in self._allowed_commands:
            raise McpHostPolicyError("MCP 可执行文件不在 Worker allowlist 中")
        if not path.is_file() or not os.access(path, os.X_OK):
            raise McpHostPolicyError("MCP 可执行文件不可执行")
        return path

    def _resolve_cwd(self, reference: str) -> Path:
        try:
            cwd = self._resolve_existing_path(
                self._directory_resolver.resolve(
                    reference, tenant_id=self._profile.tenant_id
                ),
                "MCP 工作目录",
            )
        except Exception as exc:
            raise McpHostPolicyError("MCP 工作目录引用无法解析") from exc
        if not cwd.is_dir() or not any(
            cwd == root or root in cwd.parents for root in self._allowed_cwd_roots
        ):
            raise McpHostPolicyError("MCP 工作目录超出 Worker allowlist")
        return cwd

    def _resolve_environment(self) -> Mapping[str, str]:
        values: dict[str, str] = {}
        for name, secret_ref in self._profile.environment_secret_refs.items():
            try:
                value = self._secret_resolver.resolve(
                    secret_ref, tenant_id=self._profile.tenant_id
                )
            except Exception:  # noqa: BLE001 — SecretProvider 异常可能携带 SecretRef 或凭据上下文
                raise McpHostPolicyError("MCP 环境 SecretRef 解析失败") from None
            if not isinstance(value, str) or "\x00" in value:
                raise McpHostPolicyError("MCP 环境 SecretRef 返回了非法值")
            values[name] = value
        return MappingProxyType(values)

    def _validate_catalog(self, discovered: Sequence[Any]) -> Mapping[str, McpToolDefinition]:
        by_name: dict[str, Any] = {}
        for item in discovered:
            name = getattr(item, "name", None)
            schema = getattr(item, "input_schema", None)
            if schema is None:
                schema = getattr(item, "inputSchema", None)
            if not isinstance(name, str) or not isinstance(schema, Mapping):
                raise McpCatalogDriftError("MCP tools/list 返回了非法工具定义")
            if name in by_name:
                raise McpCatalogDriftError("MCP tools/list 返回了重复工具名")
            by_name[name] = item

        missing = set(self._profile.enabled_tool_names) - set(by_name)
        if missing:
            raise McpCatalogDriftError("MCP 工具目录缺少 Profile 启用的工具")

        selected: dict[str, McpToolDefinition] = {}
        for name in self._profile.enabled_tool_names:
            item = by_name[name]
            schema = getattr(item, "input_schema", None)
            if schema is None:
                schema = getattr(item, "inputSchema", None)
            try:
                schema = json.loads(json.dumps(schema, ensure_ascii=False, sort_keys=True))
            except (TypeError, ValueError) as exc:
                raise McpCatalogDriftError("MCP 工具 Schema 不是合法 JSON") from exc
            selected[name] = McpToolDefinition(
                name=name,
                description=str(getattr(item, "description", "") or ""),
                input_schema=MappingProxyType(schema),
            )
        if compute_tool_catalog_sha256(tuple(selected.values())) != self._profile.tool_catalog_sha256:
            raise McpCatalogDriftError("MCP 工具名称或输入 Schema 摘要与 Profile 不一致")
        return MappingProxyType(selected)

    def _require_owner_task(self, task: asyncio.Task[Any] | None) -> None:
        """ContextManager 由 MCP SDK 绑定进入任务，跨任务关闭会破坏其 cancel scope。"""

        if task is None or task is not self._owner_task:
            raise McpHostPolicyError("MCP Stdio Host 必须由启动它的 Worker Run 任务使用")

    @staticmethod
    def _command_path(value: str | Path) -> Path:
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise McpHostPolicyError("MCP 可执行文件必须使用绝对路径")
        # 保留虚拟环境解释器的符号链接；它可能与系统 Python 使用不同依赖集。
        path = Path(os.path.abspath(path))
        if not path.is_file():
            raise McpHostPolicyError("MCP 可执行文件不存在")
        return path

    @staticmethod
    def _resolve_existing_path(value: str | Path, description: str) -> Path:
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise McpHostPolicyError(f"{description}必须使用绝对路径")
        try:
            return path.resolve(strict=True)
        except OSError as exc:
            raise McpHostPolicyError(f"{description}不存在或无法解析") from exc


__all__ = [
    "McpCatalogDriftError",
    "McpHostError",
    "McpHostPolicyError",
    "McpHostStartupError",
    "McpSdkUnavailableError",
    "McpStdioHost",
    "McpStdioLaunchConfig",
    "McpToolDefinition",
    "McpToolTimeoutError",
    "compute_tool_catalog_sha256",
]
