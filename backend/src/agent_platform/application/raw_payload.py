"""按租户和保留策略保存可选的加密原始 payload 引用。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from uuid import uuid4

from agent_platform.domain.identity import IdentityContext
from agent_platform.domain.usage import RawPayloadRef


@dataclass(frozen=True, slots=True)
class RawPayloadPolicy:
    enabled: bool = False
    retention_days: int = 7
    encryption_required: bool = True

    def __post_init__(self) -> None:
        if self.retention_days <= 0:
            raise ValueError("retention_days must be positive")


class InMemoryRawPayloadStore:
    """Fake 加密对象存储，仅返回引用；普通 Trace 不保存正文。"""

    def __init__(self) -> None:
        self._objects: dict[tuple[str, str], bytes] = {}

    def save(
        self, identity: IdentityContext, payload: bytes, policy: RawPayloadPolicy
    ) -> RawPayloadRef | None:
        if not policy.enabled:
            return None
        object_ref = f"payload:{identity.platform_id}:{identity.tenant_id}:{uuid4().hex}"
        self._objects[(identity.tenant_id, object_ref)] = bytes(payload)
        return RawPayloadRef(
            object_ref=object_ref,
            sha256=hashlib.sha256(payload).hexdigest(),
            size_bytes=len(payload),
            retention_until=f"{policy.retention_days}d",
            encrypted=policy.encryption_required,
        )

    def read(self, identity: IdentityContext, object_ref: str) -> bytes:
        return self._objects[(identity.tenant_id, object_ref)]
