"""平台与供应商 Runtime 之间的最小生命周期端口。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from agent_platform.domain.execution import RuntimeStatus, UsageRecord
from agent_platform.domain.observability import SkillFilePackageSnapshot


@dataclass(frozen=True, slots=True)
class RuntimeCapabilities:
    """调用方可据此判断 Runtime 是否支持恢复、取消、审批和工作区。"""

    streaming: bool = False
    resume: bool = True
    cancellation: bool = False
    approvals: bool = False
    mcp: bool = False
    workspace: bool = False


@dataclass(frozen=True, slots=True)
class RuntimeRequest:
    """平台传给 Runtime 的业务输入，不包含供应商私有上下文格式。"""

    run_id: str
    session_ref: str | None
    prompt: str
    skill_instructions: Sequence[str]
    allowed_tools: Sequence[str]
    workspace_ref: str | None = None
    thread_ref: str | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)
    system_prompt: str = ""
    skill_file_packages: Sequence[SkillFilePackageSnapshot] = ()

    def __post_init__(self) -> None:
        for field_name in ("run_id", "prompt"):
            if not getattr(self, field_name).strip():
                raise ValueError(f"{field_name} must not be blank")


@dataclass(frozen=True, slots=True)
class RuntimeEvent:
    """被适配器规范化的 Runtime 事件。"""

    event_type: str
    span_type: str
    payload: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RuntimeResult:
    """与引擎无关的执行结果和 opaque 引用。"""

    text: str
    status: RuntimeStatus
    runtime_session_ref: str
    runtime_result_ref: str
    usage: UsageRecord
    events: tuple[RuntimeEvent, ...]
    capabilities: RuntimeCapabilities = field(default_factory=RuntimeCapabilities)
    runtime_thread_ref: str | None = None
    runtime_workspace_ref: str | None = None


class RuntimeAdapter(Protocol):
    """所有 Runtime 适配器必须实现的最小生命周期协议。"""

    capabilities: RuntimeCapabilities

    def create_session(self) -> str:
        ...

    def resume_session(self, session_ref: str) -> str:
        ...

    def run(self, request: RuntimeRequest) -> RuntimeResult:
        ...

    def cancel(self, session_ref: str) -> bool:
        ...
