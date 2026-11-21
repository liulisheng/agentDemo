"""版本化 MCP Stdio Profile 与一次性工具审批的租户仓储端口。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Protocol

from agent_platform.domain.execution import RuntimeReference, UsageRecord
from agent_platform.domain.identity import IdentityContext
from agent_platform.domain.mcp import (
    McpProfileStatus,
    McpServerProfile,
    ToolApprovalRequest,
    ToolApprovalState,
)
from agent_platform.domain.observability import TraceSpan


class McpSecretResolver(Protocol):
    """以租户上下文短时解析一个环境变量 SecretRef。"""

    def resolve(self, secret_ref: str, *, tenant_id: str) -> str:
        """返回传给单个 MCP 子进程的凭据值，不得写入日志或持久化。"""
        ...


class McpWorkingDirectoryResolver(Protocol):
    """将租户范围的工作目录引用解析为本机目录。"""

    def resolve(self, working_directory_ref: str, *, tenant_id: str) -> str | Path:
        """返回本机路径；调用方还必须将路径约束在 Worker allowlist 根目录内。"""
        ...


class McpRepository(Protocol):
    """为应用服务提供带租户边界的 Profile 和 Approval 持久化。"""

    def save_profile(self, identity: IdentityContext, profile: McpServerProfile) -> None:
        """以新不可变版本保存 Profile 草稿。"""
        ...

    def set_profile_status(
        self,
        identity: IdentityContext,
        profile_id: str,
        version: int,
        status: McpProfileStatus,
    ) -> McpServerProfile:
        """显式发布或停用一个 Profile 版本。"""
        ...

    def get_profile(
        self, identity: IdentityContext, profile_id: str, version: int
    ) -> McpServerProfile: ...

    def list_profiles(self, identity: IdentityContext) -> tuple[McpServerProfile, ...]: ...

    def create_tool_approval(
        self,
        identity: IdentityContext,
        request: ToolApprovalRequest,
        *,
        run_fencing_token: int,
        trace_spans: tuple[TraceSpan, ...] = (),
        usage: UsageRecord | None = None,
        runtime_reference: RuntimeReference | None = None,
        result_ref: str | None = None,
    ) -> ToolApprovalRequest:
        """原子持久化执行轨迹、审批请求与 Runtime 结果并暂停对应 Run。"""
        ...

    def get_tool_approval(
        self, identity: IdentityContext, approval_id: str
    ) -> ToolApprovalRequest: ...

    def approve_tool_approval(
        self,
        identity: IdentityContext,
        approval_id: str,
        preview_sha256: str,
        *,
        idempotency_key: str,
        now: datetime,
    ) -> ToolApprovalRequest: ...

    def reject_tool_approval(
        self, identity: IdentityContext, approval_id: str, *, now: datetime
    ) -> ToolApprovalRequest: ...

    def expire_tool_approval(
        self, identity: IdentityContext, approval_id: str, *, now: datetime
    ) -> ToolApprovalRequest: ...

    def claim_approved_tool_approval(
        self,
        identity: IdentityContext,
        worker_id: str,
        *,
        lease_seconds: int = 30,
    ) -> ToolApprovalRequest | None: ...

    def heartbeat_tool_approval(
        self,
        identity: IdentityContext,
        approval_id: str,
        worker_id: str,
        fencing_token: int,
        *,
        lease_seconds: int = 30,
    ) -> bool:
        """只有当前动作 owner 的 fencing token 可以续租。"""
        ...

    def finish_tool_approval(
        self,
        identity: IdentityContext,
        approval_id: str,
        worker_id: str,
        fencing_token: int,
        state: ToolApprovalState,
        *,
        now: datetime,
        error_code: str | None = None,
        result_ref: str | None = None,
        result_sha256: str | None = None,
    ) -> bool: ...
