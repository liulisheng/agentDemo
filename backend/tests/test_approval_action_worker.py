"""一次性 WRITE Approval Worker 的精确版本、租约和 UNKNOWN 契约。"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from agent_platform.adapters.mcp.stdio import (
    McpHostStartupError,
    McpToolDefinition,
    McpToolTimeoutError,
    compute_tool_catalog_sha256,
)
from agent_platform.domain.catalog import (
    LifecycleStatus,
    ToolVersion,
    compute_tool_input_schema_sha256,
)
from agent_platform.domain.identity import IdentityContext
from agent_platform.domain.mcp import (
    McpProfileStatus,
    McpRiskLevel,
    McpServerProfile,
    ToolApprovalRequest,
    ToolApprovalState,
)

_SCHEMA = {
    "type": "object",
    "properties": {"workflow_name": {"type": "string"}},
    "required": ["workflow_name"],
    "additionalProperties": False,
}
_ARGUMENTS = b'{"workflow_name":"ods_to_dwd"}'
_PREVIEW = b'{"workflow_name":"ods_to_dwd","state":"OFFLINE"}'


def test_worker_maps_mcp_host_startup_failures_to_a_stable_setup_code() -> None:
    from agent_platform.workers.approval_worker import ApprovalActionWorker

    assert (
        ApprovalActionWorker._setup_error_code(McpHostStartupError("unavailable"))
        == "MCP_HOST_SETUP_FAILED"
    )


class _Repository:
    def __init__(self, request: ToolApprovalRequest, profile: McpServerProfile) -> None:
        self.request = request
        self.profile = profile
        self.claimed = False
        self.lose_lease = False
        self.finished: list[tuple[object, ...]] = []

    def claim_approved_tool_approval(self, identity, worker_id, *, lease_seconds):
        assert identity.auth_source == "worker"
        if self.claimed:
            return None
        self.claimed = True
        self.request = self.request.claim(
            worker_id, now=datetime.now(UTC), lease_seconds=lease_seconds
        )
        return self.request

    def get_profile(self, identity, profile_id, version):
        assert (profile_id, version) == (self.profile.profile_id, self.profile.version)
        return self.profile

    def heartbeat_tool_approval(
        self, identity, approval_id, worker_id, fencing_token, *, lease_seconds
    ):
        return not self.lose_lease

    def finish_tool_approval(
        self,
        identity,
        approval_id,
        worker_id,
        fencing_token,
        state,
        *,
        now,
        error_code=None,
        result_ref=None,
        result_sha256=None,
    ):
        self.finished.append((state, error_code, result_ref, result_sha256))
        return not self.lose_lease


class _Catalog:
    def __init__(self, tool: ToolVersion) -> None:
        self.tool = tool

    def get_tool_version(self, identity, tool_id, version):
        assert (tool_id, version) == (self.tool.tool_id, self.tool.version)
        return self.tool


class _ObjectStore:
    def __init__(self) -> None:
        self.contents = {"object:arguments": _ARGUMENTS, "object:preview": _PREVIEW}
        self.uploaded: list[bytes] = []

    def read(self, object_ref, platform_id, tenant_id):
        return self.contents[object_ref]

    def create_upload_intent(self, request):
        self._upload_request = request
        return SimpleNamespace(upload_id="upload-result")

    def complete_upload(self, upload_id, content):
        self.uploaded.append(content)
        return SimpleNamespace(
            object_ref="object:action-result",
            sha256=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
            mime_type="application/json",
        )


class _FakeHost:
    def __init__(self, *, behavior: str = "success", schema: dict[str, object] | None = None) -> None:
        self.tools = (
            McpToolDefinition(
                "create_offline_workflow",
                "创建 OFFLINE 工作流",
                schema or _SCHEMA,
            ),
        )
        self.behavior = behavior
        self.call_count = 0
        self.closed = False
        self.owner_task: asyncio.Task | None = None
        self.wait_forever = False
        self.result = SimpleNamespace(
            isError=False,
            model_dump=lambda **_kwargs: {
                "content": [{"type": "text", "text": "created"}],
                "isError": False,
            },
        )

    async def start(self) -> None:
        self.owner_task = asyncio.current_task()

    async def call_tool(self, name: str, arguments: dict[str, object]):
        assert name == "create_offline_workflow"
        assert arguments == {"workflow_name": "ods_to_dwd"}
        self.call_count += 1
        if self.wait_forever:
            await asyncio.Event().wait()
        if self.behavior == "timeout":
            raise McpToolTimeoutError("timeout")
        if self.behavior == "remote-error":
            self.result.isError = True
        return self.result

    async def abort(self) -> None:
        if self.owner_task is not None and self.owner_task is not asyncio.current_task():
            self.owner_task.cancel("lease lost")

    async def close(self) -> None:
        self.closed = True


def _worker(*, behavior: str = "success", schema: dict[str, object] | None = None, lose_lease: bool = False):
    try:
        from agent_platform.workers.approval_worker import ApprovalActionWorker
    except ImportError as error:
        pytest.fail(f"Approval Action Worker is not implemented: {error}")

    requester = IdentityContext("platform-1", "tenant-1", "user-1", frozenset())
    worker_identity = IdentityContext(
        "platform-1", "tenant-1", "worker-1", frozenset(), auth_source="worker"
    )
    now = datetime.now(UTC)
    profile = McpServerProfile(
        platform_id=requester.platform_id,
        tenant_id=requester.tenant_id,
        profile_id="profile-1",
        version=1,
        name="本地 Dolphin MCP",
        transport="stdio",
        command="/usr/local/bin/python3",
        args=("-m", "dolphinscheduler_mcp"),
        working_directory_ref="workspace://tenant-1/project",
        enabled_tool_names=("create_offline_workflow",),
        tool_catalog_sha256=compute_tool_catalog_sha256(
            (
                McpToolDefinition(
                    "create_offline_workflow", "创建 OFFLINE 工作流", _SCHEMA
                ),
            )
        ),
        status=McpProfileStatus.ACTIVE,
    )
    tool = ToolVersion(
        tool_id="tool-1",
        tenant_id=requester.tenant_id,
        name="create_offline_workflow",
        description="创建离线工作流",
        input_schema=_SCHEMA,
        version=4,
        status=LifecycleStatus.PUBLISHED,
        protocol="mcp",
        mcp_server_profile_id=profile.profile_id,
        mcp_server_profile_version=profile.version,
        remote_tool_name="create_offline_workflow",
        input_schema_sha256=compute_tool_input_schema_sha256(_SCHEMA),
        preview_tool_name="preview_offline_workflow",
        mcp_risk_level=McpRiskLevel.WRITE,
    )
    request = ToolApprovalRequest(
        platform_id=requester.platform_id,
        tenant_id=requester.tenant_id,
        approval_id="approval-1",
        run_id="run-1",
        session_id="session-1",
        conversation_id="conversation-1",
        requested_by=requester.user_id,
        tool_invocation_id="span-1",
        tool_id=tool.tool_id,
        tool_version=tool.version,
        mcp_server_profile_id=profile.profile_id,
        mcp_server_profile_version=profile.version,
        arguments_ref="object:arguments",
        arguments_sha256=hashlib.sha256(_ARGUMENTS).hexdigest(),
        preview_ref="object:preview",
        preview_sha256=hashlib.sha256(_PREVIEW).hexdigest(),
        expires_at=now + timedelta(minutes=5),
    ).approve(
        requester,
        hashlib.sha256(_PREVIEW).hexdigest(),
        idempotency_key="approve-1",
        now=now,
    )
    repository = _Repository(request, profile)
    repository.lose_lease = lose_lease
    host = _FakeHost(behavior=behavior, schema=schema)
    host.wait_forever = lose_lease
    store = _ObjectStore()
    worker = ApprovalActionWorker(
        worker_identity,
        "worker-1",
        repository,
        _Catalog(tool),
        store,
        host_factory=lambda _identity, _profile: host,
        lease_seconds=3,
        heartbeat_interval_seconds=0.01,
    )
    return worker, repository, host, store


def test_worker_executes_only_the_bound_write_and_persists_result_reference() -> None:
    worker, repository, host, store = _worker()

    outcome = asyncio.run(worker.execute_one())

    assert outcome is ToolApprovalState.SUCCEEDED
    assert host.call_count == 1
    assert host.closed
    assert len(store.uploaded) == 1
    assert repository.finished[0][0] is ToolApprovalState.SUCCEEDED
    assert repository.finished[0][2] == "object:action-result"
    assert asyncio.run(worker.execute_one()) is None
    assert host.call_count == 1


def test_worker_marks_remote_timeout_unknown_and_does_not_retry() -> None:
    worker, repository, host, _ = _worker(behavior="timeout")

    outcome = asyncio.run(worker.execute_one())

    assert outcome is ToolApprovalState.UNKNOWN
    assert host.call_count == 1
    assert repository.finished[0][0] is ToolApprovalState.UNKNOWN
    assert asyncio.run(worker.execute_one()) is None
    assert host.call_count == 1


def test_worker_rejects_tool_schema_drift_before_the_remote_write() -> None:
    changed_schema = {"type": "object", "properties": {"other": {"type": "string"}}}
    worker, repository, host, _ = _worker(schema=changed_schema)

    outcome = asyncio.run(worker.execute_one())

    assert outcome is ToolApprovalState.FAILED
    assert host.call_count == 0
    assert repository.finished[0][0] is ToolApprovalState.FAILED


def test_worker_rejects_changed_arguments_digest_before_the_remote_write() -> None:
    worker, repository, host, _ = _worker()
    repository.request = replace(repository.request, arguments_sha256="0" * 64)

    outcome = asyncio.run(worker.execute_one())

    assert outcome is ToolApprovalState.FAILED
    assert host.call_count == 0
    assert repository.finished[0][1] == "APPROVAL_ARGUMENTS_MISMATCH"


def test_worker_lease_loss_cancels_the_mcp_owner_and_marks_outcome_unknown() -> None:
    worker, repository, host, _ = _worker(lose_lease=True)

    outcome = asyncio.run(worker.execute_one())

    assert outcome is ToolApprovalState.UNKNOWN
    assert host.call_count == 1
    assert host.closed
    assert repository.finished[0][0] is ToolApprovalState.UNKNOWN
