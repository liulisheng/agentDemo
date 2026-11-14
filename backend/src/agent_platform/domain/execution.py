"""Runtime 执行状态、用量和不透明引用。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class RuntimeStatus(StrEnum):
    """平台统一的 Runtime 结果状态。"""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class UsageRecord:
    """模型和 Fake Runtime 可提供的最小用量记录。"""

    input_tokens: int
    output_tokens: int
    total_tokens: int
    source: str = "ESTIMATED"
    estimated: bool = True

    def __post_init__(self) -> None:
        if min(self.input_tokens, self.output_tokens, self.total_tokens) < 0:
            raise ValueError("token counts must not be negative")
        if self.total_tokens != self.input_tokens + self.output_tokens:
            raise ValueError("total_tokens must equal input plus output tokens")


@dataclass(frozen=True, slots=True)
class RuntimeReference:
    """保存 Runtime 私有 Session/Result 的 opaque 引用，不解析其内部格式。"""

    engine_name: str
    runtime_session_ref: str
    runtime_result_ref: str | None = None

    def __post_init__(self) -> None:
        if not self.engine_name.strip() or not self.runtime_session_ref.strip():
            raise ValueError("runtime reference fields must not be blank")
