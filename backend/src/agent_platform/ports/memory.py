"""企业记忆读写端口。"""

from typing import Protocol

from agent_platform.application.memory import MemoryItem, MemoryScope
from agent_platform.domain.identity import IdentityContext


class MemoryReader(Protocol):
    def read(self, identity: IdentityContext, scope: MemoryScope, scope_id: str) -> tuple[MemoryItem, ...]:
        ...


class MemoryWriter(Protocol):
    def write(self, identity: IdentityContext, scope: MemoryScope, scope_id: str, text: str, source_run_id: str) -> MemoryItem:
        ...
