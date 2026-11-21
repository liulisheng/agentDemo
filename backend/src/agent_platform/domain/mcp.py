"""租户范围 MCP Stdio 配置与一次性工具审批领域对象。"""

from __future__ import annotations

import hmac
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import StrEnum
from types import MappingProxyType

from .common import DomainError
from .identity import IdentityContext


def _text(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ValueError(f"{name} must be nonblank text")
    return value.strip()


def _digest(value: str, name: str) -> str:
    normalized = _text(value, name).lower()
    if len(normalized) != 64 or any(
        char not in "0123456789abcdef" for char in normalized
    ):
        raise ValueError(f"{name} must be a SHA-256 hex digest")
    return normalized


def _timestamp(value: datetime, name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")


class McpProfileStatus(StrEnum):
    """MCP Server Profile 的版本状态。"""

    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class McpRiskLevel(StrEnum):
    """MCP 工具在 Agent 模型侧可见的执行风险等级。"""

    READ = "READ"
    PREVIEW = "PREVIEW"
    WRITE = "WRITE"


class ToolApprovalState(StrEnum):
    """一次性工具审批及其 Worker 动作状态。"""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class McpServerProfile:
    """固定租户 Stdio 启动配置和工具目录，不保存凭据值。"""

    platform_id: str
    tenant_id: str
    profile_id: str
    version: int
    name: str
    transport: str
    command: str
    args: tuple[str, ...]
    working_directory_ref: str
    enabled_tool_names: tuple[str, ...]
    tool_catalog_sha256: str
    environment_secret_refs: Mapping[str, str] = field(default_factory=dict, repr=False)
    startup_timeout_ms: int = 10_000
    tool_timeout_ms: int = 60_000
    status: McpProfileStatus = McpProfileStatus.DRAFT

    def __post_init__(self) -> None:
        for name in (
            "platform_id",
            "tenant_id",
            "profile_id",
            "name",
            "command",
            "working_directory_ref",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        if self.transport != "stdio":
            raise ValueError("transport must be stdio")
        if self.version < 1:
            raise ValueError("version must be positive")
        if self.startup_timeout_ms <= 0 or self.startup_timeout_ms > 300_000:
            raise ValueError("startup_timeout_ms must be between 1 and 300000")
        if self.tool_timeout_ms <= 0 or self.tool_timeout_ms > 600_000:
            raise ValueError("tool_timeout_ms must be between 1 and 600000")
        if any(
            not isinstance(argument, str) or "\x00" in argument
            for argument in self.args
        ):
            raise ValueError("args must contain only safe strings")
        normalized_tools = tuple(_text(name, "enabled_tool_name") for name in self.enabled_tool_names)
        if not normalized_tools or len(set(normalized_tools)) != len(normalized_tools):
            raise ValueError("enabled_tool_names must be nonempty and unique")
        object.__setattr__(self, "enabled_tool_names", normalized_tools)
        object.__setattr__(
            self,
            "tool_catalog_sha256",
            _digest(self.tool_catalog_sha256, "tool_catalog_sha256"),
        )

        secret_refs: dict[str, str] = {}
        for environment_name, secret_ref in self.environment_secret_refs.items():
            name = _text(environment_name, "environment_name")
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) is None:
                raise ValueError("environment variable name is invalid")
            secret_refs[name] = _text(secret_ref, "environment_secret_ref")
        object.__setattr__(
            self, "environment_secret_refs", MappingProxyType(secret_refs)
        )
        object.__setattr__(self, "status", McpProfileStatus(self.status))

    def __repr__(self) -> str:
        return (
            "McpServerProfile("
            f"profile_id={self.profile_id!r}, version={self.version!r}, "
            f"transport={self.transport!r}, command={self.command!r}, "
            f"enabled_tool_names={self.enabled_tool_names!r}, "
            "environment_secret_refs=<redacted>)"
        )


@dataclass(frozen=True, slots=True)
class ToolApprovalRequest:
    """绑定 Run 提交者、精确预览和单次远端写操作的审批记录。"""

    platform_id: str
    tenant_id: str
    approval_id: str
    run_id: str
    session_id: str
    conversation_id: str
    requested_by: str
    tool_invocation_id: str
    tool_id: str
    tool_version: int
    mcp_server_profile_id: str
    mcp_server_profile_version: int
    arguments_ref: str
    arguments_sha256: str
    preview_ref: str
    preview_sha256: str
    expires_at: datetime
    state: ToolApprovalState = ToolApprovalState.PENDING
    confirmation_idempotency_key: str | None = None
    approved_by: str | None = None
    rejected_by: str | None = None
    approved_at: datetime | None = None
    worker_id: str | None = None
    fencing_token: int = 0
    lease_expires_at: datetime | None = None
    consumed_at: datetime | None = None
    finished_at: datetime | None = None
    error_code: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "platform_id",
            "tenant_id",
            "approval_id",
            "run_id",
            "session_id",
            "conversation_id",
            "requested_by",
            "tool_invocation_id",
            "tool_id",
            "mcp_server_profile_id",
            "arguments_ref",
            "preview_ref",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        if self.tool_version < 1 or self.mcp_server_profile_version < 1:
            raise ValueError("tool and MCP profile versions must be positive")
        if self.fencing_token < 0:
            raise ValueError("fencing_token must not be negative")
        object.__setattr__(
            self, "arguments_sha256", _digest(self.arguments_sha256, "arguments_sha256")
        )
        object.__setattr__(
            self, "preview_sha256", _digest(self.preview_sha256, "preview_sha256")
        )
        object.__setattr__(self, "state", ToolApprovalState(self.state))
        _timestamp(self.expires_at, "expires_at")
        for name in ("approved_at", "lease_expires_at", "consumed_at", "finished_at"):
            value = getattr(self, name)
            if value is not None:
                _timestamp(value, name)
        if self.approved_by is not None:
            object.__setattr__(self, "approved_by", _text(self.approved_by, "approved_by"))
            if self.approved_by != self.requested_by:
                raise ValueError("approved_by must equal the Run submitter")
        if self.rejected_by is not None:
            object.__setattr__(self, "rejected_by", _text(self.rejected_by, "rejected_by"))
            if self.rejected_by != self.requested_by:
                raise ValueError("rejected_by must equal the Run submitter")
        if self.approved_by is not None and self.rejected_by is not None:
            raise ValueError("approval cannot be both approved and rejected")
        if self.confirmation_idempotency_key is not None:
            object.__setattr__(
                self,
                "confirmation_idempotency_key",
                _text(self.confirmation_idempotency_key, "confirmation_idempotency_key"),
            )
        if self.worker_id is not None:
            object.__setattr__(self, "worker_id", _text(self.worker_id, "worker_id"))
        if self.error_code is not None:
            object.__setattr__(self, "error_code", _text(self.error_code, "error_code"))

    def approve(
        self,
        identity: IdentityContext,
        preview_sha256: str,
        *,
        idempotency_key: str,
        now: datetime,
    ) -> ToolApprovalRequest:
        """仅允许原 Run 提交者批准未过期且摘要完全相同的预览。"""

        _timestamp(now, "now")
        if (
            identity.platform_id != self.platform_id
            or identity.tenant_id != self.tenant_id
            or identity.user_id != self.requested_by
        ):
            raise DomainError("AUTHORIZATION_DENIED", "只有 Run 提交者可以批准本次工具操作")
        if not hmac.compare_digest(_digest(preview_sha256, "preview_sha256"), self.preview_sha256):
            raise DomainError("APPROVAL_PREVIEW_MISMATCH", "审批预览摘要已变化")
        key = _text(idempotency_key, "idempotency_key")
        if self.state is not ToolApprovalState.PENDING:
            if (
                self.state
                in {
                    ToolApprovalState.APPROVED,
                    ToolApprovalState.RUNNING,
                    ToolApprovalState.SUCCEEDED,
                    ToolApprovalState.FAILED,
                    ToolApprovalState.UNKNOWN,
                }
                and self.approved_by == identity.user_id
                and self.confirmation_idempotency_key == key
            ):
                return self
            raise DomainError("INVALID_STATE", "审批不处于待确认状态")
        if now >= self.expires_at:
            raise DomainError("APPROVAL_EXPIRED", "审批预览已过期")
        return replace(
            self,
            state=ToolApprovalState.APPROVED,
            approved_by=identity.user_id,
            approved_at=now,
            confirmation_idempotency_key=key,
        )

    def reject(self, identity: IdentityContext, *, now: datetime) -> ToolApprovalRequest:
        """由原 Run 提交者拒绝待确认操作。"""

        self._require_requester(identity)
        _timestamp(now, "now")
        if self.state is not ToolApprovalState.PENDING:
            if self.state is ToolApprovalState.REJECTED and self.rejected_by == identity.user_id:
                return self
            raise DomainError("INVALID_STATE", "审批不处于待确认状态")
        if now >= self.expires_at:
            raise DomainError("APPROVAL_EXPIRED", "审批预览已过期")
        return replace(
            self,
            state=ToolApprovalState.REJECTED,
            rejected_by=identity.user_id,
            finished_at=now,
        )

    def expire(self, *, now: datetime) -> ToolApprovalRequest:
        """到期后关闭未获批准的请求，不改变已批准动作。"""

        _timestamp(now, "now")
        if self.state is not ToolApprovalState.PENDING or now < self.expires_at:
            raise DomainError("INVALID_STATE", "审批当前不能过期")
        return replace(self, state=ToolApprovalState.EXPIRED, finished_at=now)

    def claim(
        self, worker_id: str, *, now: datetime, lease_seconds: int
    ) -> ToolApprovalRequest:
        """以新的 fencing token 原子领取一次性动作。"""

        worker_id = _text(worker_id, "worker_id")
        _timestamp(now, "now")
        if lease_seconds <= 0:
            raise DomainError("INVALID_ARGUMENT", "lease_seconds 必须为正数")
        if self.state is not ToolApprovalState.APPROVED:
            raise DomainError("INVALID_STATE", "只有已批准动作可以由 Worker 领取")
        return replace(
            self,
            state=ToolApprovalState.RUNNING,
            worker_id=worker_id,
            fencing_token=self.fencing_token + 1,
            lease_expires_at=now + timedelta(seconds=lease_seconds),
            consumed_at=now,
        )

    def finish(
        self,
        worker_id: str,
        *,
        fencing_token: int,
        state: ToolApprovalState,
        now: datetime,
        error_code: str | None = None,
    ) -> ToolApprovalRequest:
        """只接受租约持有者提交的可信动作终态。"""

        worker_id = _text(worker_id, "worker_id")
        _timestamp(now, "now")
        terminal_state = ToolApprovalState(state)
        if terminal_state not in {
            ToolApprovalState.SUCCEEDED,
            ToolApprovalState.FAILED,
            ToolApprovalState.UNKNOWN,
        }:
            raise DomainError("INVALID_ARGUMENT", "审批动作终态不合法")
        if (
            self.state is not ToolApprovalState.RUNNING
            or self.worker_id != worker_id
            or self.fencing_token != fencing_token
            or self.lease_expires_at is None
            or now >= self.lease_expires_at
        ):
            raise DomainError("STALE_FENCING_TOKEN", "Worker 租约已失效")
        return replace(
            self,
            state=terminal_state,
            finished_at=now,
            error_code=error_code,
        )

    def _require_requester(self, identity: IdentityContext) -> None:
        if (
            identity.platform_id != self.platform_id
            or identity.tenant_id != self.tenant_id
            or identity.user_id != self.requested_by
        ):
            raise DomainError("AUTHORIZATION_DENIED", "只有 Run 提交者可以处理本次工具审批")
