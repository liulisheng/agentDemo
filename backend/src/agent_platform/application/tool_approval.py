"""一次性 MCP 写操作的审批预览、确认和拒绝应用服务。"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import UTC, datetime

from agent_platform.domain.common import DomainError
from agent_platform.domain.identity import IdentityContext
from agent_platform.domain.mcp import ToolApprovalRequest
from agent_platform.ports.mcp import McpRepository
from agent_platform.ports.storage import ObjectStore


@dataclass(frozen=True, slots=True)
class ToolApprovalPreview:
    """当前 Run 提交者可见的预览正文和最小审批元数据。"""

    request: ToolApprovalRequest
    preview_text: str


class ToolApprovalService:
    """只改变 Approval 状态；绝不在 API 调用链执行 MCP 写工具。"""

    def __init__(self, repository: McpRepository, object_store: ObjectStore) -> None:
        self.repository = repository
        self.object_store = object_store

    def get_preview(
        self, identity: IdentityContext, approval_id: str
    ) -> ToolApprovalPreview:
        """读取租户隔离的预览正文，并校验其摘要仍与 Approval 绑定。"""

        request = self.repository.get_tool_approval(identity, approval_id)
        self._require_requester(identity, request)
        try:
            content = self.object_store.read(
                request.preview_ref,
                identity.platform_id,
                identity.tenant_id,
            )
        except PermissionError as error:
            raise DomainError("RESOURCE_NOT_FOUND", "审批预览不存在") from error
        digest = hashlib.sha256(content).hexdigest()
        if not hmac.compare_digest(digest, request.preview_sha256):
            raise DomainError("APPROVAL_PREVIEW_MISMATCH", "审批预览摘要已变化")
        try:
            preview_text = content.decode("utf-8")
        except UnicodeDecodeError as error:
            raise DomainError("APPROVAL_PREVIEW_MISMATCH", "审批预览不是合法 UTF-8 文本") from error
        return ToolApprovalPreview(request, preview_text)

    def approve(
        self,
        identity: IdentityContext,
        approval_id: str,
        *,
        preview_sha256: str,
        idempotency_key: str,
    ) -> ToolApprovalRequest:
        """交由 MySQL 行锁原子校验提交者、有效期、摘要和确认幂等键。"""

        return self.repository.approve_tool_approval(
            identity,
            approval_id,
            preview_sha256,
            idempotency_key=idempotency_key,
            now=datetime.now(UTC),
        )

    def reject(
        self, identity: IdentityContext, approval_id: str
    ) -> ToolApprovalRequest:
        """由原 Run 提交者拒绝当前预览；重复拒绝由领域状态幂等处理。"""

        return self.repository.reject_tool_approval(
            identity,
            approval_id,
            now=datetime.now(UTC),
        )

    @staticmethod
    def _require_requester(
        identity: IdentityContext, request: ToolApprovalRequest
    ) -> None:
        if (
            identity.platform_id != request.platform_id
            or identity.tenant_id != request.tenant_id
            or identity.user_id != request.requested_by
        ):
            raise DomainError("RESOURCE_NOT_FOUND", "审批预览不存在")


__all__ = ["ToolApprovalPreview", "ToolApprovalService"]
