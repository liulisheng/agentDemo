import hashlib

import pytest

from agent_platform.adapters.runtime.fake import FakeRuntimeAdapter
from agent_platform.adapters.runtime.harness import HarnessRuntimeAdapter
from agent_platform.domain.deployment_runtime import RuntimeOptions
from agent_platform.domain.execution import RuntimeStatus
from agent_platform.ports.runtime import (
    RuntimeEmployeeContext,
    RuntimeRequest,
    RuntimeSkillFile,
    RuntimeToolDefinition,
)


def test_fake_runtime_creates_session_and_returns_deterministic_result() -> None:
    runtime = FakeRuntimeAdapter()
    session_ref = runtime.create_session()

    result = runtime.run(
        RuntimeRequest(
            run_id="run-1",
            session_ref=session_ref,
            prompt="hello",
            skill_instructions=("回答问题",),
            allowed_tools=(),
        )
    )

    assert result.status is RuntimeStatus.SUCCEEDED
    assert result.text == "fake: hello"
    assert result.runtime_session_ref == session_ref
    assert result.usage.total_tokens == result.usage.input_tokens + result.usage.output_tokens
    assert [event.event_type for event in result.events] == ["run_started", "runtime_completed", "run_finished"]


def test_fake_runtime_denies_tool_outside_allowed_intersection() -> None:
    runtime = FakeRuntimeAdapter()
    session_ref = runtime.create_session()

    result = runtime.run(
        RuntimeRequest(
            run_id="run-1",
            session_ref=session_ref,
            prompt="echo: hello",
            skill_instructions=("使用 echo",),
            allowed_tools=(),
        )
    )

    assert result.status is RuntimeStatus.FAILED
    assert result.events[1].event_type == "tool_denied"
    assert result.events[1].payload["error_category"] == "POLICY_DENIED"


def test_fake_runtime_normalizes_reasoning_phase_events() -> None:
    runtime = FakeRuntimeAdapter()

    result = runtime.run(
        RuntimeRequest(
            run_id="run-thinking",
            session_ref=None,
            prompt="分析任务",
            skill_instructions=(),
            allowed_tools=(),
            runtime_options=RuntimeOptions.from_mapping({"reasoning_mode": "DEEP"}),
        )
    )

    assert [event.event_type for event in result.events[:3]] == [
        "run_started",
        "reasoning_started",
        "reasoning_completed",
    ]


def test_fake_runtime_rejects_unknown_session() -> None:
    runtime = FakeRuntimeAdapter()

    with pytest.raises(ValueError, match="runtime session"):
        runtime.run(
            RuntimeRequest(
                run_id="run-1",
                session_ref="fake-session-missing",
                prompt="hello",
                skill_instructions=(),
                allowed_tools=(),
            )
        )


def test_fake_runtime_exposes_opaque_thread_and_workspace_and_cancel() -> None:
    runtime = FakeRuntimeAdapter()
    session_ref = runtime.create_session()
    result = runtime.run(
        RuntimeRequest(
            run_id="run-2",
            session_ref=session_ref,
            prompt="hello",
            skill_instructions=(),
            allowed_tools=(),
            workspace_ref="fake-workspace-1",
        )
    )

    assert result.runtime_thread_ref
    assert result.runtime_workspace_ref == "fake-workspace-1"
    assert runtime.cancel(session_ref)


def test_runtime_request_has_typed_employee_context_and_read_preview_tool_specs() -> None:
    context = RuntimeEmployeeContext(
        employee_id="employee-1",
        employee_version=3,
        system_prompt="只执行员工任务",
        skill_instructions=("先只读检查，再输出预览",),
        skill_files=(
            RuntimeSkillFile(
                skill_id="skill-1",
                skill_version=2,
                path="SKILL.md",
                content="创建工作流必须 OFFLINE",
                content_sha256=hashlib.sha256("创建工作流必须 OFFLINE".encode()).hexdigest(),
            ),
        ),
        model_route_id="route-1",
    )
    tool = RuntimeToolDefinition(
        tool_id="tool-1",
        tool_version=2,
        name="projects_list",
        description="列出项目",
        input_schema={"type": "object", "properties": {}},
        risk_level="READ",
    )
    request = RuntimeRequest(
        run_id="run-1",
        session_ref="session-1",
        prompt="列出项目",
        skill_instructions=context.skill_instructions,
        allowed_tools=(tool.name,),
        employee_context=context,
        tool_definitions=(tool,),
    )

    assert request.employee_context is context
    assert request.tool_definitions == (tool,)
    assert "secret" not in {field.name for field in __import__("dataclasses").fields(context)}
    assert "只执行员工任务" not in repr(context)
    assert "创建工作流必须 OFFLINE" not in repr(context)
    with pytest.raises(ValueError, match="READ or PREVIEW"):
        RuntimeToolDefinition(
            tool_id="tool-write",
            tool_version=1,
            name="workflow_create",
            description="创建工作流",
            input_schema={"type": "object"},
            risk_level="WRITE",
        )


def test_runtime_request_rejects_a_raw_harness_home_path() -> None:
    with pytest.raises(ValueError, match="opaque harness-session reference"):
        RuntimeRequest(
            run_id="run-path-check",
            session_ref="session-1",
            prompt="查询项目",
            skill_instructions=(),
            allowed_tools=(),
            harness_home_ref="/var/lib/agent-platform/harness-sessions/tenant-a",
        )


def test_harness_runtime_reports_cancellation_only_when_engine_can_cancel() -> None:
    class EngineWithoutCancel:
        def start(self) -> None:
            pass

        def create_session(self) -> str:
            return "runtime-session-1"

    runtime = HarnessRuntimeAdapter(
        lambda _engine_request, _runtime_request: EngineWithoutCancel(), engine_name="fake"
    )
    session_ref = runtime.create_session()

    assert runtime.capabilities.cancellation is False
    assert runtime.cancel(session_ref) is False
