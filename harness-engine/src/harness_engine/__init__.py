"""Public package for the pluggable Harness orchestration layer."""

from .core import (
    CancelResult,
    CancelStatus,
    EngineCapabilities,
    EngineClosedError,
    EngineError,
    EngineLifecycleError,
    EngineLifecycleState,
    EngineNotStartedError,
    EngineRequest,
    EngineResult,
    EngineState,
    HarnessEngine,
    HarnessEvent,
    LifecycleEngine,
    RunResult,
    RunStatus,
    SessionMismatchError,
    SessionNotFoundError,
    SessionRef,
    TenantContext,
    UnsupportedCapabilityError,
    VisibleTextDelta,
)
from .session_service import PersistentSessionService
from .tools import (
    ToolDefinition,
    ToolError,
    ToolExecutionError,
    ToolLoadError,
    ToolNotAllowedError,
    ToolNotFoundError,
    ToolRegistry,
    ToolValidationError,
    build_tool_registry,
    echo_tool_definition,
    validate_tool_input,
)

__all__ = [
    "CancelResult",
    "CancelStatus",
    "EngineCapabilities",
    "EngineClosedError",
    "EngineError",
    "EngineLifecycleError",
    "EngineLifecycleState",
    "EngineNotStartedError",
    "EngineRequest",
    "EngineResult",
    "EngineState",
    "HarnessEngine",
    "HarnessEvent",
    "LifecycleEngine",
    "McpToolServer",
    "McpUnavailableError",
    "PersistentSessionService",
    "RunResult",
    "RunStatus",
    "SessionMismatchError",
    "SessionNotFoundError",
    "SessionRef",
    "TenantContext",
    "ToolDefinition",
    "ToolError",
    "ToolExecutionError",
    "ToolLoadError",
    "ToolNotAllowedError",
    "ToolNotFoundError",
    "ToolRegistry",
    "ToolValidationError",
    "UnsupportedCapabilityError",
    "VisibleTextDelta",
    "build_tool_registry",
    "echo_tool_definition",
    "validate_tool_input",
]


def __getattr__(name: str):
    # Keep ``python -m harness_engine.mcp_server`` from importing the module
    # twice while preserving the convenient package-level export.
    if name in {"McpToolServer", "McpUnavailableError"}:
        from .mcp_server import McpToolServer, McpUnavailableError

        exports = {
            "McpToolServer": McpToolServer,
            "McpUnavailableError": McpUnavailableError,
        }
        return exports[name]
    raise AttributeError(name)
