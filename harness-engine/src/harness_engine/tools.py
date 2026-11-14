"""Trusted Python tool definitions, discovery, and registration.

This module intentionally knows nothing about MCP transport or a vendor
engine.  It is the policy boundary that decides which installed tools may be
loaded and gives the execution layer a stable definition to consume.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from typing import Any

ToolHandler = Callable[[Mapping[str, Any]], Any]
TOOL_ENTRY_POINT_GROUP = "agent_harness.tools"


class ToolError(RuntimeError):
    """Base class for public tool registry and execution errors."""


class ToolNotFoundError(ToolError):
    """Raised when a tool name is not registered."""


class ToolNotAllowedError(ToolError):
    """Raised when a tool is not in the configured allowlist."""


class ToolLoadError(ToolError):
    """Raised when an entry point cannot produce a valid definition."""


class ToolValidationError(ToolError):
    """Raised when tool input does not match its JSON Schema."""


class ToolExecutionError(ToolError):
    """Raised when a trusted tool handler fails."""


def _type_matches(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, Mapping)
    if expected == "array":
        return isinstance(value, (list, tuple))
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    raise ToolValidationError(f"unsupported JSON Schema type {expected!r}")


def validate_tool_input(schema: Mapping[str, Any], value: Any) -> None:
    """Validate the supported JSON Schema subset used by trusted tools."""

    if not isinstance(schema, Mapping):
        raise ToolValidationError("tool input schema must be an object")
    _validate_schema_node(schema, value, path="$")


def _validate_schema_node(schema: Mapping[str, Any], value: Any, *, path: str) -> None:
    expected = schema.get("type")
    if expected is not None:
        expected_types = expected if isinstance(expected, list) else [expected]
        if not any(
            isinstance(item, str) and _type_matches(value, item)
            for item in expected_types
        ):
            raise ToolValidationError(
                f"{path} must have type {expected!r}, got {type(value).__name__}"
            )

    if "enum" in schema and value not in schema["enum"]:
        raise ToolValidationError(f"{path} must be one of the allowed values")

    if isinstance(value, str):
        min_length = schema.get("minLength")
        if min_length is not None and len(value) < min_length:
            raise ToolValidationError(f"{path} is shorter than minLength")
        pattern = schema.get("pattern")
        if pattern is not None and re.search(str(pattern), value) is None:
            raise ToolValidationError(f"{path} does not match pattern")

    if isinstance(value, Mapping):
        properties = schema.get("properties", {})
        if not isinstance(properties, Mapping):
            raise ToolValidationError(f"{path}.properties must be an object")
        required = schema.get("required", ())
        for name in required:
            if name not in value:
                raise ToolValidationError(f"{path}.{name} is required")
        if schema.get("additionalProperties", True) is False:
            unknown = [name for name in value if name not in properties]
            if unknown:
                raise ToolValidationError(f"{path}.{unknown[0]} is not allowed")
        for name, property_schema in properties.items():
            if name in value:
                if not isinstance(property_schema, Mapping):
                    raise ToolValidationError(f"{path}.{name} has an invalid schema")
                _validate_schema_node(
                    property_schema,
                    value[name],
                    path=f"{path}.{name}",
                )

    if isinstance(value, (list, tuple)):
        item_schema = schema.get("items")
        if item_schema is not None:
            if not isinstance(item_schema, Mapping):
                raise ToolValidationError(f"{path}.items must be an object")
            for index, item in enumerate(value):
                _validate_schema_node(item_schema, item, path=f"{path}[{index}]")


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """Engine-neutral metadata and trusted handler for one tool."""

    name: str
    description: str
    input_schema: Mapping[str, Any]
    handler: ToolHandler
    plugin_name: str | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("tool name must not be blank")
        if any(character.isspace() for character in self.name):
            raise ValueError("tool name must not contain whitespace")
        if not self.description.strip():
            raise ValueError("tool description must not be blank")
        if not isinstance(self.input_schema, Mapping):
            raise TypeError("tool input schema must be an object")
        if not callable(self.handler):
            raise TypeError("tool handler must be callable")


class ToolRegistry:
    """Explicit registry with allowlisted entry-point discovery."""

    def __init__(self, allowlist: Iterable[str] = ()) -> None:
        self._allowlist = frozenset(self._normalize(name) for name in allowlist)
        self._tools: dict[str, ToolDefinition] = {}

    @staticmethod
    def _normalize(name: str) -> str:
        normalized = name.strip().lower()
        if not normalized:
            raise ValueError("tool name must not be blank")
        return normalized

    @property
    def allowlist(self) -> tuple[str, ...]:
        return tuple(sorted(self._allowlist))

    def register(self, tool: ToolDefinition) -> None:
        name = self._normalize(tool.name)
        if self._allowlist and name not in self._allowlist:
            raise ToolNotAllowedError(f"tool {tool.name!r} is not enabled")
        if name in self._tools:
            raise ValueError(f"tool {name!r} already registered")
        self._tools[name] = tool

    def discover(self) -> tuple[str, ...]:
        """Load only entry points explicitly named in the allowlist."""

        if not self._allowlist:
            return ()
        discovered: list[str] = []
        for point in entry_points(group=TOOL_ENTRY_POINT_GROUP):
            point_name = self._normalize(point.name)
            if point_name not in self._allowlist:
                continue
            try:
                loaded = point.load()
                candidate = loaded() if callable(loaded) else loaded
                if not isinstance(candidate, ToolDefinition):
                    raise TypeError("entry point did not return ToolDefinition")
                if self._normalize(candidate.name) != point_name:
                    raise ToolLoadError(
                        f"entry point {point.name!r} returned tool "
                        f"{candidate.name!r} with a different name"
                    )
                self.register(candidate)
            except ToolError:
                raise
            except Exception as exc:
                raise ToolLoadError(
                    f"failed to load tool entry point {point.name!r}"
                ) from exc
            discovered.append(point_name)
        return tuple(sorted(discovered))

    def list_tools(self) -> tuple[ToolDefinition, ...]:
        return tuple(self._tools[name] for name in sorted(self._tools))

    def get(self, name: str) -> ToolDefinition:
        normalized = self._normalize(name)
        try:
            return self._tools[normalized]
        except KeyError as exc:
            raise ToolNotFoundError(f"tool {name!r} is not registered") from exc

    def invoke(self, name: str, arguments: Mapping[str, Any]) -> Any:
        tool = self.get(name)
        validate_tool_input(tool.input_schema, arguments)
        try:
            return tool.handler(arguments)
        except Exception as exc:
            raise ToolExecutionError(f"tool {tool.name!r} failed") from exc


def echo_tool_definition() -> ToolDefinition:
    """Return the no-side-effect example tool used by offline verification."""

    return ToolDefinition(
        name="echo",
        description="Return the supplied text without side effects.",
        input_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
        handler=lambda arguments: {"text": arguments["text"]},
        plugin_name="builtin",
    )


def build_tool_registry(enabled: Iterable[str]) -> ToolRegistry:
    """Build a registry from entry points plus the bundled echo example."""

    registry = ToolRegistry(allowlist=enabled)
    registry.discover()
    enabled_names = set(registry.allowlist)
    if "echo" in enabled_names and all(tool.name != "echo" for tool in registry.list_tools()):
        registry.register(echo_tool_definition())
    return registry
