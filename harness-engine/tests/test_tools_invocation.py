from typing import Any

import pytest

from harness_engine.tools import (
    ToolDefinition,
    ToolExecutionError,
    ToolRegistry,
    ToolValidationError,
    echo_tool_definition,
)


def _definition(handler):
    return ToolDefinition(
        name="summarize",
        description="summarize input",
        input_schema={
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "style": {"type": "string", "enum": ["short", "long"]},
            },
            "required": ["text"],
            "additionalProperties": False,
        },
        handler=handler,
    )


def test_schema_validation_happens_before_handler_execution():
    calls: list[dict[str, Any]] = []
    registry = ToolRegistry()
    registry.register(_definition(lambda arguments: calls.append(dict(arguments))))

    invalid_arguments = (
        {},
        {"text": 42},
        {"text": "hello", "extra": True},
        {"text": "hello", "style": "unknown"},
    )
    for arguments in invalid_arguments:
        with pytest.raises(ToolValidationError):
            registry.invoke("summarize", arguments)

    assert calls == []


def test_registry_invokes_valid_tool_and_wraps_handler_errors():
    registry = ToolRegistry()
    registry.register(_definition(lambda arguments: arguments["text"].upper()))

    assert registry.invoke("summarize", {"text": "hello"}) == "HELLO"

    def fail(_arguments):
        raise RuntimeError("secret token should not escape")

    registry.register(
        ToolDefinition(
            name="fail",
            description="fails",
            input_schema={"type": "object"},
            handler=fail,
        )
    )
    with pytest.raises(ToolExecutionError, match="failed") as error:
        registry.invoke("fail", {})
    assert "secret token" not in str(error.value)
    assert isinstance(error.value.__cause__, RuntimeError)


def test_echo_tool_is_side_effect_free_and_validates_text():
    registry = ToolRegistry()
    registry.register(echo_tool_definition())

    assert registry.invoke("echo", {"text": "hello"}) == {"text": "hello"}

    with pytest.raises(ToolValidationError):
        registry.invoke("echo", {"text": 1})
