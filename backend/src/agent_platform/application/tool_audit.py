"""工具调用标准化审计仓储。"""

from __future__ import annotations

from agent_platform.domain.identity import IdentityContext
from agent_platform.domain.usage import ToolInvocationRecord


class InMemoryToolAuditRepository:
    def __init__(self) -> None:
        self._records: dict[tuple[str, str, str], list[ToolInvocationRecord]] = {}

    def record(self, identity: IdentityContext, record: ToolInvocationRecord) -> None:
        self._records.setdefault((identity.platform_id, identity.tenant_id, record.run_id), []).append(record)

    def list(self, identity: IdentityContext, run_id: str) -> tuple[ToolInvocationRecord, ...]:
        return tuple(self._records.get((identity.platform_id, identity.tenant_id, run_id), ()))
