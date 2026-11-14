"""Engine-neutral contracts and lifecycle primitives.

No SDK-specific type is allowed to cross this module boundary.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Protocol


class EngineError(RuntimeError):
    """Base class for errors raised by an engine-neutral lifecycle."""


class EngineLifecycleError(EngineError):
    """Raised when an operation is invalid for the engine lifecycle state."""


class EngineNotStartedError(EngineLifecycleError):
    """Raised when an operation requires a started engine."""


class EngineClosedError(EngineLifecycleError):
    """Raised when an operation is attempted after close."""


class UnsupportedCapabilityError(EngineError):
    """Raised when an engine cannot provide a requested capability."""


class SessionError(EngineError):
    """Base class for session reference errors."""


class SessionNotFoundError(SessionError):
    """Raised when a session cannot be resumed."""


class SessionMismatchError(SessionError):
    """Raised when a session belongs to another engine."""


class EngineState(str, Enum):
    CREATED = "created"
    STARTED = "started"
    CLOSED = "closed"


# ``EngineLifecycleState`` is a readable alias for callers that prefer the
# longer name; the enum remains one shared type for stable comparisons.
EngineLifecycleState = EngineState


class RunStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class CancelStatus(str, Enum):
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    NOT_RUNNING = "not_running"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class SessionRef:
    """Engine-neutral reference to a persisted or active session."""

    session_id: str
    engine_name: str
    metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.session_id.strip():
            raise ValueError("session_id must not be blank")
        if not self.engine_name.strip():
            raise ValueError("engine_name must not be blank")

    @property
    def engine_session_id(self) -> str:
        """Compatibility name for storage adapters."""

        return self.session_id


@dataclass(frozen=True, slots=True)
class TenantContext:
    platform_id: str
    tenant_id: str
    user_id: str
    roles: tuple[str, ...] = ()
    auth_source: str = "unknown"

    def __post_init__(self) -> None:
        for name in ("platform_id", "tenant_id", "user_id"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} must not be blank")

    @property
    def scope(self) -> tuple[str, str]:
        return self.platform_id, self.tenant_id


@dataclass(frozen=True, slots=True)
class EngineCapabilities:
    streaming: bool = False
    resume: bool = False
    approvals: bool = False
    mcp: bool = False
    cancellation: bool = False
    events: bool = False


@dataclass(frozen=True, slots=True)
class EngineRequest:
    prompt: str
    session_id: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)
    session: SessionRef | None = None
    run_id: str | None = None
    parent_run_id: str | None = None

    def __post_init__(self) -> None:
        if not self.prompt.strip():
            raise ValueError("prompt must not be blank")
        if self.session_id is not None and not self.session_id.strip():
            raise ValueError("session_id must not be blank")
        if (
            self.session is not None
            and self.session_id is not None
            and self.session.session_id != self.session_id
        ):
            raise ValueError("session_id conflicts with session reference")
        for name in ("run_id", "parent_run_id"):
            value = getattr(self, name)
            if value is not None and not value.strip():
                raise ValueError(f"{name} must not be blank")

    @property
    def resolved_session_id(self) -> str | None:
        if self.session is not None:
            return self.session.session_id
        return self.session_id


@dataclass(frozen=True, slots=True)
class EngineResult:
    text: str
    finish_reason: str
    completed_at: datetime
    capabilities: EngineCapabilities = field(default_factory=EngineCapabilities)
    run_id: str | None = None
    session: SessionRef | None = None
    status: RunStatus = RunStatus.SUCCEEDED
    result_ref: str | None = None

    def __post_init__(self) -> None:
        if self.result_ref is not None and not self.result_ref.strip():
            raise ValueError("result_ref must not be blank when provided")


RunResult = EngineResult


@dataclass(frozen=True, slots=True)
class CancelResult:
    status: CancelStatus
    session_id: str
    run_id: str | None = None

    def __post_init__(self) -> None:
        if not self.session_id.strip():
            raise ValueError("session_id must not be blank")

    @property
    def confirmed(self) -> bool:
        return self.status is CancelStatus.CANCELLED


@dataclass(frozen=True, slots=True)
class HarnessEvent:
    """A vendor-neutral event that can later describe skills or subagents."""

    event_type: str
    session_id: str | None = None
    run_id: str | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)
    occurred_at: datetime = field(
        default_factory=lambda: datetime.now(UTC)
    )

    def __post_init__(self) -> None:
        if not self.event_type.strip():
            raise ValueError("event_type must not be blank")

    @property
    def kind(self) -> str:
        return self.event_type


@dataclass(frozen=True, slots=True)
class VisibleTextDelta:
    """One ordered, user-visible text fragment emitted during a run.

    Engines must filter provider-specific reasoning, tool payloads, and other
    non-surface data before constructing this value.
    """

    session_id: str
    run_id: str
    sequence: int
    text: str
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if not self.session_id.strip():
            raise ValueError("session_id must not be blank")
        if not self.run_id.strip():
            raise ValueError("run_id must not be blank")
        if self.sequence <= 0:
            raise ValueError("sequence must be positive")
        if not self.text:
            raise ValueError("text must not be empty")


EngineEvent = HarnessEvent | VisibleTextDelta
EventHandler = Callable[[EngineEvent], None]


class LifecycleEngine:
    """Reusable state guard for synchronous engine implementations."""

    def __init__(self) -> None:
        self._state = EngineState.CREATED

    @property
    def state(self) -> EngineState:
        return self._state

    def start(self) -> None:
        if self._state is EngineState.CLOSED:
            raise EngineClosedError("engine is closed")
        if self._state is EngineState.STARTED:
            return
        self._start_impl()
        self._state = EngineState.STARTED

    def close(self) -> None:
        if self._state is EngineState.CLOSED:
            return
        try:
            self._close_impl()
        finally:
            self._state = EngineState.CLOSED

    def _require_started(self) -> None:
        if self._state is EngineState.CLOSED:
            raise EngineClosedError("engine is closed")
        if self._state is not EngineState.STARTED:
            raise EngineNotStartedError("engine has not been started")

    def _start_impl(self) -> None:
        return None

    def _close_impl(self) -> None:
        return None


class HarnessEngine(Protocol):
    """Stable interface implemented by Fake and vendor adapters."""

    @property
    def capabilities(self) -> EngineCapabilities: ...

    @property
    def state(self) -> EngineState: ...

    def start(self) -> None: ...

    def create_session(self) -> SessionRef: ...

    def resume_session(self, ref: SessionRef) -> SessionRef: ...

    def run(
        self, request: EngineRequest, on_event: EventHandler | None = None
    ) -> EngineResult: ...

    def cancel(self, session_id: str) -> CancelResult: ...

    def close(self) -> None: ...
