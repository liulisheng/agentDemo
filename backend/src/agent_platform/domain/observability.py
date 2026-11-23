"""Session、Run、Trace 和 Usage 的平台侧索引模型。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

TraceId = str

from .context import ContextSnapshot
from .execution import RuntimeReference, RuntimeStatus, UsageRecord
from .model_routes import ModelRouteSnapshot


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
class SkillFileSnapshotEntry:
    """Run 固定的 Skill 文件引用；对象内容仍由租户 MediaAsset 授权读取。"""

    file_path: str
    media_asset_id: str
    mime_type: str
    size_bytes: int
    content_sha256: str
    asset_platform_id: str = ""
    asset_tenant_id: str = ""
    source_mode: str = "LOCAL"


@dataclass(frozen=True, slots=True)
class SkillFilePackageSnapshot:
    """Run 固定的 Skill 文件入口、清单摘要和只读文件 refs。"""

    skill_id: str
    skill_version: int
    entry_path: str
    manifest_sha256: str
    files: tuple[SkillFileSnapshotEntry, ...]


@dataclass(frozen=True, slots=True)
class HubResourceSnapshot:
    """Run 使用的 Hub 来源固定记录；保存版本/摘要/模式，不保存 Package 正文。"""

    local_resource_type: str
    local_resource_id: str
    local_resource_version: int
    hub_resource_id: str
    hub_version: int
    manifest_sha256: str
    mode: str


@dataclass(frozen=True, slots=True)
class RunSnapshot:
    """Run 启动时复制的版本快照，后续发布不会改变它。"""

    employee_id: str
    employee_version: int
    skill_version_ids: tuple[str, ...]
    tool_version_ids: tuple[str, ...]
    execution_profile_id: str
    engine_name: str | None = None
    skill_version_refs: tuple[tuple[str, int], ...] = ()
    tool_version_refs: tuple[tuple[str, int], ...] = ()
    execution_profile_version: int = 1
    model_route_id: str | None = None
    model_alias: str | None = None
    model_route_snapshot: ModelRouteSnapshot | None = None
    runtime_config_version: int | None = None
    runtime_profile_source: str | None = None
    runtime_model_route_source: str | None = None
    hub_resource_refs: tuple[HubResourceSnapshot, ...] = ()
    skill_file_packages: tuple[SkillFilePackageSnapshot, ...] = ()

    def __post_init__(self) -> None:
        if (
            self.model_route_snapshot is not None
            and self.model_route_id != self.model_route_snapshot.route_id
        ):
            raise ValueError("model_route_snapshot must match model_route_id")
        if self.model_alias is not None and not self.model_alias.strip():
            raise ValueError("model_alias must be nonblank when provided")


@dataclass(slots=True)
class SessionRecord:
    """平台 Session 与部署、Trace 和 Runtime opaque 引用的关联。"""

    session_id: str
    platform_id: str
    tenant_id: str
    deployment_id: str
    trace_id: str
    employee_version: int
    employee_id: str = ""
    execution_profile_id: str = ""
    execution_profile_version: int = 1
    model_route_id: str | None = None
    model_alias: str | None = None
    model_route_snapshot: ModelRouteSnapshot | None = None
    runtime_config_version: int | None = None
    runtime_profile_source: str | None = None
    runtime_model_route_source: str | None = None
    engine_name: str = ""
    runtime_session_ref: str | None = None
    harness_home_ref: str | None = None
    status: str = "ACTIVE"
    span_id: str = ""


@dataclass(slots=True)
class RunRecord:
    """一次 Run 的提交者、状态、结果和不可变版本快照。"""

    run_id: str
    session_id: str
    platform_id: str
    tenant_id: str
    trace_id: str
    prompt_digest: str
    snapshot: RunSnapshot
    status: RuntimeStatus
    result_text: str = ""
    result_ref: str | None = None
    runtime_reference: RuntimeReference | None = None
    error_category: str | None = None
    context_snapshot: ContextSnapshot | None = None
    span_id: str = ""
    submitted_by_user_id: str | None = None


@dataclass(frozen=True, slots=True)
class RunTimeline:
    """按生成顺序返回 Run、Span、Runtime 引用和 Usage。"""

    run: RunRecord
    spans: tuple[TraceSpan, ...]
    usage: UsageRecord
    runtime_reference: RuntimeReference | None
