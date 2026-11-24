"""RuntimeOptions 类型化回答策略的范围和继承契约。"""

from __future__ import annotations

import pytest

from agent_platform.domain.deployment_runtime import (
    ReasoningEffort,
    ReasoningMode,
    ResponseVerbosity,
    RuntimeOptions,
)
from agent_platform.ports.runtime import RuntimeRequest


def test_runtime_options_parse_and_resolve_inherited_values() -> None:
    override = RuntimeOptions.from_mapping(
        {
            "reasoning_mode": "DEEP",
            "reasoning_effort": "HIGH",
            "response_verbosity": "DETAILED",
            "reasoning_budget_tokens": 16_384,
        }
    )

    effective = override.resolve()

    assert effective.reasoning_mode is ReasoningMode.DEEP
    assert effective.reasoning_effort is ReasoningEffort.HIGH
    assert effective.response_verbosity is ResponseVerbosity.DETAILED
    assert effective.reasoning_budget_tokens == 16_384
    assert effective.max_tool_steps == 20
    assert effective.timeout_seconds == 300


def test_runtime_options_accept_inherit_markers_and_round_trip_without_them() -> None:
    value = RuntimeOptions.from_mapping(
        {
            "reasoning_mode": "INHERIT",
            "reasoning_effort": None,
            "response_verbosity": "STANDARD",
        }
    )

    assert value.reasoning_mode is None
    assert value.reasoning_effort is None
    assert value.to_mapping() == {"response_verbosity": "STANDARD"}
    assert value.to_mapping(include_none=True)["reasoning_mode"] is None


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("reasoning_mode", "MEDIUM"),
        ("reasoning_effort", "EXTREME"),
        ("response_verbosity", "VERBOSE"),
        ("reasoning_budget_tokens", 0),
        ("max_output_tokens", -1),
        ("max_tool_steps", 0),
        ("timeout_seconds", 0),
    ],
)
def test_runtime_options_reject_invalid_values(key: str, value: object) -> None:
    with pytest.raises(ValueError):
        RuntimeOptions.from_mapping({key: value})


def test_runtime_options_reject_unknown_fields() -> None:
    with pytest.raises(ValueError, match="unknown runtime option"):
        RuntimeOptions.from_mapping({"temperature": 0.2})


def test_runtime_request_accepts_the_typed_runtime_options_boundary() -> None:
    request = RuntimeRequest(
        run_id="run-1",
        session_ref=None,
        prompt="分析任务",
        skill_instructions=(),
        allowed_tools=(),
        runtime_options=RuntimeOptions.from_mapping({"reasoning_mode": "DEEP"}),
    )

    assert request.runtime_options is not None
    assert request.runtime_options.reasoning_mode is ReasoningMode.DEEP
