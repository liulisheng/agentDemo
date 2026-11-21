"""一次性 Tool Approval 预览读取与提交者审批服务契约。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest

from agent_platform.domain.common import DomainError
from agent_platform.domain.identity import IdentityContext
from agent_platform.domain.mcp import ToolApprovalRequest, ToolApprovalState


def _request(preview: bytes) -> ToolApprovalRequest:
    return ToolApprovalRequest(
        platform_id="platform-1",
        tenant_id="tenant-1",
        approval_id="approval-1",
        run_id="run-1",
        session_id="session-1",
        conversation_id="conversation-1",
        requested_by="user-1",
        tool_invocation_id="span-1",
        tool_id="tool-1",
        tool_version=1,
        mcp_server_profile_id="profile-1",
        mcp_server_profile_version=1,
        arguments_ref="object:arguments",
        arguments_sha256="a" * 64,
        preview_ref="object:preview",
        preview_sha256=sha256(preview).hexdigest(),
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )


class _Repository:
    def __init__(self, request: ToolApprovalRequest) -> None:
        self.request = request
        self.approve_calls: list[tuple[object, ...]] = []
        self.reject_calls: list[tuple[object, ...]] = []

    def get_tool_approval(self, identity: IdentityContext, approval_id: str):
        return self.request

    def approve_tool_approval(
        self, identity, approval_id, preview_sha256, *, idempotency_key, now
    ):
        self.approve_calls.append((identity, approval_id, preview_sha256, idempotency_key, now))
        self.request = self.request.approve(
            identity, preview_sha256, idempotency_key=idempotency_key, now=now
        )
        return self.request

    def reject_tool_approval(self, identity, approval_id, *, now):
        self.reject_calls.append((identity, approval_id, now))
        self.request = self.request.reject(identity, now=now)
        return self.request


class _ObjectStore:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.read_scopes: list[tuple[str, str, str]] = []

    def read(self, object_ref: str, platform_id: str, tenant_id: str) -> bytes:
        self.read_scopes.append((object_ref, platform_id, tenant_id))
        return self.payload


def _service(preview: bytes):
    try:
        from agent_platform.application.tool_approval import ToolApprovalService
    except ImportError as error:
        pytest.fail(f"Tool Approval Service is not implemented: {error}")
    repository = _Repository(_request(preview))
    object_store = _ObjectStore(preview)
    service = ToolApprovalService(repository, object_store)
    return service, repository, object_store


def test_preview_is_read_only_authorized_and_digest_checked() -> None:
    identity = IdentityContext("platform-1", "tenant-1", "user-1", frozenset())
    preview = b'{"workflow":"ods_to_dwd","release_state":"OFFLINE"}'
    service, repository, object_store = _service(preview)

    details = service.get_preview(identity, "approval-1")

    assert details.request.approval_id == "approval-1"
    assert details.preview_text == preview.decode("utf-8")
    assert object_store.read_scopes == [("object:preview", "platform-1", "tenant-1")]
    assert repository.approve_calls == []


def test_preview_is_not_readable_by_another_user_or_tenant() -> None:
    preview = b'{"workflow":"ods_to_dwd"}'
    service, _, object_store = _service(preview)

    with pytest.raises(DomainError) as denied:
        service.get_preview(
            IdentityContext("platform-1", "tenant-1", "user-2", frozenset()),
            "approval-1",
        )
    assert denied.value.code == "RESOURCE_NOT_FOUND"
    assert object_store.read_scopes == []


def test_preview_digest_mismatch_is_rejected_before_display() -> None:
    service, _, object_store = _service(b'{"workflow":"ods_to_dwd"}')
    object_store.payload = b'{"workflow":"tampered"}'
    identity = IdentityContext("platform-1", "tenant-1", "user-1", frozenset())

    with pytest.raises(DomainError) as mismatch:
        service.get_preview(identity, "approval-1")
    assert mismatch.value.code == "APPROVAL_PREVIEW_MISMATCH"


def test_approve_and_reject_forward_only_the_submitter_identity() -> None:
    identity = IdentityContext("platform-1", "tenant-1", "user-1", frozenset())
    preview = b'{"workflow":"ods_to_dwd"}'
    service, repository, _ = _service(preview)

    approved = service.approve(
        identity,
        "approval-1",
        preview_sha256=sha256(preview).hexdigest(),
        idempotency_key="approve-1",
    )
    assert approved.state is ToolApprovalState.APPROVED
    assert repository.approve_calls[0][0] == identity
    assert repository.approve_calls[0][3] == "approve-1"

    rejected_service, rejected_repository, _ = _service(preview)
    rejected = rejected_service.reject(identity, "approval-1")
    assert rejected.state is ToolApprovalState.REJECTED
    assert rejected_repository.reject_calls[0][0] == identity
