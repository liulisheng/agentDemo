from datetime import UTC, datetime, timedelta

import pytest

from agent_platform.domain.common import DomainError
from agent_platform.domain.identity import IdentityContext
from agent_platform.domain.mcp import (
    McpProfileStatus,
    McpServerProfile,
    ToolApprovalRequest,
    ToolApprovalState,
)

NOW = datetime(2026, 11, 19, 10, 0, tzinfo=UTC)


def test_mcp_profile_validates_stdio_contract_and_redacts_secret_refs() -> None:
    profile = McpServerProfile(
        platform_id="platform-1",
        tenant_id="tenant-1",
        profile_id="profile-1",
        version=1,
        name="DolphinScheduler",
        transport="stdio",
        command="/usr/local/bin/python3",
        args=("-m", "staffdeck_mcp"),
        working_directory_ref="workspace://dolphin-mcp",
        environment_secret_refs={"DOLPHIN_TOKEN": "secret://tenant/dolphin"},
        enabled_tool_names=("projects_list", "workflows_list"),
        tool_catalog_sha256="a" * 64,
        status=McpProfileStatus.ACTIVE,
    )

    assert profile.args == ("-m", "staffdeck_mcp")
    assert profile.enabled_tool_names == ("projects_list", "workflows_list")
    assert "secret://tenant/dolphin" not in repr(profile)

    no_argument_profile = McpServerProfile(
        platform_id="platform-1",
        tenant_id="tenant-1",
        profile_id="profile-no-args",
        version=1,
        name="Standalone MCP",
        transport="stdio",
        command="/usr/local/bin/dolphin-mcp",
        args=(),
        working_directory_ref="workspace://dolphin-mcp",
        enabled_tool_names=("projects_list",),
        tool_catalog_sha256="b" * 64,
    )
    assert no_argument_profile.args == ()

    with pytest.raises(ValueError, match="stdio"):
        McpServerProfile(
            platform_id="platform-1",
            tenant_id="tenant-1",
            profile_id="profile-2",
            version=1,
            name="Remote server",
            transport="streamable-http",
            command="/usr/local/bin/python3",
            args=(),
            working_directory_ref="workspace://mcp",
            enabled_tool_names=("projects_list",),
            tool_catalog_sha256="b" * 64,
        )


def _approval() -> ToolApprovalRequest:
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
        tool_version=2,
        mcp_server_profile_id="profile-1",
        mcp_server_profile_version=3,
        arguments_ref="obj://tenant/arguments",
        arguments_sha256="c" * 64,
        preview_ref="obj://tenant/preview",
        preview_sha256="d" * 64,
        expires_at=NOW + timedelta(minutes=10),
    )


def test_only_run_submitter_can_approve_exact_unexpired_preview() -> None:
    request = _approval()
    requester = IdentityContext("platform-1", "tenant-1", "user-1", frozenset({"member"}))
    other_user = IdentityContext("platform-1", "tenant-1", "user-2", frozenset({"admin"}))
    other_tenant = IdentityContext("platform-1", "tenant-2", "user-1", frozenset({"admin"}))

    with pytest.raises(DomainError) as denied:
        request.approve(other_user, "d" * 64, idempotency_key="confirm-1", now=NOW)
    assert denied.value.code == "AUTHORIZATION_DENIED"

    with pytest.raises(DomainError) as cross_tenant:
        request.approve(other_tenant, "d" * 64, idempotency_key="confirm-1", now=NOW)
    assert cross_tenant.value.code == "AUTHORIZATION_DENIED"

    with pytest.raises(DomainError) as mismatch:
        request.approve(requester, "e" * 64, idempotency_key="confirm-1", now=NOW)
    assert mismatch.value.code == "APPROVAL_PREVIEW_MISMATCH"

    with pytest.raises(DomainError) as expired:
        request.approve(
            requester,
            "d" * 64,
            idempotency_key="confirm-1",
            now=request.expires_at,
        )
    assert expired.value.code == "APPROVAL_EXPIRED"

    approved = request.approve(
        requester, "d" * 64, idempotency_key="confirm-1", now=NOW
    )
    assert approved.state is ToolApprovalState.APPROVED
    assert approved.approved_by == "user-1"
    assert approved.confirmation_idempotency_key == "confirm-1"
    assert approved.approve(
        requester, "d" * 64, idempotency_key="confirm-1", now=NOW
    ) is approved

    rejected = request.reject(requester, now=NOW)
    assert rejected.state is ToolApprovalState.REJECTED
    assert rejected.rejected_by == "user-1"
    assert rejected.approved_by is None


def test_approval_action_is_single_use_and_fencing_protects_completion() -> None:
    requester = IdentityContext("platform-1", "tenant-1", "user-1", frozenset({"member"}))
    approved = _approval().approve(
        requester, "d" * 64, idempotency_key="confirm-1", now=NOW
    )
    running = approved.claim("worker-1", now=NOW, lease_seconds=30)

    assert running.state is ToolApprovalState.RUNNING
    assert running.worker_id == "worker-1"
    assert running.fencing_token == 1
    assert running.consumed_at == NOW

    with pytest.raises(DomainError) as duplicate:
        running.claim("worker-2", now=NOW, lease_seconds=30)
    assert duplicate.value.code == "INVALID_STATE"

    with pytest.raises(DomainError) as stale:
        running.finish(
            "worker-1",
            fencing_token=0,
            state=ToolApprovalState.SUCCEEDED,
            now=NOW + timedelta(seconds=1),
        )
    assert stale.value.code == "STALE_FENCING_TOKEN"

    completed = running.finish(
        "worker-1",
        fencing_token=1,
        state=ToolApprovalState.SUCCEEDED,
        now=NOW + timedelta(seconds=2),
    )
    assert completed.state is ToolApprovalState.SUCCEEDED
    assert completed.finished_at == NOW + timedelta(seconds=2)
