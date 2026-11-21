"""验证 PREVIEW 完成后只生成提交人可审阅的一次性 WRITE 授权。"""

import hashlib
import json

from agent_platform.adapters.identity.fake import FakeIdentityProvider
from agent_platform.adapters.runtime.fake import FakeRuntimeAdapter
from agent_platform.application.catalog_service import CatalogService, InMemoryCatalogRepository
from agent_platform.application.control_plane import ControlPlaneService
from agent_platform.domain.catalog import (
    LifecycleStatus,
    ToolVersion,
    compute_tool_input_schema_sha256,
)
from agent_platform.domain.execution import RuntimeReference, RuntimeStatus, UsageRecord
from agent_platform.domain.mcp import McpProfileStatus, McpRiskLevel, McpServerProfile
from agent_platform.domain.observability import (
    RunRecord,
    RunSnapshot,
    SessionRecord,
    SpanType,
    TraceSpan,
)
from agent_platform.ports.runtime import RuntimeEvent, RuntimeToolDefinition
from agent_platform.ports.storage import UploadIntent


class _ObjectStore:
    encryption_enabled = True

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def create_upload_intent(self, request):
        object_ref = f"object:{request.platform_id}:{request.tenant_id}:{len(self.objects) + 1}"
        return UploadIntent("upload-1", object_ref, "upload://upload-1", 60, 1024 * 1024)

    def complete_upload(self, upload_id, content):
        object_ref = f"object:platform-demo:tenant-demo:{len(self.objects) + 1}"
        self.objects[object_ref] = content
        return type("Asset", (), {"object_ref": object_ref})()


class _ConversationRepository:
    def get_conversation_id_for_run(self, identity, run_id):
        assert run_id == "run-demo"
        return "conversation-demo"


class _McpRepository:
    def __init__(self, profile):
        self.profile = profile
        self.created = None

    def get_profile(self, identity, profile_id, version):
        assert (profile_id, version) == (self.profile.profile_id, self.profile.version)
        return self.profile

    def create_tool_approval(self, identity, request, **kwargs):
        self.created = (identity, request, kwargs)
        return request


def test_preview_result_creates_encrypted_exact_write_approval_without_trace_payloads():
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    repository = InMemoryCatalogRepository()
    profile_id = "mcp-profile-demo"
    catalog = CatalogService(repository)
    schema = {
        "type": "object",
        "properties": {
            "project_code": {"type": "string"},
            "name": {"type": "string"},
            "confirmation_code": {"type": "string"},
        },
        "required": ["project_code", "name", "confirmation_code"],
    }
    write = ToolVersion(
        tool_id="tool-write-demo",
        tenant_id=identity.tenant_id,
        name="create_multi",
        description="创建离线工作流",
        input_schema=schema,
        status=LifecycleStatus.PUBLISHED,
        protocol="mcp",
        mcp_server_profile_id=profile_id,
        mcp_server_profile_version=1,
        remote_tool_name="dolphinscheduler_workflow_create_multi",
        input_schema_sha256=compute_tool_input_schema_sha256(schema),
        preview_tool_name="dolphinscheduler_workflow_create_multi_preview",
        mcp_risk_level=McpRiskLevel.WRITE,
        idempotency_support=False,
    )
    preview_schema = {
        "type": "object",
        "properties": {"project_code": {"type": "string"}, "name": {"type": "string"}},
        "required": ["project_code", "name"],
    }
    preview = ToolVersion(
        tool_id="tool-preview-demo",
        tenant_id=identity.tenant_id,
        name="preview_multi",
        description="预览离线工作流",
        input_schema=preview_schema,
        status=LifecycleStatus.PUBLISHED,
        protocol="mcp",
        mcp_server_profile_id=profile_id,
        mcp_server_profile_version=1,
        remote_tool_name="dolphinscheduler_workflow_create_multi_preview",
        input_schema_sha256=compute_tool_input_schema_sha256(preview_schema),
        mcp_risk_level=McpRiskLevel.PREVIEW,
    )
    repository.save_tool(identity, preview)
    repository.save_tool(identity, write)
    profile = McpServerProfile(
        platform_id=identity.platform_id,
        tenant_id=identity.tenant_id,
        profile_id=profile_id,
        version=1,
        name="DolphinScheduler",
        transport="stdio",
        command="/usr/local/bin/python3",
        args=("/data/mcp/dolphinscheduler_tools_server.py",),
        working_directory_ref=f"env-path://{identity.tenant_id}/MCP_WORKDIR",
        enabled_tool_names=(preview.remote_tool_name, write.remote_tool_name),
        tool_catalog_sha256="a" * 64,
        status=McpProfileStatus.ACTIVE,
    )
    mcp_repository = _McpRepository(profile)
    object_store = _ObjectStore()
    service = ControlPlaneService(
        catalog,
        FakeRuntimeAdapter(),
        object_store=object_store,
        mcp_repository=mcp_repository,
        conversation_repository=_ConversationRepository(),
    )
    tool_definition = RuntimeToolDefinition(
        tool_id=preview.tool_id,
        tool_version=1,
        name="preview_multi",
        description=preview.description,
        input_schema=preview.input_schema,
        risk_level="PREVIEW",
        remote_tool_name=preview.remote_tool_name,
    )
    run = RunRecord(
        run_id="run-demo",
        session_id="session-demo",
        platform_id=identity.platform_id,
        tenant_id=identity.tenant_id,
        trace_id="trace-demo",
        prompt_digest="b" * 64,
        snapshot=RunSnapshot(
            employee_id="employee-demo",
            employee_version=1,
            skill_version_ids=("skill-demo",),
            tool_version_ids=(preview.tool_id, write.tool_id),
            execution_profile_id="execution-profile-demo",
            tool_version_refs=((preview.tool_id, 1), (write.tool_id, 1)),
        ),
        status=RuntimeStatus.RUNNING,
        submitted_by_user_id=identity.user_id,
    )
    session = SessionRecord(
        session_id=run.session_id,
        platform_id=identity.platform_id,
        tenant_id=identity.tenant_id,
        deployment_id="deployment-demo",
        trace_id=run.trace_id,
        employee_version=1,
    )
    arguments = {"project_code": "ODS_DWD", "name": "ODS 到 DWD"}
    confirmation_code = "do-not-log-this-confirmation"
    response = {
        "preview": {"name": "ODS 到 DWD", "state": "OFFLINE", "nodes": ["ods", "dwd"]},
        "confirmation_code": confirmation_code,
        "writes_performed": False,
    }
    events = (
        RuntimeEvent(
            "tool/call",
            "TOOL",
            {"data": {"callId": "call-demo", "name": "mcp__dolphin__dolphinscheduler_workflow_create_multi_preview", "arguments": json.dumps(arguments)}},
        ),
        RuntimeEvent(
            "tool/result",
            "TOOL",
            {"data": {"message": {"toolCallId": "call-demo", "isError": False, "content": [{"type": "text", "text": json.dumps(response, ensure_ascii=False)}]}}},
        ),
    )
    call_span = TraceSpan(
        "span-call-demo", run.trace_id, "span-run-demo", run.run_id, SpanType.TOOL, "running",
        {"event_type": "tool/call", "tool_name": "preview_multi", "call_id": "call-demo"}, 4,
    )
    result_span = TraceSpan(
        "span-result-demo", run.trace_id, "span-run-demo", run.run_id, SpanType.TOOL, "succeeded",
        {"event_type": "tool/result", "tool_call_id": "call-demo"}, 5,
    )

    approval = service._create_approval_from_preview(
        identity,
        run,
        session,
        (tool_definition,),
        events,
        (call_span, result_span),
        UsageRecord(1, 1, 2),
        RuntimeReference("harness", "session-ref", "result-ref"),
        run_fencing_token=9,
    )

    assert approval is not None
    assert approval.run_id == run.run_id
    assert approval.conversation_id == "conversation-demo"
    assert approval.requested_by == identity.user_id
    assert approval.tool_id == write.tool_id
    assert approval.tool_invocation_id == call_span.span_id
    stored_arguments = json.loads(object_store.objects[approval.arguments_ref])
    assert stored_arguments == {**arguments, "confirmation_code": confirmation_code}
    stored_preview = object_store.objects[approval.preview_ref].decode()
    assert "OFFLINE" in stored_preview
    assert confirmation_code not in stored_preview
    assert hashlib.sha256(object_store.objects[approval.arguments_ref]).hexdigest() == approval.arguments_sha256
    assert hashlib.sha256(object_store.objects[approval.preview_ref]).hexdigest() == approval.preview_sha256
    assert mcp_repository.created[2]["run_fencing_token"] == 9
    assert mcp_repository.created[2]["trace_spans"] == (call_span, result_span)
    assert "confirmation_code" not in json.dumps(call_span.attributes)
    assert confirmation_code not in json.dumps(result_span.attributes)
