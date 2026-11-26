"""执行产出物和交付记录领域对象。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from agent_platform.domain.common import _require_text, generate_database_id


class ArtifactStatus(StrEnum):
    CREATED = "CREATED"
    UPLOADING = "UPLOADING"
    SCANNING = "SCANNING"
    READY = "READY"
    DELIVERING = "DELIVERING"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"


class ArtifactVisibility(StrEnum):
    INTERNAL = "INTERNAL"
    TEAM = "TEAM"
    USER = "USER"
    EXTERNAL = "EXTERNAL"


@dataclass(frozen=True, slots=True)
class ExecutionArtifact:
    """Runtime 产出物的租户索引和来源快照。"""

    artifact_id: str
    platform_id: str
    tenant_id: str
    conversation_id: str
    filename: str
    mime_type: str
    size_bytes: int
    content_sha256: str
    object_ref: str
    task_id: str | None = None
    run_id: str | None = None
    orchestration_id: str | None = None
    source_engine: str | None = None
    source_model: str | None = None
    visibility: ArtifactVisibility | str = ArtifactVisibility.TEAM
    status: ArtifactStatus | str = ArtifactStatus.CREATED
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    media_asset_id: str | None = None
    attempt_no: int | None = None

    def __post_init__(self) -> None:
        for name in ("artifact_id", "platform_id", "tenant_id", "conversation_id", "filename", "mime_type", "object_ref"):
            _require_text(getattr(self, name), name)
        if self.size_bytes < 0:
            raise ValueError("size_bytes must not be negative")
        if self.attempt_no is not None and self.attempt_no < 1:
            raise ValueError("attempt_no must be positive")
        digest = self.content_sha256.lower()
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError("content_sha256 must be a SHA-256 hex digest")
        object.__setattr__(self, "content_sha256", digest)
        object.__setattr__(self, "visibility", ArtifactVisibility(self.visibility))
        object.__setattr__(self, "status", ArtifactStatus(self.status))


@dataclass(frozen=True, slots=True)
class ArtifactDelivery:
    """产出物交付的租户幂等记录。"""

    delivery_id: str
    artifact_id: str
    platform_id: str
    tenant_id: str
    conversation_id: str
    channel_name: str
    idempotency_key: str
    status: str = "PENDING"
    retry_count: int = 0
    external_message_id: str | None = None

    @classmethod
    def new(
        cls,
        artifact_id: str,
        platform_id: str,
        tenant_id: str,
        conversation_id: str,
        channel_name: str,
        idempotency_key: str,
    ) -> ArtifactDelivery:
        return cls(generate_database_id(), artifact_id, platform_id, tenant_id, conversation_id, channel_name, idempotency_key)
