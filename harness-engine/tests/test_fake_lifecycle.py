import hashlib

import pytest

from harness_engine.core import (
    CancelStatus,
    EmployeeContext,
    EngineNotStartedError,
    EngineRequest,
    EngineState,
    EngineToolDefinition,
    RunStatus,
    SessionMismatchError,
    SessionNotFoundError,
    SessionRef,
    SkillFileContext,
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


def test_fake_engine_traces_employee_version_and_tool_names_without_context_bodies():
    context = EmployeeContext(
        employee_id="employee-1",
        employee_version=4,
        system_prompt="内部员工指令不进入 Trace",
        skill_instructions=("内部 Skill 指令不进入 Trace",),
        skill_files=(
            SkillFileContext(
                "SKILL.md",
                "私有 Skill 文件正文",
                hashlib.sha256("私有 Skill 文件正文".encode()).hexdigest(),
            ),
        ),
    )
    tool = EngineToolDefinition(
        name="projects_list",
        description="列出项目",
        input_schema={"type": "object", "properties": {}},
        risk_level="READ",
    )
    engine = FakeEngine()
    engine.start()
    events = []

    engine.run(
        EngineRequest(
            prompt="查询项目",
            employee_context=context,
            tool_definitions=(tool,),
        ),
        on_event=events.append,
    )

    started = events[0].payload
    assert started["employee_id"] == "employee-1"
    assert started["employee_version"] == 4
    assert started["allowed_tool_names"] == ("projects_list",)
    assert "内部员工指令不进入 Trace" not in repr(started)
    assert "私有 Skill 文件正文" not in repr(started)
