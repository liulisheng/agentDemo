from types import SimpleNamespace

import pytest

from harness_engine.tools import (
    ToolDefinition,
    ToolLoadError,
    ToolNotFoundError,
    ToolRegistry,
)


def _echo(arguments):
    return arguments["text"]


def test_tool_definition_rejects_blank_name_and_invalid_schema():
    with pytest.raises(ValueError, match="name"):
        ToolDefinition(
            name=" ",
            description="echo",
            input_schema={"type": "object"},
            handler=_echo,
        )

    with pytest.raises(TypeError, match="schema"):
        ToolDefinition(
            name="echo",
            description="echo",
            input_schema=[],
            handler=_echo,
        )


def test_registry_discovers_only_explicitly_allowlisted_entry_points(monkeypatch):
    loaded: list[str] = []

    def load_allowed():
        loaded.append("allowed")
        return ToolDefinition(
            name="allowed",
            description="allowed tool",
            input_schema={"type": "object"},
            handler=_echo,
        )

    def load_blocked():
        loaded.append("blocked")
        return ToolDefinition(
            name="blocked",
            description="blocked tool",
            input_schema={"type": "object"},
            handler=_echo,
        )

    monkeypatch.setattr(
        "harness_engine.tools.entry_points",
        lambda group: [
            SimpleNamespace(name="allowed", load=load_allowed),
            SimpleNamespace(name="blocked", load=load_blocked),
        ],
    )

    registry = ToolRegistry(allowlist=("allowed",))

    assert registry.discover() == ("allowed",)
    assert loaded == ["allowed"]
    assert [tool.name for tool in registry.list_tools()] == ["allowed"]


def test_registry_rejects_duplicate_names_and_unknown_tools():
    registry = ToolRegistry()
    definition = ToolDefinition(
        name="echo",
        description="echo",
        input_schema={"type": "object"},
        handler=_echo,
    )
    registry.register(definition)

    with pytest.raises(ValueError, match="already registered"):
        registry.register(definition)

    with pytest.raises(ToolNotFoundError, match="missing"):
        registry.get("missing")


def test_discovery_rejects_entry_point_name_mismatch(monkeypatch):
    def load_wrong_name():
        return ToolDefinition(
            name="different",
            description="wrong name",
            input_schema={"type": "object"},
            handler=_echo,
        )

    monkeypatch.setattr(
        "harness_engine.tools.entry_points",
        lambda group: [SimpleNamespace(name="allowed", load=load_wrong_name)],
    )

    with pytest.raises(ToolLoadError, match="name"):
        ToolRegistry(allowlist=("allowed",)).discover()
