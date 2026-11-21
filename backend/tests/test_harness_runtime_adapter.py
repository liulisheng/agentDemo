import hashlib
from dataclasses import dataclass

import pytest

from agent_platform.adapters.runtime.harness import HarnessRuntimeAdapter
from agent_platform.domain.model_routes import ModelRouteSnapshot, SecretRef
from agent_platform.domain.observability import SkillFilePackageSnapshot, SkillFileSnapshotEntry
from agent_platform.ports.runtime import (
    RuntimeEmployeeContext,
    RuntimeRequest,
    RuntimeSkillFile,
    RuntimeToolDefinition,
)

engine_core = pytest.importorskip("harness_engine.core")


@dataclass
class FakeHarness:
    started: bool = False
    last_request: object | None = None
    closed: bool = False

    def start(self) -> None:
        self.started = True

    def create_session(self):
        return type("Ref", (), {"session_id": "harness-session-1"})()

    def run(self, request, on_event=None):
        self.last_request = request
        return type("Result", (), {"text": f"harness: {request.prompt}", "result_ref": "result-1"})()

    def close(self) -> None:
        self.started = False
        self.closed = True


def test_harness_adapter_only_translates_lifecycle_and_keeps_engine_loop_opaque() -> None:
    harness = FakeHarness()
    adapter = HarnessRuntimeAdapter(
        lambda _engine_request, _runtime_request: harness,
        engine_name="fake",
    )
    session = adapter.create_session()
    file_package = SkillFilePackageSnapshot(
        "skill-1",
        1,
        "SKILL.md",
        "a" * 64,
        (SkillFileSnapshotEntry("SKILL.md", "asset-1", "text/markdown", 5, "b" * 64),),
    )
    result = adapter.run(
        RuntimeRequest(
            "run-1",
            session,
            "hello",
            ("处理步骤",),
            ("echo",),
            system_prompt="员工系统指令",
            skill_file_packages=(file_package,),
            employee_context=RuntimeEmployeeContext(
                employee_id="employee-1",
                employee_version=3,
                system_prompt="员工系统指令",
                skill_refs=(("skill-1", 1),),
                skill_instructions=("处理步骤",),
                skill_files=(
                    RuntimeSkillFile(
                        skill_id="skill-1",
                        skill_version=1,
                        path="references.md",
                        content="只读查询后再预览",
                        content_sha256=hashlib.sha256("只读查询后再预览".encode()).hexdigest(),
                    ),
                ),
                model_route_id="route-1",
            ),
            tool_definitions=(
                RuntimeToolDefinition(
                    tool_id="tool-1",
                    tool_version=2,
                    name="echo",
                    description="回显用户文本",
                    input_schema={"type": "object", "properties": {"text": {"type": "string"}}},
                    risk_level="READ",
                ),
            ),
        )
    )
    assert isinstance(harness.last_request, engine_core.EngineRequest)
    assert session.startswith("harness-pending:")
    assert result.text == "harness: hello"
    assert harness.last_request.employee_context.system_prompt == "员工系统指令"
    assert harness.last_request.employee_context.skill_instructions == ("处理步骤",)
    assert harness.last_request.employee_context.employee_version == 3
    assert harness.last_request.employee_context.skill_files[0].content == "只读查询后再预览"
    assert harness.last_request.employee_context.model_route_id == "route-1"
    assert harness.last_request.tool_definitions[0].name == "echo"
    assert "员工系统指令" not in repr(harness.last_request)
    assert "只读查询后再预览" not in repr(harness.last_request)
    assert harness.closed is True


def test_harness_adapter_passes_pinned_model_config_without_secret_reference() -> None:
    assert "model_route_snapshot" in RuntimeRequest.__dataclass_fields__, (
        "RuntimeRequest must carry the RunSnapshot model configuration"
    )
    harness = FakeHarness()
    created: list[object] = []

    def create_engine(engine_request, _runtime_request):
        created.append(engine_request)
        return harness

    snapshot = ModelRouteSnapshot(
        tenant_id="tenant-a",
        route_id="route-a",
        route_version=3,
        route_alias="assistant",
        connection_id="connection-a",
        connection_version=4,
        provider_kind="openai-compatible",
        model_id="local-model-a",
        protocol="openai-chat-completions",
        endpoint="http://host.docker.internal:63072/v1",
        secret_ref=SecretRef("tenant-a/local-model-key", "tenant-a", "environment"),
        parameters={"temperature": 0.2, "max_tokens": 64},
    )
    adapter = HarnessRuntimeAdapter(create_engine, engine_name="deepseek")
    result = adapter.run(
        RuntimeRequest(
            run_id="model-config-run",
            session_ref=None,
            prompt="Reply READY",
            skill_instructions=(),
            allowed_tools=(),
            model_route_snapshot=snapshot,
        )
    )

    model_config = getattr(created[0], "model_config", None)
    assert model_config is not None
    assert model_config.route_id == "route-a"
    assert model_config.route_version == 3
    assert model_config.connection_version == 4
    assert model_config.model_id == "local-model-a"
    assert model_config.endpoint == "http://host.docker.internal:63072/v1"
    assert model_config.parameters == {"temperature": 0.2, "max_tokens": 64}
    assert "local-model-key" not in repr(model_config)
    assert result.text == "harness: Reply READY"
    assert result.usage.estimated is True
    assert result.usage.source == "ESTIMATED"
    assert result.usage.pricing_version == "estimated-v1"


def test_harness_adapter_normalizes_upstream_usage_and_request_id() -> None:
    class EngineWithUpstreamUsage(FakeHarness):
        def run(self, request, on_event=None):
            self.last_request = request
            return type(
                "UpstreamResult",
                (),
                {
                    "text": "READY",
                    "finish_reason": "completed",
                    "usage": {
                        "prompt_tokens": 13,
                        "completion_tokens": 5,
                        "total_tokens": 18,
                        "api_key": "must-not-escape",
                    },
                    "request_id": "req-provider-123",
                },
            )()

    adapter = HarnessRuntimeAdapter(
        lambda _engine_request, _runtime_request: EngineWithUpstreamUsage(),
        engine_name="deepseek",
    )
    result = adapter.run(
        RuntimeRequest(
            run_id="upstream-usage-run",
            session_ref=None,
            prompt="Reply READY",
            skill_instructions=(),
            allowed_tools=(),
        )
    )

    assert result.usage.input_tokens == 13
    assert result.usage.output_tokens == 5
    assert result.usage.total_tokens == 18
    assert result.usage.source == "UPSTREAM"
    assert result.usage.estimated is False
    assert result.usage.provider_request_id == "req-provider-123"
    assert result.usage.pricing_version == "unpriced-v1"
    assert "must-not-escape" not in repr(result.usage)


def test_harness_adapter_uses_fallback_result_ref_when_sdk_omits_one() -> None:
    class EngineWithoutResultRef(FakeHarness):
        def run(self, request, on_event=None):
            self.last_request = request
            return type(
                "ResultWithoutRef",
                (),
                {"text": "READY", "finish_reason": "completed", "result_ref": None},
            )()

    adapter = HarnessRuntimeAdapter(
        lambda _engine_request, _runtime_request: EngineWithoutResultRef(),
        engine_name="deepseek",
    )
    result = adapter.run(
        RuntimeRequest(
            run_id="missing-result-ref-run",
            session_ref=None,
            prompt="Reply READY",
            skill_instructions=(),
            allowed_tools=(),
        )
    )

    assert result.runtime_result_ref == "deepseek-result"


def test_harness_adapter_builds_and_closes_a_distinct_engine_for_each_run() -> None:
    created: list[tuple[object, FakeHarness]] = []

    def create_engine(request, _runtime_request):
        engine = FakeHarness()
        created.append((request, engine))
        return engine

    adapter = HarnessRuntimeAdapter(create_engine, engine_name="deepseek")
    first_context = RuntimeEmployeeContext(
        employee_id="employee-a",
        employee_version=1,
        system_prompt="员工 A 的系统指令",
    )
    second_context = RuntimeEmployeeContext(
        employee_id="employee-b",
        employee_version=4,
        system_prompt="员工 B 的系统指令",
    )
    first_tools = (
        RuntimeToolDefinition(
            "tool-a",
            1,
            "projects_list",
            "列出项目",
            {"type": "object", "properties": {}},
            "READ",
        ),
    )
    second_tools = (
        RuntimeToolDefinition(
            "tool-b",
            2,
            "workflow_preview",
            "预览工作流",
            {"type": "object", "properties": {}},
            "PREVIEW",
        ),
    )

    for run_id, context, definitions in (
        ("run-a", first_context, first_tools),
        ("run-b", second_context, second_tools),
    ):
        adapter.run(
            RuntimeRequest(
                run_id,
                "stable-session",
                "执行当前任务",
                context.skill_instructions,
                tuple(item.name for item in definitions),
                employee_context=context,
                tool_definitions=definitions,
                harness_home_ref="harness-session:" + "a" * 64,
            )
        )

    assert len(created) == 2
    first_request, first_engine = created[0]
    second_request, second_engine = created[1]
    assert first_engine is not second_engine
    assert first_request.run_id == "run-a"
    assert second_request.run_id == "run-b"
    assert first_request.harness_home_ref == "harness-session:" + "a" * 64
    assert second_request.harness_home_ref == first_request.harness_home_ref
    assert first_request.employee_context.employee_id == "employee-a"
    assert first_request.tool_definitions[0].name == "projects_list"
    assert second_request.employee_context.employee_id == "employee-b"
    assert second_request.tool_definitions[0].name == "workflow_preview"
    assert first_engine.closed is True
    assert second_engine.closed is True


def test_harness_adapter_forwards_tool_call_events_with_a_tool_span_type() -> None:
    sdk_event = engine_core.HarnessEvent(
        event_type="tool/call",
        payload={"data": {"arguments": '{"text":"private-payload"}'}},
    )
    assert "private-payload" not in repr(sdk_event)

    class EventHarness(FakeHarness):
        def run(self, request, on_event=None):
            self.last_request = request
            if on_event is not None:
                on_event(
                    engine_core.HarnessEvent(
                        event_type="tool/call",
                        session_id="session-e2e",
                        run_id=request.run_id,
                        payload={
                            "data": {
                                "callId": "call-e2e",
                                "name": "mcp__profile__workflow_preview",
                                "arguments": '{"project_code":123}',
                            }
                        },
                    )
                )
            return type(
                "Result",
                (),
                {"text": "preview-ready", "result_ref": "result-preview"},
            )()

    harness = EventHarness()
    adapter = HarnessRuntimeAdapter(
        lambda _engine_request, _runtime_request: harness,
        engine_name="deepseek",
    )
    result = adapter.run(
        RuntimeRequest(
            "run-events",
            "session-e2e",
            "create a preview",
            (),
            (),
            system_prompt="Use the configured preview tool.",
            employee_context=RuntimeEmployeeContext(
                employee_id="employee-events",
                employee_version=1,
                system_prompt="Use the configured preview tool.",
            ),
        )
    )

    assert len(result.events) == 1
    assert result.events[0].event_type == "tool/call"
    assert result.events[0].span_type == "TOOL"
    assert result.events[0].payload["data"]["callId"] == "call-e2e"


def test_harness_adapter_exposes_its_engine_name_for_runtime_reference() -> None:
    adapter = HarnessRuntimeAdapter(
        lambda _engine_request, _runtime_request: object(), engine_name="deepseek"
    )

    assert adapter.engine_name == "deepseek"


def test_harness_adapter_resolves_sdk_shortened_mcp_tool_name_from_run_schema() -> None:
    remote_name = "dolphinscheduler_project_create_preview"

    class ShortNameHarness(FakeHarness):
        def run(self, request, on_event=None):
            if on_event is not None:
                on_event(
                    engine_core.HarnessEvent(
                        event_type="tool/call",
                        payload={
                            "data": {
                                "callId": "short-name-call",
                                "name": "mcp__mcp-6dd872c9666fc2713f27__dolphinscheduler_pro_3c153b568176",
                                "arguments": '{"project_name":"acceptance"}',
                            }
                        },
                    )
                )
            return type("Result", (), {"text": "preview", "result_ref": "result"})()

    tool = RuntimeToolDefinition(
        "tool-project-preview",
        1,
        remote_name,
        "预览创建项目",
        {
            "type": "object",
            "properties": {
                "project_name": {"type": "string"},
                "description": {"type": "string"},
            },
            "required": ["project_name"],
        },
        "PREVIEW",
        remote_tool_name=remote_name,
    )
    adapter = HarnessRuntimeAdapter(
        lambda _engine_request, _runtime_request: ShortNameHarness(), engine_name="deepseek"
    )
    result = adapter.run(
        RuntimeRequest(
            "run-short-name",
            "session-short-name",
            "preview project",
            (),
            (remote_name,),
            system_prompt="员工只可调用已绑定工具。",
            employee_context=RuntimeEmployeeContext(
                employee_id="employee-short-name",
                employee_version=1,
                system_prompt="员工只可调用已绑定工具。",
            ),
            tool_definitions=(tool,),
        )
    )

    assert result.events[0].payload["data"]["name"] == remote_name
