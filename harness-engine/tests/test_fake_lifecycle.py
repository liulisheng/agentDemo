import pytest

from harness_engine.core import (
    CancelStatus,
    EngineNotStartedError,
    EngineRequest,
    EngineState,
    SessionMismatchError,
    SessionNotFoundError,
    SessionRef,
    RunStatus,
)
from harness_engine.engines.fake import FakeEngine


def test_fake_requires_start_and_close_blocks_new_runs():
    engine = FakeEngine()

    with pytest.raises(EngineNotStartedError):
        engine.run(EngineRequest(prompt="hello"))

    engine.start()
    assert engine.state is EngineState.STARTED
    assert engine.run(EngineRequest(prompt="hello")).text == "fake: hello"

    engine.close()
    with pytest.raises(RuntimeError, match="closed"):
        engine.run(EngineRequest(prompt="again"))


def test_fake_creates_and_resumes_only_known_sessions():
    engine = FakeEngine()

    engine.start()
    created = engine.create_session()
    assert created.engine_name == "fake"
    assert engine.resume_session(created) == created

    with pytest.raises(SessionNotFoundError):
        engine.resume_session(SessionRef(session_id="missing", engine_name="fake"))

    with pytest.raises(SessionMismatchError):
        engine.resume_session(SessionRef(session_id="other", engine_name="deepseek"))


def test_fake_emits_parent_aware_events_and_supports_cancellation():
    engine = FakeEngine()
    engine.start()
    session = engine.create_session()
    events = []
    cancellations = []

    def on_event(event):
        events.append(event)
        if event.event_type == "run_started":
            cancellations.append(engine.cancel(event.session_id or ""))

    result = engine.run(
        EngineRequest(
            prompt="hello",
            session=session,
            parent_run_id="parent-1",
        ),
        on_event=on_event,
    )

    assert [event.event_type for event in events] == [
        "run_started",
        "run_finished",
    ]
    assert all(event.session_id == session.session_id for event in events)
    assert events[0].payload["parent_run_id"] == "parent-1"
    assert cancellations[0].status is CancelStatus.CANCELLING
    assert result.status is RunStatus.CANCELLED
    assert engine.cancel(session.session_id).status is CancelStatus.NOT_RUNNING


def test_fake_cleans_active_state_when_event_handler_fails():
    engine = FakeEngine()
    engine.start()
    session = engine.create_session()

    def fail(_event):
        raise ValueError("event consumer failed")

    with pytest.raises(ValueError, match="event consumer"):
        engine.run(EngineRequest(prompt="hello", session=session), on_event=fail)

    assert engine.cancel(session.session_id).status is CancelStatus.NOT_RUNNING
