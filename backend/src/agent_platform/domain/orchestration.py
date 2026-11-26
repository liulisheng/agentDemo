"""团队编排、任务节点、Attempt 和事件领域对象。"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum

from agent_platform.domain.common import DomainError, _require_text, generate_database_id


class OrchestrationStatus(StrEnum):
    PLANNING = "PLANNING"
    AWAITING_PLAN_APPROVAL = "AWAITING_PLAN_APPROVAL"
    READY = "READY"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    CANCELLING = "CANCELLING"
    SUCCEEDED = "SUCCEEDED"
    PARTIAL_FAILED = "PARTIAL_FAILED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    UNKNOWN = "UNKNOWN"


class TaskStatus(StrEnum):
    PROPOSED = "PROPOSED"
    READY = "READY"
    ASSIGNED = "ASSIGNED"
    CLAIMED = "CLAIMED"
    RUNNING = "RUNNING"
    BLOCKED = "BLOCKED"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    SKIPPED = "SKIPPED"


class AssignmentSource(StrEnum):
    USER = "USER"
    ROUTER = "ROUTER"
    SCHEDULER = "SCHEDULER"


@dataclass(frozen=True, slots=True)
class OrchestrationRun:
    """一次用户请求的父级编排运行。"""

    orchestration_id: str
    platform_id: str
    tenant_id: str
    conversation_id: str
    team_id: str
    team_version: int
    plan_revision: int = 1
    status: OrchestrationStatus | str = OrchestrationStatus.PLANNING
    approval_mode: str = "CONFIRM"
    idempotency_key: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        for name in ("orchestration_id", "platform_id", "tenant_id", "conversation_id", "team_id"):
            _require_text(getattr(self, name), name)
        if self.team_version < 1 or self.plan_revision < 1:
            raise ValueError("team_version and plan_revision must be positive")
        object.__setattr__(self, "status", OrchestrationStatus(self.status))

    def transition(self, status: OrchestrationStatus | str) -> OrchestrationRun:
        target = OrchestrationStatus(status)
        allowed = {
            OrchestrationStatus.PLANNING: {OrchestrationStatus.AWAITING_PLAN_APPROVAL, OrchestrationStatus.READY, OrchestrationStatus.CANCELLED, OrchestrationStatus.FAILED},
            OrchestrationStatus.AWAITING_PLAN_APPROVAL: {OrchestrationStatus.READY, OrchestrationStatus.CANCELLED},
            OrchestrationStatus.READY: {OrchestrationStatus.RUNNING, OrchestrationStatus.PAUSED, OrchestrationStatus.CANCELLING, OrchestrationStatus.CANCELLED},
            OrchestrationStatus.RUNNING: {OrchestrationStatus.PAUSED, OrchestrationStatus.CANCELLING, OrchestrationStatus.SUCCEEDED, OrchestrationStatus.PARTIAL_FAILED, OrchestrationStatus.FAILED, OrchestrationStatus.UNKNOWN},
            OrchestrationStatus.PAUSED: {OrchestrationStatus.READY, OrchestrationStatus.CANCELLING, OrchestrationStatus.CANCELLED, OrchestrationStatus.SUCCEEDED, OrchestrationStatus.PARTIAL_FAILED, OrchestrationStatus.FAILED},
            OrchestrationStatus.CANCELLING: {OrchestrationStatus.CANCELLED, OrchestrationStatus.PARTIAL_FAILED, OrchestrationStatus.FAILED},
            OrchestrationStatus.PARTIAL_FAILED: {OrchestrationStatus.READY, OrchestrationStatus.CANCELLED},
            OrchestrationStatus.FAILED: {OrchestrationStatus.READY, OrchestrationStatus.CANCELLED},
            OrchestrationStatus.UNKNOWN: {OrchestrationStatus.RUNNING, OrchestrationStatus.FAILED, OrchestrationStatus.CANCELLED},
        }
        if target not in allowed.get(self.status, set()):
            raise DomainError("INVALID_STATE", f"Orchestration 不允许从 {self.status} 转为 {target}")
        return replace(self, status=target, updated_at=datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class TaskNode:
    """DAG 中的任务节点和当前分配状态。"""

    task_id: str
    orchestration_id: str
    client_key: str
    title: str
    instruction_ref: str = ""
    required: bool = True
    required_capabilities: tuple[str, ...] = ()
    preferred_engine_name: str | None = None
    preferred_member_id: str | None = None
    assigned_member_id: str | None = None
    assignment_source: AssignmentSource | str | None = None
    status: TaskStatus | str = TaskStatus.PROPOSED
    attempt: int = 0
    max_attempts: int = 3
    output_ref: str | None = None
    error_code: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = None
    finished_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in ("task_id", "orchestration_id", "client_key", "title"):
            _require_text(getattr(self, name), name)
        if self.attempt < 0 or self.max_attempts < 1:
            raise ValueError("task attempts are invalid")
        object.__setattr__(self, "status", TaskStatus(self.status))
        object.__setattr__(self, "required_capabilities", tuple(sorted(set(self.required_capabilities))))
        if self.assignment_source is not None:
            object.__setattr__(self, "assignment_source", AssignmentSource(self.assignment_source))


@dataclass(frozen=True, slots=True)
class TaskDependency:
    """任务 DAG 的一条依赖边。"""

    task_id: str
    depends_on_task_id: str
    condition: str = "SUCCEEDED"

    def __post_init__(self) -> None:
        _require_text(self.task_id, "task_id")
        _require_text(self.depends_on_task_id, "depends_on_task_id")
        if self.task_id == self.depends_on_task_id:
            raise ValueError("task cannot depend on itself")
        if self.condition not in {"SUCCEEDED", "TERMINAL"}:
            raise ValueError("unsupported dependency condition")


@dataclass(frozen=True, slots=True)
class TaskAttempt:
    """任务一次执行尝试、幂等键和 Worker fencing token。"""

    attempt_id: str
    task_id: str
    attempt_no: int
    idempotency_key: str
    status: str = "QUEUED"
    worker_id: str | None = None
    fencing_token: int = 0
    run_id: str | None = None
    error_code: str | None = None
    worker_pool: str | None = None

    def __post_init__(self) -> None:
        for name in ("attempt_id", "task_id", "idempotency_key"):
            _require_text(getattr(self, name), name)
        if self.attempt_no < 1 or self.fencing_token < 0:
            raise ValueError("attempt values are invalid")


@dataclass(frozen=True, slots=True)
class TaskOutput:
    """跨引擎传递的脱敏任务结果，不包含 Runtime 私有 Session。"""

    task_id: str
    summary: str
    structured_output_ref: str | None = None
    artifact_ids: tuple[str, ...] = ()
    source_engine: str | None = None
    source_model: str | None = None
    content_sha256: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.task_id, "task_id")
        _require_text(self.summary, "summary")
        object.__setattr__(self, "artifact_ids", tuple(self.artifact_ids))


@dataclass(frozen=True, slots=True)
class TaskEvent:
    """任务/编排追加事件；sequence 由租户范围 Repository 分配。"""

    event_id: str
    orchestration_id: str
    event_type: str
    task_id: str | None = None
    attempt_id: str | None = None
    actor_type: str = "SYSTEM"
    actor_id: str | None = None
    payload: dict[str, object] = field(default_factory=dict)
    sequence_no: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def new(cls, orchestration_id: str, event_type: str, **kwargs: object) -> TaskEvent:
        return cls(generate_database_id(), orchestration_id, event_type, **kwargs)

    def __post_init__(self) -> None:
        for name in ("event_id", "orchestration_id", "event_type"):
            _require_text(getattr(self, name), name)
        if self.sequence_no < 0:
            raise ValueError("sequence_no must not be negative")
