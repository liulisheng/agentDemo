"""按租户和保留策略保存可选的加密原始 payload 引用。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from agent_platform.domain.common import DomainError
from agent_platform.domain.identity import IdentityContext
from agent_platform.domain.usage import RawPayloadRef
from agent_platform.ports.storage import ObjectStore, ObjectStoreError, UploadRequest


@dataclass(frozen=True, slots=True)
class RawPayloadPolicy:
    enabled: bool = False
    retention_days: int = 7
    encryption_required: bool = True

    def __post_init__(self) -> None:
        if self.retention_days <= 0:
            raise ValueError("retention_days must be positive")


class InMemoryRawPayloadStore:
    """仅供单元测试使用的内存 Fake，不提供静态加密能力。"""

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
            encrypted=False,
        )

    def read(self, identity: IdentityContext, object_ref: str) -> bytes:
        return self._objects[(identity.tenant_id, object_ref)]


class LocalRawPayloadStore:
    """将显式允许的原始 payload 存入 LocalObjectStore，MySQL 只登记引用和元数据。"""

    def __init__(self, object_store: ObjectStore) -> None:
        self.object_store = object_store

    def save(
        self, identity: IdentityContext, payload: bytes, policy: RawPayloadPolicy
    ) -> RawPayloadRef | None:
        if not policy.enabled:
            return None
        if policy.encryption_required and not getattr(self.object_store, "encryption_enabled", False):
            raise ObjectStoreError("raw payload requires an encrypted object store")
        digest = hashlib.sha256(payload).hexdigest()
        intent = self.object_store.create_upload_intent(
            UploadRequest(
                platform_id=identity.platform_id,
                tenant_id=identity.tenant_id,
                filename="raw-payload.bin",
                mime_type="application/octet-stream",
                size_bytes=len(payload),
                retention_policy="ephemeral",
                expected_sha256=digest,
                retention_expires_at=datetime.now(UTC) + timedelta(days=policy.retention_days),
            )
        )
        asset = self.object_store.complete_upload(intent.upload_id, payload)
        return RawPayloadRef(
            object_ref=asset.object_ref,
            sha256=asset.sha256,
            size_bytes=asset.size_bytes,
            retention_until=asset.retention_expires_at.isoformat()
            if asset.retention_expires_at is not None
            else None,
            encrypted=asset.encryption_key_version is not None,
        )

    def read(self, identity: IdentityContext, object_ref: str) -> bytes:
        try:
            return self.object_store.read(object_ref, identity.platform_id, identity.tenant_id)
        except PermissionError as error:
            raise DomainError("RESOURCE_NOT_FOUND", "原始 payload 不存在") from error

    def delete(self, identity: IdentityContext, object_ref: str) -> None:
        try:
            self.object_store.delete(object_ref, identity.platform_id, identity.tenant_id)
        except PermissionError as error:
            raise DomainError("RESOURCE_NOT_FOUND", "原始 payload 不存在") from error
