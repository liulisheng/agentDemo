import pytest

from agent_platform.adapters.identity.fake import FakeIdentityProvider
from agent_platform.adapters.runtime.fake import FakeRuntimeAdapter
from agent_platform.application.catalog_service import CatalogService, InMemoryCatalogRepository
from agent_platform.application.control_plane import ControlPlaneService
from agent_platform.domain.catalog import SkillType
from agent_platform.domain.common import DomainError
from agent_platform.domain.observability import SpanType


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
    service = ControlPlaneService(catalog, FakeRuntimeAdapter())
    return service, identity, deployment


def test_run_persists_snapshot_and_ordered_trace() -> None:
    service, identity, deployment = _published_service()
    session = service.create_session(identity, deployment.deployment_id)

    run = service.create_run(identity, session.session_id, "echo: hello", "idem-1")
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
