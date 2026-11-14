"""按声明顺序组装业务上下文并记录裁剪原因。"""

from __future__ import annotations

import hashlib
from dataclasses import replace

from agent_platform.domain.context import (
    BusinessContextPolicy,
    ContextSnapshot,
    ContextSource,
    ContextSourceType,
)


def _source(source_type: ContextSourceType, reference: str, content: str) -> ContextSource:
    return ContextSource(
        source_type=source_type,
        reference=reference,
        token_estimate=max(1, len(content.split())),
        content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
    )


class BusinessContextAssembler:
    """平台只管理业务引用和预算，Runtime 自己负责私有上下文压缩。"""

    def __init__(self, policy: BusinessContextPolicy) -> None:
        self.policy = policy

    def assemble(
        self,
        *,
        run_id: str,
        trace_id: str,
        agent_ref: str,
        skill_refs: tuple[str, ...],
        recent_messages: tuple[str, ...],
        memory_refs: tuple[str, ...],
        tool_result_refs: tuple[str, ...],
    ) -> ContextSnapshot:
        candidates = [_source(ContextSourceType.AGENT, agent_ref, agent_ref)]
        candidates.extend(_source(ContextSourceType.SKILL, ref, ref) for ref in skill_refs)
        candidates.extend(_source(ContextSourceType.MESSAGE, f"message-{index}", content) for index, content in enumerate(recent_messages))
        candidates.extend(_source(ContextSourceType.MEMORY, ref, ref) for ref in memory_refs)
        candidates.extend(_source(ContextSourceType.TOOL_RESULT, ref, ref) for ref in tool_result_refs)
        included: list[ContextSource] = []
        excluded: list[ContextSource] = []
        total = 0
        for candidate in candidates:
            if total + candidate.token_estimate <= self.policy.max_tokens:
                included.append(candidate)
                total += candidate.token_estimate
            else:
                excluded.append(replace(candidate, included=False, reason="budget"))
        return ContextSnapshot.new(
            run_id,
            trace_id,
            tuple(included),
            tuple(excluded),
            {"context_policy": self.policy.compaction_strategy},
        )
