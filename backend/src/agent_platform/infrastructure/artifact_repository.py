"""执行产出物和交付记录的内存租户仓储。"""

from __future__ import annotations

from agent_platform.domain.artifacts import ArtifactDelivery, ExecutionArtifact
from agent_platform.domain.common import DomainError
from agent_platform.domain.identity import IdentityContext


class InMemoryArtifactRepository:
    """Fake 模式中使用的 Artifact/Delivery 仓储，不保存文件正文。"""

    def __init__(self) -> None:
        self._artifacts: dict[tuple[str, str, str], ExecutionArtifact] = {}
        self._deliveries: dict[tuple[str, str, str, str], ArtifactDelivery] = {}

    @staticmethod
    def _scope(identity: IdentityContext, resource_id: str) -> tuple[str, str, str]:
        return identity.platform_id, identity.tenant_id, resource_id

    def save_artifact(self, identity: IdentityContext, value: ExecutionArtifact) -> None:
        if (value.platform_id, value.tenant_id) != (identity.platform_id, identity.tenant_id):
            raise DomainError("AUTHORIZATION_DENIED", "产出物不属于当前租户")
        self._artifacts[self._scope(identity, value.artifact_id)] = value

    def get_artifact(self, identity: IdentityContext, artifact_id: str) -> ExecutionArtifact:
        try:
            return self._artifacts[self._scope(identity, artifact_id)]
        except KeyError as error:
            raise DomainError("RESOURCE_NOT_FOUND", "产出物不存在", {"resource": "artifact"}) from error

    def list_artifacts(self, identity: IdentityContext, orchestration_id: str) -> tuple[ExecutionArtifact, ...]:
        return tuple(
            value
            for (platform_id, tenant_id, _), value in self._artifacts.items()
            if platform_id == identity.platform_id
            and tenant_id == identity.tenant_id
            and value.orchestration_id == orchestration_id
        )

    def save_delivery(self, identity: IdentityContext, value: ArtifactDelivery) -> None:
        if (value.platform_id, value.tenant_id) != (identity.platform_id, identity.tenant_id):
            raise DomainError("AUTHORIZATION_DENIED", "交付不属于当前租户")
        self.get_artifact(identity, value.artifact_id)
        self._deliveries[(identity.platform_id, identity.tenant_id, value.artifact_id, value.idempotency_key)] = value

    def get_delivery_by_idempotency(
        self, identity: IdentityContext, artifact_id: str, channel_name: str, idempotency_key: str
    ) -> ArtifactDelivery | None:
        value = self._deliveries.get((identity.platform_id, identity.tenant_id, artifact_id, idempotency_key))
        if value is None or value.channel_name != channel_name:
            return None
        return value
