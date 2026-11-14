"""租户隔离的企业记忆读写实现；不解析 Runtime 私有记忆。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from agent_platform.domain.common import DomainError
from agent_platform.domain.identity import IdentityContext


class MemoryScope(StrEnum):
    CONVERSATION = "conversation"
    USER = "user"
    PROJECT = "project"
    TENANT = "tenant"
    AGENT = "agent"


@dataclass(frozen=True, slots=True)
class MemoryItem:
    item_id: str
    platform_id: str
    tenant_id: str
    scope: MemoryScope
    scope_id: str
    text: str
    source_run_id: str
    visibility: str = "tenant"
    retention_policy: str = "default"
    expires_at: datetime | None = None


class InMemoryMemoryStore:
    """显式 write 才生成长期 MemoryItem，查询始终校验租户和作用域。"""

    def __init__(self) -> None:
        self._items: dict[str, MemoryItem] = {}

    def write(
        self,
        identity: IdentityContext,
        scope: MemoryScope,
        scope_id: str,
        text: str,
        source_run_id: str,
    ) -> MemoryItem:
        if scope is MemoryScope.TENANT and scope_id != identity.tenant_id:
            raise DomainError("RESOURCE_NOT_FOUND", "记忆不存在")
        if scope is MemoryScope.USER and scope_id != identity.user_id:
            raise DomainError("RESOURCE_NOT_FOUND", "记忆不存在")
        item = MemoryItem(
            f"memory-{uuid4().hex}", identity.platform_id, identity.tenant_id, scope, scope_id, text, source_run_id
        )
        self._items[item.item_id] = item
        return item

    def read(self, identity: IdentityContext, scope: MemoryScope, scope_id: str) -> tuple[MemoryItem, ...]:
        if scope is MemoryScope.TENANT and scope_id != identity.tenant_id:
            return ()
        if scope is MemoryScope.USER and scope_id != identity.user_id:
            return ()
        now = datetime.now(UTC)
        return tuple(
            item for item in self._items.values()
            if item.platform_id == identity.platform_id
            and item.tenant_id == identity.tenant_id
            and item.scope is scope
            and item.scope_id == scope_id
            and (item.expires_at is None or item.expires_at > now)
        )
