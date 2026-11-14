"""Trace 和 Usage 的租户范围内存仓储。"""

from __future__ import annotations

from agent_platform.domain.common import DomainError
from agent_platform.domain.identity import IdentityContext
from agent_platform.domain.observability import TraceSpan
from agent_platform.domain.usage import ModelCallRecord, UsageAdjustment


class InMemoryTraceRepository:
    """Trace 记录按 platform/tenant/run 分区，禁止跨租户查询。"""

    def __init__(self) -> None:
        self._spans: dict[tuple[str, str, str], list[TraceSpan]] = {}

    def append(self, identity: IdentityContext, run_id: str, span: TraceSpan) -> None:
        key = (identity.platform_id, identity.tenant_id, run_id)
        self._spans.setdefault(key, []).append(span)

    def list(self, identity: IdentityContext, run_id: str) -> tuple[TraceSpan, ...]:
        return tuple(self._spans.get((identity.platform_id, identity.tenant_id, run_id), ()))


class InMemoryUsageLedger:
    """完成后的模型用量不可覆盖，只能用单独 adjustment 记录修正。"""

    def __init__(self) -> None:
        self._records: dict[tuple[str, str, str], ModelCallRecord] = {}
        self._adjustments: list[UsageAdjustment] = []

    def record(self, identity: IdentityContext, record: ModelCallRecord) -> None:
        if record.tenant_id != identity.tenant_id:
            raise DomainError("RESOURCE_NOT_FOUND", "用量不存在")
        key = (identity.platform_id, identity.tenant_id, record.call_id)
        if key in self._records:
            raise DomainError("CONFLICT", "已完成的用量记录不可覆盖")
        self._records[key] = record

    def add_adjustment(self, identity: IdentityContext, adjustment: UsageAdjustment) -> None:
        if not any(key[1] == identity.tenant_id and record.call_id == adjustment.original_call_id for key, record in self._records.items()):
            raise DomainError("RESOURCE_NOT_FOUND", "原始用量不存在")
        self._adjustments.append(adjustment)

    def adjustments(self, identity: IdentityContext) -> tuple[UsageAdjustment, ...]:
        return tuple(self._adjustments)

    def aggregate(
        self,
        identity: IdentityContext,
        *,
        run_id: str | None = None,
        user_id: str | None = None,
        employee_id: str | None = None,
        session_id: str | None = None,
        project_id: str | None = None,
        channel: str | None = None,
    ) -> dict[str, float | int]:
        records = [
            record for (platform_id, tenant_id, _), record in self._records.items()
            if platform_id == identity.platform_id
            and tenant_id == identity.tenant_id
            and (run_id is None or record.run_id == run_id)
            and (user_id is None or record.user_id == user_id)
            and (employee_id is None or record.employee_id == employee_id)
            and (session_id is None or record.session_id == session_id)
            and (project_id is None or record.project_id == project_id)
            and (channel is None or record.channel == channel)
        ]
        return {
            "calls": len(records),
            "input_tokens": sum(record.input_tokens for record in records),
            "output_tokens": sum(record.output_tokens for record in records),
            "total_tokens": sum(record.total_tokens for record in records),
            "cost_amount": sum(record.cost_amount for record in records),
        }
