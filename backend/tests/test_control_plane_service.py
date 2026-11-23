import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from agent_platform.adapters.identity.fake import FakeIdentityProvider
from agent_platform.adapters.runtime.fake import FakeRuntimeAdapter
from agent_platform.application.catalog_service import CatalogService, InMemoryCatalogRepository
from agent_platform.application.control_plane import ControlPlaneService
from agent_platform.domain.catalog import SkillType
from agent_platform.domain.common import DomainError
from agent_platform.domain.observability import SpanType
from agent_platform.ports.runtime import RuntimeEvent
from tests._runtime_config_helpers import configure_in_memory_deployment


def _published_service() -> tuple[ControlPlaneService, object, object]:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    catalog = CatalogService(InMemoryCatalogRepository())
    tool = catalog.create_tool(identity, "echo", "回显", {"type": "object"})
    catalog.publish_tool(identity, tool.tool_id)
    skill = catalog.create_skill(
        identity, "回声技能", SkillType.TOOL_BUNDLE, "使用 echo", [tool.tool_id]
    )
    catalog.publish_skill(identity, skill.skill_id)
    employee = catalog.create_employee(identity, "行政助手", "行政", "处理事务")
    version = catalog.create_employee_version(
        identity, employee.employee_id, "你是行政助手", [skill.skill_id], [tool.tool_id]
    )
    catalog.publish_employee_version(identity, employee.employee_id, version.version)
    deployment = catalog.deploy(identity, employee.employee_id, version.version)
    deployment = configure_in_memory_deployment(catalog, identity, deployment)
    service = ControlPlaneService(catalog, FakeRuntimeAdapter())
    return service, identity, deployment


def test_run_persists_snapshot_and_ordered_trace() -> None:
    service, identity, deployment = _published_service()
    session = service.create_session(identity, deployment.deployment_id)
    assert len(session.session_id) == 26

    run = service.create_run(identity, session.session_id, "echo: hello", "idem-1")
    assert len(run.run_id) == 26
    assert run.submitted_by_user_id == identity.user_id
    trace = service.get_trace(identity, run.run_id)

    assert run.status.value == "succeeded"
    assert run.snapshot.employee_version == 1
    assert run.snapshot.tool_version_ids
    assert [span.span_type for span in trace.spans][:2] == [SpanType.SESSION, SpanType.RUN]
    assert {span.span_type for span in trace.spans} >= {
        SpanType.SESSION,
        SpanType.RUN,
        SpanType.SKILL,
        SpanType.TOOL,
        SpanType.RUNTIME,
        SpanType.MODEL,
        SpanType.RESULT,
    }
    assert trace.usage.total_tokens > 0
    assert session.runtime_session_ref is not None


def test_run_idempotency_replays_and_conflicts() -> None:
    service, identity, deployment = _published_service()
    session = service.create_session(identity, deployment.deployment_id)

    first = service.create_run(identity, session.session_id, "hello", "idem-1")
    replay = service.create_run(identity, session.session_id, "hello", "idem-1")
    assert replay.run_id == first.run_id

    with pytest.raises(DomainError) as error:
        service.create_run(identity, session.session_id, "changed", "idem-1")
    assert error.value.code == "IDEMPOTENCY_CONFLICT"


def test_prepared_conversation_turn_is_queued_and_idempotent_without_running_runtime() -> None:
    service, identity, deployment = _published_service()
    session = service.create_session(identity, deployment.deployment_id)

    first = service.prepare_conversation_turn(
        identity, session.session_id, "conversation-1", "生成离线工作流"
    )
    assert first.status.value == "queued"
    assert first.submitted_by_user_id == identity.user_id

    queued = service.queue_prepared_run(identity, first, "turn-1")
    assert queued.run_id == first.run_id
    assert service.get_run(identity, first.run_id).status.value == "queued"

    replay_candidate = service.prepare_conversation_turn(
        identity, session.session_id, "conversation-1", "生成离线工作流"
    )
    replay = service.queue_prepared_run(identity, replay_candidate, "turn-1")
    assert replay.run_id == first.run_id

    changed = service.prepare_conversation_turn(
        identity, session.session_id, "conversation-1", "创建另一个工作流"
    )
    with pytest.raises(DomainError) as conflict:
        service.queue_prepared_run(identity, changed, "turn-1")
    assert conflict.value.code == "IDEMPOTENCY_CONFLICT"


def test_run_started_before_deployment_disable_can_finish_and_replay() -> None:
    class BlockingFakeRuntime(FakeRuntimeAdapter):
        """允许测试在 Runtime 等待期间变更 Deployment 状态。"""

        def __init__(self) -> None:
            super().__init__()
            self.started = Event()
            self.finish = Event()

        def run(self, request):
            self.started.set()
            if not self.finish.wait(timeout=5):
                raise TimeoutError("测试未释放阻塞的 Fake Runtime")
            return super().run(request)

    catalog, identity, deployment = _published_service()
    runtime = BlockingFakeRuntime()
    service = ControlPlaneService(catalog.catalog, runtime)
    session = service.create_session(identity, deployment.deployment_id)

    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(
            service.create_run, identity, session.session_id, "运行中请求", "running-idem"
        )
        try:
            assert runtime.started.wait(timeout=3)
            running_replay = service.create_run(
                identity, session.session_id, "运行中请求", "running-idem"
            )
            assert running_replay.status.value == "running"
            assert service.catalog.disable_deployment(identity, deployment.deployment_id).status.value == "DISABLED"

            with pytest.raises(DomainError) as new_run_after_disable:
                service.create_run(identity, session.session_id, "新请求", "new-after-disable")
            assert new_run_after_disable.value.code == "DEPLOYMENT_DISABLED"
            with pytest.raises(DomainError) as changed_replay:
                service.create_run(identity, session.session_id, "修改内容", "running-idem")
            assert changed_replay.value.code == "IDEMPOTENCY_CONFLICT"
        finally:
            runtime.finish.set()

        completed = pending.result(timeout=5)

    assert completed.status.value == "succeeded"
    assert service.create_run(identity, session.session_id, "运行中请求", "running-idem").run_id == completed.run_id


def test_trace_is_tenant_scoped() -> None:
    service, identity, deployment = _published_service()
    session = service.create_session(identity, deployment.deployment_id)
    run = service.create_run(identity, session.session_id, "hello", "idem-1")
    other = FakeIdentityProvider().resolve_identity("admin-other")

    with pytest.raises(DomainError) as error:
        service.get_trace(other, run.run_id)
    assert error.value.code == "RESOURCE_NOT_FOUND"


def test_run_trace_excludes_sibling_run_spans() -> None:
    service, identity, deployment = _published_service()
    session = service.create_session(identity, deployment.deployment_id)
    first = service.create_run(identity, session.session_id, "first", "idem-1")
    second = service.create_run(identity, session.session_id, "second", "idem-2")

    first_trace = service.get_trace(identity, first.run_id)
    assert all(span.run_id in {"", first.run_id} for span in first_trace.spans)
    assert any(span.run_id == second.run_id for span in service.get_session_trace(identity, session.session_id))


def test_control_plane_can_persist_run_snapshot_and_runtime_refs() -> None:
    from agent_platform.infrastructure.persistence import InMemorySessionRunPersistence

    service, identity, deployment = _published_service()
    persistence = InMemorySessionRunPersistence()
    integrated = ControlPlaneService(service.catalog, FakeRuntimeAdapter(), persistence=persistence)
    session = integrated.create_session(identity, deployment.deployment_id)
    run = integrated.create_run(identity, session.session_id, "hello", "persist-1")
    persisted = persistence.get_run(identity, run.run_id)
    assert persisted["runtime_refs"]["runtime_session_ref"].startswith("fake-session-")


def test_tool_trace_keeps_only_safe_metadata_and_payload_digests() -> None:
    service, identity, deployment = _published_service()
    session = service.create_session(identity, deployment.deployment_id)
    run = service.create_run(identity, session.session_id, "普通验收输入", "trace-safe-1")
    arguments = '{"datasource_type":"MYSQL","password_env_alias":"WAREHOUSE"}'
    result_text = '{"preview":{"name":"warehouse"},"confirmation_code":"one-time-secret"}'
    call_event = RuntimeEvent(
        "tool/call",
        "TOOL",
        {
            "data": {
                "callId": "call-safe-1",
                "name": "mcp__dolphin__dolphinscheduler_datasource_create_preview",
                "arguments": arguments,
                "step": 1,
                "turn": 1,
            }
        },
    )
    result_event = RuntimeEvent(
        "tool/result",
        "TOOL",
        {
            "data": {
                "step": 1,
                "turn": 1,
                "message": {
                    "toolCallId": "call-safe-1",
                    "isError": False,
                    "content": [{"type": "text", "text": result_text}],
                },
            }
        },
    )

    call_span = service._event_to_span(session.trace_id, run, call_event)
    result_span = service._event_to_span(session.trace_id, run, result_event)

    assert call_span is not None and call_span.span_type is SpanType.TOOL
    assert call_span.attributes["tool_name"] == "mcp__dolphin__dolphinscheduler_datasource_create_preview"
    assert call_span.attributes["call_id"] == "call-safe-1"
    assert call_span.attributes["arguments_sha256"] == hashlib.sha256(arguments.encode()).hexdigest()
    assert "arguments" not in call_span.attributes
    assert result_span is not None and result_span.span_type is SpanType.TOOL
    assert result_span.attributes["tool_call_id"] == "call-safe-1"
    assert result_span.attributes["result_sha256"] == hashlib.sha256(result_text.encode()).hexdigest()
    serialized = json.dumps(
        {"call": call_span.attributes, "result": result_span.attributes},
        ensure_ascii=False,
    )
    assert "one-time-secret" not in serialized
    assert arguments not in serialized
    assert "one-time-secret" not in repr(result_event)
