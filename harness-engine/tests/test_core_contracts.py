from datetime import UTC, datetime

import pytest

from harness_engine.core import (
    CancelResult,
    CancelStatus,
    EngineCapabilities,
    EngineRequest,
    EngineResult,
    EngineState,
    HarnessEvent,
    LifecycleEngine,
    SessionRef,
    TenantContext,
    VisibleTextDelta,
)
from harness_engine.engines.fake import FakeEngine
from harness_engine.registry import EngineRegistry


def test_tenant_context_requires_platform_and_tenant():
    context = TenantContext(
        platform_id="platform-1",
        tenant_id="tenant-1",
        user_id="user-1",
        roles=("operator",),
        auth_source="oidc",
    )

    assert context.scope == ("platform-1", "tenant-1")


def test_engine_request_rejects_blank_prompt():
    with pytest.raises(ValueError, match="prompt"):
        EngineRequest(prompt=" ")


def test_registry_rejects_duplicate_engine_names():
    registry = EngineRegistry()
    registry.register("fake", lambda: object())

    with pytest.raises(ValueError, match="already registered"):
        registry.register("fake", lambda: object())


def test_engine_result_contains_completion_timestamp():
    result = EngineResult(
        text="done",
        finish_reason="completed",
        completed_at=datetime.now(UTC),
        capabilities=EngineCapabilities(streaming=True),
    )

    assert result.text == "done"
    assert result.capabilities.streaming is True


def test_session_ref_and_request_keep_parent_run_extension_point():
    session = SessionRef(session_id="session-1", engine_name="fake")
    request = EngineRequest(
        prompt="hello",
        session=session,
        parent_run_id="parent-1",
        metadata={"skill": "summarize"},
    )

    assert request.session_id is None
    assert request.resolved_session_id == "session-1"
    assert request.parent_run_id == "parent-1"
    assert session.engine_session_id == "session-1"


def test_request_rejects_conflicting_session_reference():
    session = SessionRef(session_id="session-1", engine_name="fake")

    with pytest.raises(ValueError, match="session_id"):
        EngineRequest(prompt="hello", session_id="session-2", session=session)


def test_cancel_result_and_event_are_engine_neutral():
    result = CancelResult(status=CancelStatus.CANCELLING, session_id="session-1")
    event = HarnessEvent(
        event_type="subagent_started",
        session_id="session-1",
        run_id="run-1",
        payload={"parent_run_id": "parent-1"},
    )

    assert result.confirmed is False
    assert event.kind == "subagent_started"
    assert event.payload["parent_run_id"] == "parent-1"


def test_visible_text_delta_is_typed_ordered_and_engine_neutral():
    delta = VisibleTextDelta(
        session_id="session-1",
        run_id="run-1",
        sequence=2,
        text=" world",
    )

    assert delta.sequence == 2
    assert delta.text == " world"
    with pytest.raises(ValueError, match="sequence"):
        VisibleTextDelta("session-1", "run-1", 0, "bad")
    with pytest.raises(ValueError, match="text"):
        VisibleTextDelta("session-1", "run-1", 1, "")


def test_lifecycle_engine_start_and_close_are_idempotent():
    engine = LifecycleEngine()

    assert engine.state is EngineState.CREATED
    engine.start()
    engine.start()
    assert engine.state is EngineState.STARTED

    engine.close()
    engine.close()
    assert engine.state is EngineState.CLOSED

    with pytest.raises(RuntimeError, match="closed"):
        engine.start()


def test_fake_engine_returns_engine_neutral_result():
    engine = FakeEngine()
    engine.start()
    result = engine.run(EngineRequest(prompt="hello"))

    assert result.text == "fake: hello"
    assert result.finish_reason == "completed"
