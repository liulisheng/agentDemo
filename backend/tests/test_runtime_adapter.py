import pytest

from agent_platform.adapters.runtime.fake import FakeRuntimeAdapter
from agent_platform.domain.execution import RuntimeStatus
from agent_platform.ports.runtime import RuntimeRequest


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
