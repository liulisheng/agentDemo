"""Session、Run、Trace 和 Usage 的平台侧索引模型。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

TraceId = str

from .context import ContextSnapshot
from .execution import RuntimeReference, RuntimeStatus, UsageRecord


class SpanType(StrEnum):
    """最小闭环中可查询的 Trace Span 类型。"""

    SESSION = "session"
    RUN = "run"
    SKILL = "skill"
    TOOL = "tool"
    RUNTIME = "runtime"
    MODEL = "model"
    RESULT = "result"
    CONTEXT = "context"
    MEMORY = "memory"
    APPROVAL = "approval"
    SANDBOX = "sandbox"
    CHANNEL = "channel"


@dataclass(frozen=True, slots=True)
class TraceSpan:
    """Trace 索引只保存元数据和摘要，不保存敏感正文。"""

    span_id: str
    trace_id: str
    parent_span_id: str | None
    run_id: str
    span_type: SpanType
    status: str
    attributes: Mapping[str, object] = field(default_factory=dict)
    sequence: int = 0


@dataclass(frozen=True, slots=True)
class RunSnapshot:
    """Run 启动时复制的版本快照，后续发布不会改变它。"""

    employee_id: str
    employee_version: int
    skill_version_ids: tuple[str, ...]
    tool_version_ids: tuple[str, ...]
    execution_profile_id: str


@dataclass(slots=True)
class SessionRecord:
    """平台 Session 与部署、Trace 和 Runtime opaque 引用的关联。"""

    session_id: str
    platform_id: str
    tenant_id: str
    deployment_id: str
    trace_id: str
    employee_version: int
    runtime_session_ref: str | None = None
    status: str = "ACTIVE"
    span_id: str = ""


@dataclass(slots=True)
class RunRecord:
    """一次同步 Run 的状态、结果和不可变版本快照。"""

    run_id: str
    session_id: str
    platform_id: str
    tenant_id: str
    trace_id: str
    prompt_digest: str
    snapshot: RunSnapshot
    status: RuntimeStatus
    result_text: str = ""
    runtime_reference: RuntimeReference | None = None
    error_category: str | None = None
    context_snapshot: ContextSnapshot | None = None
    span_id: str = ""


@dataclass(frozen=True, slots=True)
class RunTimeline:
    """按生成顺序返回 Run、Span、Runtime 引用和 Usage。"""

    run: RunRecord
    spans: tuple[TraceSpan, ...]
    usage: UsageRecord
    runtime_reference: RuntimeReference | None
