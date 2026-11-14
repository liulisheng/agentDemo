"""模型、工具调用和审计用量的标准化领域记录。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class UsageStatus(StrEnum):
    EXACT = "EXACT"
    ESTIMATED = "ESTIMATED"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class ModelCallRecord:
    call_id: str
    run_id: str
    tenant_id: str
    user_id: str
    employee_id: str
    session_id: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    usage_status: UsageStatus
    provider_request_id: str | None
    cost_amount: float
    currency: str
    pricing_version: str
    cached_input_tokens: int = 0
    reasoning_tokens: int = 0
    duration_ms: int = 0
    project_id: str | None = None
    channel: str | None = None


@dataclass(frozen=True, slots=True)
class ToolInvocationRecord:
    invocation_id: str
    run_id: str
    tenant_id: str
    tool_name: str
    protocol: str
    version: str
    status: str
    error_category: str | None = None
    duration_ms: int = 0
    retries: int = 0
    input_ref: str | None = None
    output_ref: str | None = None
    approval_ref: str | None = None


@dataclass(frozen=True, slots=True)
class UsageAdjustment:
    adjustment_id: str
    original_call_id: str
    reason: str
    delta_cost_amount: float
    delta_tokens: int


@dataclass(frozen=True, slots=True)
class AuditEvent:
    event_id: str
    tenant_id: str
    actor_id: str
    action: str
    resource_ref: str
    outcome: str
    metadata: dict[str, str]


@dataclass(frozen=True, slots=True)
class RawPayloadRef:
    object_ref: str
    sha256: str
    size_bytes: int
    retention_until: str | None
    encrypted: bool = True


@dataclass(frozen=True, slots=True)
class UsageLedger:
    """按组织和 Run 聚合的不可变用量摘要。"""

    platform_id: str
    tenant_id: str
    run_id: str
    total_tokens: int
    cost_amount: float
    call_count: int
    duration_ms: int = 0
