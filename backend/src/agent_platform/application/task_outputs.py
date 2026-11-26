"""跨引擎 TaskOutput 摘要和上下文封装。"""

from __future__ import annotations

from collections.abc import Sequence

from agent_platform.domain.orchestration import TaskOutput


class TaskOutputContextService:
    """只向下游 Runtime 暴露摘要、结构化引用和 Artifact ID。"""

    def build_context(self, outputs: Sequence[TaskOutput], *, max_summary_chars: int = 4000) -> tuple[dict[str, object], ...]:
        return tuple(
            {
                "task_id": output.task_id,
                "summary": output.summary[:max_summary_chars],
                "structured_output_ref": output.structured_output_ref,
                "artifact_ids": list(output.artifact_ids),
                "source_engine": output.source_engine,
                "source_model": output.source_model,
                "content_sha256": output.content_sha256,
            }
            for output in outputs
        )
