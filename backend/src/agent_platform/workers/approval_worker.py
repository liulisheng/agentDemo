"""消费已批准的单次 WRITE ToolVersion 并维护不确定结果边界。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_platform.adapters.mcp.stdio import (
    McpCatalogDriftError,
    McpHostError,
    McpStdioHost,
    McpToolDefinition,
    McpToolTimeoutError,
)
from agent_platform.domain.catalog import (
    LifecycleStatus,
    ToolVersion,
    compute_tool_input_schema_sha256,
)
from agent_platform.domain.common import DomainError
from agent_platform.domain.identity import IdentityContext
from agent_platform.domain.mcp import (
    McpProfileStatus,
    McpRiskLevel,
    McpServerProfile,
    ToolApprovalRequest,
    ToolApprovalState,
)
from agent_platform.ports.catalog_repository import CatalogRepository
from agent_platform.ports.mcp import McpRepository
from agent_platform.ports.storage import ObjectStore, UploadRequest

logger = logging.getLogger(__name__)


class ApprovalActionWorker:
    """租约保护的异步审批动作执行器；任何写调用不确定时都进入 UNKNOWN。"""

    def __init__(
        self,
        identity: IdentityContext,
        worker_id: str,
        repository: McpRepository,
        catalog: CatalogRepository,
        object_store: ObjectStore,
        *,
        host_factory: Callable[[IdentityContext, McpServerProfile], McpStdioHost],
        lease_seconds: int = 30,
        heartbeat_interval_seconds: float | None = None,
    ) -> None:
        if identity.auth_source != "worker":
            raise ValueError("ApprovalActionWorker 必须使用 Worker 身份")
        if not worker_id.strip() or lease_seconds < 3:
            raise ValueError("worker_id 必须非空且 lease_seconds 至少为 3")
        self.identity = identity
        self.worker_id = worker_id
        self.repository = repository
        self.catalog = catalog
        self.object_store = object_store
        self.host_factory = host_factory
        self.lease_seconds = lease_seconds
        self.heartbeat_interval_seconds = (
            heartbeat_interval_seconds
            if heartbeat_interval_seconds is not None
            else lease_seconds / 3
        )
        if self.heartbeat_interval_seconds <= 0 or self.heartbeat_interval_seconds >= lease_seconds:
            raise ValueError("heartbeat_interval_seconds 必须小于 lease_seconds")

    async def execute_one(self) -> ToolApprovalState | None:
        """原子领取并执行一个 APPROVED 动作；UNKNOWN 动作永远不会重新领取。"""

        request = self.repository.claim_approved_tool_approval(
            self.identity,
            self.worker_id,
            lease_seconds=self.lease_seconds,
        )
        if request is None:
            return None

        owner_task = asyncio.current_task()
        if owner_task is None:
            raise RuntimeError("Approval Worker 必须运行在 asyncio Task 中")
        stop_heartbeat = asyncio.Event()
        lease_lost = asyncio.Event()
        host_ref: list[McpStdioHost | None] = [None]
        heartbeat_task = asyncio.create_task(
            self._heartbeat(request, owner_task, host_ref, stop_heartbeat, lease_lost)
        )
        state = ToolApprovalState.FAILED
        error_code: str | None = "APPROVAL_ACTION_SETUP_FAILED"
        result_ref: str | None = None
        result_sha256: str | None = None
        call_started = False
        cancellation_received = False

        try:
            profile = self.repository.get_profile(
                self.identity,
                request.mcp_server_profile_id,
                request.mcp_server_profile_version,
            )
            tool = self.catalog.get_tool_version(
                self.identity, request.tool_id, request.tool_version
            )
            self._validate_binding(request, profile, tool)
            arguments = self._load_json_object(
                request.arguments_ref,
                request.arguments_sha256,
                "工具参数",
                "APPROVAL_ARGUMENTS_MISMATCH",
            )
            self._load_json_object(
                request.preview_ref,
                request.preview_sha256,
                "用户预览",
                "APPROVAL_PREVIEW_MISMATCH",
            )

            host = self.host_factory(self.identity, profile)
            host_ref[0] = host
            await host.start()
            self._validate_live_tool(host.tools, tool)
            call_started = True
            result = await host.call_tool(tool.remote_tool_name or "", arguments)
            result_ref, result_sha256 = self._store_result(request, result)
            if lease_lost.is_set() or self._is_error_result(result):
                state = ToolApprovalState.UNKNOWN
                error_code = "MCP_WRITE_OUTCOME_UNKNOWN"
            else:
                state = ToolApprovalState.SUCCEEDED
                error_code = None
        except McpToolTimeoutError:
            state = ToolApprovalState.UNKNOWN
            error_code = "MCP_WRITE_OUTCOME_UNKNOWN"
        except asyncio.CancelledError:
            cancellation_received = not lease_lost.is_set()
            state = ToolApprovalState.UNKNOWN if call_started else ToolApprovalState.FAILED
            error_code = (
                "MCP_WRITE_OUTCOME_UNKNOWN"
                if call_started or lease_lost.is_set()
                else "WORKER_CANCELLED_BEFORE_WRITE"
            )
        except Exception as error:  # noqa: BLE001 — MCP/存储驱动异常不能证明远端没有执行
            # MCP 服务异常发生在发送写请求后时，不能假设远端没有执行。
            state = ToolApprovalState.UNKNOWN if call_started else ToolApprovalState.FAILED
            logger.error(
                "审批动作执行收敛为 %s，异常类型=%s",
                state.value,
                type(error).__name__,
            )
            error_code = (
                "MCP_WRITE_OUTCOME_UNKNOWN"
                if call_started
                else self._setup_error_code(error)
            )
        finally:
            stop_heartbeat.set()
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
            if host_ref[0] is not None:
                try:
                    await host_ref[0].close()
                except McpHostError:
                    if call_started:
                        state = ToolApprovalState.UNKNOWN
                        error_code = "MCP_WRITE_OUTCOME_UNKNOWN"
            finished = self.repository.finish_tool_approval(
                self.identity,
                request.approval_id,
                self.worker_id,
                request.fencing_token,
                state,
                now=datetime.now(UTC),
                error_code=error_code,
                result_ref=result_ref,
                result_sha256=result_sha256,
            )
            if not finished:
                state = ToolApprovalState.UNKNOWN

        if cancellation_received:
            raise asyncio.CancelledError
        return state

    async def _heartbeat(
        self,
        request: ToolApprovalRequest,
        owner_task: asyncio.Task[Any],
        host_ref: list[McpStdioHost | None],
        stop: asyncio.Event,
        lease_lost: asyncio.Event,
    ) -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(
                    stop.wait(), timeout=self.heartbeat_interval_seconds
                )
                return
            except TimeoutError:
                pass
            try:
                renewed = self.repository.heartbeat_tool_approval(
                    self.identity,
                    request.approval_id,
                    self.worker_id,
                    request.fencing_token,
                    lease_seconds=self.lease_seconds,
                )
            except Exception:  # noqa: BLE001 — 续租失败时必须立即停止可能的写调用
                renewed = False
            if renewed:
                continue
            lease_lost.set()
            if host_ref[0] is not None:
                try:
                    await host_ref[0].abort()
                except Exception:  # noqa: BLE001 — 取消 Host 失败仍必须取消 owner Run task
                    if not owner_task.done():
                        owner_task.cancel("Approval action lease lost")
            elif not owner_task.done():
                owner_task.cancel("Approval action lease lost")
            return

    def _validate_binding(
        self,
        request: ToolApprovalRequest,
        profile: McpServerProfile,
        tool: ToolVersion,
    ) -> None:
        if profile.status is not McpProfileStatus.ACTIVE:
            raise DomainError("INVALID_STATE", "MCP Profile 已停用")
        if tool.status is not LifecycleStatus.PUBLISHED or tool.protocol != "mcp":
            raise DomainError("INVALID_STATE", "审批绑定的 MCP ToolVersion 不可执行")
        if tool.mcp_risk_level is not McpRiskLevel.WRITE:
            raise DomainError("INVALID_STATE", "审批动作必须绑定 WRITE ToolVersion")
        if (
            tool.mcp_server_profile_id != request.mcp_server_profile_id
            or tool.mcp_server_profile_version != request.mcp_server_profile_version
            or profile.profile_id != request.mcp_server_profile_id
            or profile.version != request.mcp_server_profile_version
            or tool.remote_tool_name is None
            or tool.remote_tool_name not in profile.enabled_tool_names
            or tool.input_schema_sha256 is None
        ):
            raise DomainError("MCP_CATALOG_DRIFT", "审批绑定的 Tool/Profile 版本已变化")

    @staticmethod
    def _validate_live_tool(
        definitions: tuple[McpToolDefinition, ...], tool: ToolVersion
    ) -> None:
        definition = next(
            (item for item in definitions if item.name == tool.remote_tool_name), None
        )
        if definition is None:
            raise McpCatalogDriftError("MCP Profile 未发现审批绑定的写工具")
        if compute_tool_input_schema_sha256(definition.input_schema) != tool.input_schema_sha256:
            raise McpCatalogDriftError("审批绑定的写工具 Schema 摘要已变化")

    def _load_json_object(
        self,
        object_ref: str,
        expected_sha256: str,
        label: str,
        digest_error_code: str,
    ) -> dict[str, object]:
        content = self.object_store.read(
            object_ref, self.identity.platform_id, self.identity.tenant_id
        )
        if hashlib.sha256(content).hexdigest() != expected_sha256:
            raise DomainError(digest_error_code, f"{label}摘要已变化")
        try:
            value = json.loads(content)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise DomainError("INVALID_ARGUMENT", f"{label}不是合法 JSON") from error
        if not isinstance(value, dict):
            raise DomainError("INVALID_ARGUMENT", f"{label}必须是 JSON object")
        return value

    def _store_result(
        self, request: ToolApprovalRequest, result: Any
    ) -> tuple[str, str]:
        if hasattr(result, "model_dump"):
            data = result.model_dump(mode="json", by_alias=True, exclude_none=True)
        elif isinstance(result, Mapping):
            data = dict(result)
        else:
            raise DomainError("INVALID_STATE", "MCP 工具返回值不是可序列化的 JSON")
        payload = json.dumps(
            data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        digest = hashlib.sha256(payload).hexdigest()
        upload = self.object_store.create_upload_intent(
            UploadRequest(
                platform_id=request.platform_id,
                tenant_id=request.tenant_id,
                filename=f"approval-{request.approval_id}-result.json",
                mime_type="application/json",
                size_bytes=len(payload),
                retention_policy="conversation",
                expected_sha256=digest,
            )
        )
        asset = self.object_store.complete_upload(upload.upload_id, payload)
        return asset.object_ref, asset.sha256

    @staticmethod
    def _is_error_result(result: Any) -> bool:
        return bool(getattr(result, "is_error", getattr(result, "isError", False)))

    @staticmethod
    def _setup_error_code(error: Exception) -> str:
        if isinstance(error, McpCatalogDriftError):
            return "MCP_CATALOG_DRIFT"
        if isinstance(error, McpHostError):
            return "MCP_HOST_SETUP_FAILED"
        if isinstance(error, DomainError):
            return error.code
        return "APPROVAL_ACTION_SETUP_FAILED"


def main() -> int:
    """以独立租户 Worker 消费已批准动作；缺少 MCP allowlist 时不会启动外部进程。"""

    import logging
    import os
    import signal
    import socket

    from agent_platform.adapters.mcp.environment import (
        EnvironmentMcpSecretResolver,
        EnvironmentMcpWorkingDirectoryResolver,
    )
    from agent_platform.api.app import create_app_from_environment

    logging.basicConfig(
        level=os.environ.get("AGENT_PLATFORM_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if os.environ.get("AGENT_PLATFORM_MODE") != "mysql":
        raise RuntimeError("Approval Worker 必须使用 MySQL 模式")
    platform_id = os.environ.get("AGENT_PLATFORM_ID", "").strip()
    tenant_id = os.environ.get("AGENT_PLATFORM_TENANT_ID", "").strip()
    if not platform_id or not tenant_id:
        raise RuntimeError("Approval Worker 缺少平台或租户标识")
    worker_id = os.environ.get(
        "AGENT_PLATFORM_APPROVAL_WORKER_ID", socket.gethostname()
    ).strip()
    if not worker_id:
        raise RuntimeError("Approval Worker 缺少 Worker 标识")

    def path_allowlist(environment_name: str) -> tuple[str, ...]:
        try:
            values = json.loads(os.environ.get(environment_name, "[]"))
        except json.JSONDecodeError as error:
            raise RuntimeError(f"{environment_name} 必须是 JSON 路径数组") from error
        if not isinstance(values, list) or any(
            not isinstance(value, str) or not Path(value).is_absolute()
            for value in values
        ):
            raise RuntimeError(f"{environment_name} 必须是绝对路径 JSON 数组")
        return tuple(values)

    allowed_commands = path_allowlist("AGENT_PLATFORM_MCP_ALLOWED_COMMANDS")
    allowed_cwd_roots = path_allowlist(
        "AGENT_PLATFORM_MCP_ALLOWED_WORKING_DIRECTORY_ROOTS"
    )
    app = create_app_from_environment()
    control_plane = app.state.control_plane_service
    if control_plane.persistence is None or control_plane.object_store is None:
        raise RuntimeError("Approval Worker 缺少 MySQL 仓储或对象存储")
    if control_plane.mcp_repository is None:
        raise RuntimeError("Approval Worker 缺少 MCP Repository")

    def host_factory(identity: IdentityContext, profile: McpServerProfile) -> McpStdioHost:
        """每次批准动作只按固定 Profile 创建一个受限的 Stdio Host。"""

        return McpStdioHost(
            profile,
            directory_resolver=EnvironmentMcpWorkingDirectoryResolver(),
            secret_resolver=EnvironmentMcpSecretResolver(),
            allowed_commands=allowed_commands,
            allowed_working_directory_roots=allowed_cwd_roots,
        )

    identity = IdentityContext(
        platform_id,
        tenant_id,
        worker_id,
        frozenset(),
        auth_source="worker",
    )
    worker = ApprovalActionWorker(
        identity,
        worker_id,
        control_plane.mcp_repository,
        control_plane.catalog.repository,
        control_plane.object_store,
        host_factory=host_factory,
        lease_seconds=int(os.environ.get("AGENT_PLATFORM_APPROVAL_LEASE_SECONDS", "30")),
    )
    poll_seconds = float(os.environ.get("AGENT_PLATFORM_APPROVAL_POLL_SECONDS", "1"))

    async def _run() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for termination_signal in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(termination_signal, stop.set)
            except NotImplementedError:
                pass
        async with app.router.lifespan_context(app):
            while not stop.is_set():
                try:
                    state = await worker.execute_one()
                except Exception as error:  # noqa: BLE001 — 故障不应携带工具参数进入日志
                    logging.getLogger(__name__).error(
                        "Approval Worker 本轮未完成，错误类别=%s", type(error).__name__
                    )
                    state = None
                if state is not None or stop.is_set():
                    continue
                try:
                    await asyncio.wait_for(stop.wait(), timeout=poll_seconds)
                except TimeoutError:
                    pass

    asyncio.run(_run())
    return 0


__all__ = ["ApprovalActionWorker", "main"]
