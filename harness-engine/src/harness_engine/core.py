"""Engine-neutral contracts and lifecycle primitives.

No SDK-specific type is allowed to cross this module boundary.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from types import MappingProxyType
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
class SkillFileContext:
    """Run 内存中的 Skill 文件内容；repr 不泄露文件正文。"""

    path: str
    content: str = field(repr=False)
    content_sha256: str
    skill_id: str | None = None
    skill_version: int | None = None

    def __post_init__(self) -> None:
        if not self.path.strip() or "\x00" in self.path:
            raise ValueError("Skill file path must be nonblank text")
        if not isinstance(self.content, str):
            raise TypeError("Skill file content must be text")
        digest = self.content_sha256.lower()
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError("Skill file digest must be a SHA-256 hex value")
        actual_digest = hashlib.sha256(self.content.encode("utf-8")).hexdigest()
        if actual_digest != digest:
            raise ValueError("Skill file content digest does not match its content")
        if (self.skill_id is None) != (self.skill_version is None):
            raise ValueError("Skill file resource id and version must be provided together")
        if self.skill_id is not None and (
            not self.skill_id.strip() or self.skill_version is None or self.skill_version < 1
        ):
            raise ValueError("Skill file must reference a positive Skill version")
        object.__setattr__(self, "content_sha256", digest)


@dataclass(frozen=True, slots=True)
class EmployeeContext:
    """RunSnapshot 固定的员工、系统指令和 Skill 输入，不含租户凭据。"""

    employee_id: str
    employee_version: int
    system_prompt: str = field(repr=False)
    skill_refs: tuple[tuple[str, int], ...] = ()
    skill_instructions: tuple[str, ...] = field(default=(), repr=False)
    skill_files: tuple[SkillFileContext, ...] = ()
    model_route_id: str | None = None

    def __post_init__(self) -> None:
        if not self.employee_id.strip() or self.employee_version < 1:
            raise ValueError("Employee context requires a fixed employee version")
        if not self.system_prompt.strip():
            raise ValueError("Employee system_prompt must not be blank")
        instructions = tuple(self.skill_instructions)
        if any(not isinstance(item, str) or not item.strip() for item in instructions):
            raise ValueError("Skill instructions must be nonblank text")
        files = tuple(self.skill_files)
        if any(not isinstance(item, SkillFileContext) for item in files):
            raise TypeError("skill_files must contain SkillFileContext values")
        if self.model_route_id is not None and not self.model_route_id.strip():
            raise ValueError("model_route_id must be nonblank when provided")
        refs = tuple((str(skill_id), int(version)) for skill_id, version in self.skill_refs)
        if any(not skill_id.strip() or version < 1 for skill_id, version in refs):
            raise ValueError("skill_refs must contain positive fixed versions")
        if len({skill_id for skill_id, _ in refs}) != len(refs):
            raise ValueError("skill_refs must contain unique Skill ids")
        object.__setattr__(self, "skill_refs", refs)
        object.__setattr__(self, "skill_instructions", instructions)
        object.__setattr__(self, "skill_files", files)


@dataclass(frozen=True, slots=True)
class EngineToolDefinition:
    """模型可见工具的 JSON Schema；写工具不能进入 EngineRequest。"""

    name: str
    description: str
    input_schema: Mapping[str, Any]
    risk_level: str
    tool_id: str | None = None
    tool_version: int | None = None
    remote_tool_name: str | None = None

    def __post_init__(self) -> None:
        if not self.name.strip() or any(character.isspace() for character in self.name):
            raise ValueError("tool name must be a nonblank token")
        if not self.description.strip():
            raise ValueError("tool description must not be blank")
        if not isinstance(self.input_schema, Mapping):
            raise TypeError("tool input_schema must be an object")
        risk_level = self.risk_level.upper().strip()
        if risk_level not in {"READ", "PREVIEW"}:
            raise ValueError("risk_level must be READ or PREVIEW")
        if (self.tool_id is None) != (self.tool_version is None):
            raise ValueError("Tool id and version must be provided together")
        if self.tool_id is not None and (
            not self.tool_id.strip() or self.tool_version is None or self.tool_version < 1
        ):
            raise ValueError("Tool definition must reference a positive ToolVersion")
        if self.remote_tool_name is not None and not self.remote_tool_name.strip():
            raise ValueError("remote_tool_name must be nonblank when provided")
        object.__setattr__(self, "risk_level", risk_level)
        object.__setattr__(self, "input_schema", MappingProxyType(dict(self.input_schema)))


@dataclass(frozen=True, slots=True)
class EngineModelConfig:
    """本次 Engine 调用固定使用的非敏感模型路由配置，不包含 SecretRef 或密钥。"""

    tenant_id: str
    route_id: str
    route_version: int
    connection_id: str
    connection_version: int
    provider_kind: str
    model_id: str
    protocol: str
    endpoint: str
    parameters: Mapping[str, object] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        for name in (
            "tenant_id",
            "route_id",
            "connection_id",
            "provider_kind",
            "model_id",
            "protocol",
            "endpoint",
        ):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} must not be blank")
        if self.route_version < 1 or self.connection_version < 1:
            raise ValueError("model route and connection versions must be positive")
        if not isinstance(self.parameters, Mapping):
            raise TypeError("model parameters must be a mapping")
        object.__setattr__(self, "parameters", MappingProxyType(dict(self.parameters)))


@dataclass(frozen=True, slots=True)
class EngineRequest:
    prompt: str
    session_id: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)
    session: SessionRef | None = None
    run_id: str | None = None
    parent_run_id: str | None = None
    employee_context: EmployeeContext | None = None
    tool_definitions: tuple[EngineToolDefinition, ...] = ()
    harness_home_ref: str | None = None
    model_config: EngineModelConfig | None = None
    max_tokens: int | None = None

    def __post_init__(self) -> None:
        if not self.prompt.strip():
            raise ValueError("prompt must not be blank")
        if self.session_id is not None and not self.session_id.strip():
            raise ValueError("session_id must not be blank")
        if self.harness_home_ref is not None and re.fullmatch(
            r"harness-session:[0-9a-f]{64}", self.harness_home_ref
        ) is None:
            raise ValueError("harness_home_ref must be an opaque harness-session reference")
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
        definitions = tuple(self.tool_definitions)
        if any(not isinstance(item, EngineToolDefinition) for item in definitions):
            raise TypeError("tool_definitions must contain EngineToolDefinition values")
        if len({item.name for item in definitions}) != len(definitions):
            raise ValueError("tool definition names must be unique")
        if self.employee_context is not None and not isinstance(
            self.employee_context, EmployeeContext
        ):
            raise TypeError("employee_context must be an EmployeeContext value")
        if self.model_config is not None and not isinstance(self.model_config, EngineModelConfig):
            raise TypeError("model_config must be an EngineModelConfig value")
        if self.max_tokens is not None and self.max_tokens < 1:
            raise ValueError("max_tokens must be positive when provided")
        object.__setattr__(self, "tool_definitions", definitions)

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
    usage: Mapping[str, object] | None = None
    provider_request_id: str | None = None

    def __post_init__(self) -> None:
        if self.result_ref is not None and not self.result_ref.strip():
            raise ValueError("result_ref must not be blank when provided")
        if self.usage is not None:
            if not isinstance(self.usage, Mapping):
                raise TypeError("usage must be a mapping when provided")
            object.__setattr__(self, "usage", MappingProxyType(dict(self.usage)))
        if self.provider_request_id is not None and not self.provider_request_id.strip():
            raise ValueError("provider_request_id must not be blank when provided")


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
    payload: Mapping[str, Any] = field(default_factory=dict, repr=False)
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
