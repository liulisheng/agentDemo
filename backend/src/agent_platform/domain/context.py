"""企业业务上下文快照和预算策略，不读取 Runtime 私有历史。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from uuid import uuid4


class ContextSourceType(StrEnum):
    AGENT = "agent"
    SKILL = "skill"
    MESSAGE = "message"
    MEMORY = "memory"
    TOOL_RESULT = "tool_result"


@dataclass(frozen=True, slots=True)
class BusinessContextPolicy:
    max_tokens: int = 4096
    compaction_strategy: str = "truncate"

    def __post_init__(self) -> None:
        if self.max_tokens <= 0:
            raise ValueError("max_tokens must be positive")
        if self.compaction_strategy not in {"truncate", "summary", "reject"}:
            raise ValueError("unsupported compaction strategy")


@dataclass(frozen=True, slots=True)
class ContextSource:
    source_type: ContextSourceType
    reference: str
    token_estimate: int
    content_hash: str
    included: bool = True
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ContextSnapshot:
    snapshot_id: str
    run_id: str
    trace_id: str
    sources: tuple[ContextSource, ...]
    total_tokens: int
    excluded_sources: tuple[ContextSource, ...]
    runtime_handoff: dict[str, str] = field(default_factory=dict)

    @classmethod
    def new(
        cls,
        run_id: str,
        trace_id: str,
        sources: tuple[ContextSource, ...],
        excluded_sources: tuple[ContextSource, ...],
        runtime_handoff: dict[str, str] | None = None,
    ) -> ContextSnapshot:
        return cls(
            snapshot_id=f"context-{uuid4().hex}",
            run_id=run_id,
            trace_id=trace_id,
            sources=sources,
            total_tokens=sum(source.token_estimate for source in sources),
            excluded_sources=excluded_sources,
            runtime_handoff=runtime_handoff or {},
        )
