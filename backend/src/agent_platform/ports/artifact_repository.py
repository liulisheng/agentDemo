"""执行产出物和交付记录的租户持久化端口。"""

from __future__ import annotations

from typing import Protocol

from agent_platform.domain.artifacts import ArtifactDelivery, ExecutionArtifact
from agent_platform.domain.identity import IdentityContext


class ArtifactRepository(Protocol):
    """保存 Artifact 来源、可见性和交付幂等状态。"""

    def save_artifact(self, identity: IdentityContext, value: ExecutionArtifact) -> None: ...

    def get_artifact(self, identity: IdentityContext, artifact_id: str) -> ExecutionArtifact: ...

    def list_artifacts(self, identity: IdentityContext, orchestration_id: str) -> tuple[ExecutionArtifact, ...]: ...

    def save_delivery(self, identity: IdentityContext, value: ArtifactDelivery) -> None: ...

    def get_delivery_by_idempotency(
        self, identity: IdentityContext, artifact_id: str, channel_name: str, idempotency_key: str
    ) -> ArtifactDelivery | None: ...
