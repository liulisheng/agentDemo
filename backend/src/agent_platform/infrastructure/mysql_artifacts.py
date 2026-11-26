"""V011 团队产物索引和交付幂等记录的 MySQL 仓储。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from agent_platform.domain.artifacts import ArtifactDelivery, ExecutionArtifact
from agent_platform.domain.common import DomainError
from agent_platform.domain.identity import IdentityContext
from agent_platform.ports.artifact_repository import ArtifactRepository


class MySqlArtifactRepository(ArtifactRepository):
    """将产物元数据/交付状态保存到 MySQL，正文只留在租户 ObjectStore。"""

    _ARTIFACT_COLUMNS = (
        "artifact_id,conversation_id,orchestration_id,task_id,run_id,attempt_no,media_asset_id,object_ref,filename,mime_type,size_bytes,content_sha256,source_engine,source_model,visibility,status,created_at"
    )

    def __init__(self, connection_factory) -> None:
        self._connection_factory = connection_factory

    @contextmanager
    def _transaction(self) -> Iterator[tuple[Any, Any]]:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            connection.start_transaction()
            yield connection, cursor
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    @contextmanager
    def _reader(self) -> Iterator[tuple[Any, Any]]:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            yield connection, cursor
        finally:
            cursor.close()
            connection.close()

    @staticmethod
    def _scope(identity: IdentityContext) -> tuple[str, str]:
        return identity.platform_id, identity.tenant_id

    @staticmethod
    def _not_found(resource: str) -> DomainError:
        return DomainError("RESOURCE_NOT_FOUND", "资源不存在", {"resource": resource})

    def save_artifact(self, identity: IdentityContext, value: ExecutionArtifact) -> None:
        if (value.platform_id, value.tenant_id) != self._scope(identity):
            raise DomainError("AUTHORIZATION_DENIED", "产出物不属于当前租户")
        with self._transaction() as (_, cursor):
            cursor.execute(
                "INSERT INTO execution_artifacts "
                "(platform_id,tenant_id,artifact_id,conversation_id,orchestration_id,task_id,run_id,attempt_no,media_asset_id,object_ref,filename,mime_type,size_bytes,content_sha256,source_engine,source_model,visibility,status,created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE orchestration_id=VALUES(orchestration_id),task_id=VALUES(task_id),run_id=VALUES(run_id),attempt_no=VALUES(attempt_no),media_asset_id=VALUES(media_asset_id),filename=VALUES(filename),mime_type=VALUES(mime_type),size_bytes=VALUES(size_bytes),content_sha256=VALUES(content_sha256),source_engine=VALUES(source_engine),source_model=VALUES(source_model),visibility=VALUES(visibility),status=VALUES(status)",
                (
                    identity.platform_id, identity.tenant_id, value.artifact_id, value.conversation_id,
                    value.orchestration_id, value.task_id, value.run_id, value.attempt_no,
                    value.media_asset_id, value.object_ref, value.filename, value.mime_type,
                    value.size_bytes, value.content_sha256, value.source_engine, value.source_model,
                    value.visibility.value, value.status.value, value.created_at.replace(tzinfo=None),
                ),
            )

    @staticmethod
    def _artifact(row: tuple[Any, ...], identity: IdentityContext) -> ExecutionArtifact:
        return ExecutionArtifact(
            artifact_id=str(row[0]), platform_id=identity.platform_id, tenant_id=identity.tenant_id,
            conversation_id=str(row[1]), orchestration_id=row[2], task_id=row[3], run_id=row[4],
            attempt_no=int(row[5]) if row[5] is not None else None, media_asset_id=row[6],
            object_ref=str(row[7]), filename=str(row[8]), mime_type=str(row[9]), size_bytes=int(row[10]),
            content_sha256=str(row[11]), source_engine=row[12], source_model=row[13],
            visibility=str(row[14]), status=str(row[15]), created_at=row[16],
        )

    def get_artifact(self, identity: IdentityContext, artifact_id: str) -> ExecutionArtifact:
        with self._reader() as (_, cursor):
            cursor.execute(
                f"SELECT {self._ARTIFACT_COLUMNS} FROM execution_artifacts WHERE platform_id=%s AND tenant_id=%s AND artifact_id=%s",
                (*self._scope(identity), artifact_id),
            )
            row = cursor.fetchone()
        if row is None:
            raise self._not_found("artifact")
        return self._artifact(row, identity)

    def list_artifacts(self, identity: IdentityContext, orchestration_id: str) -> tuple[ExecutionArtifact, ...]:
        with self._reader() as (_, cursor):
            cursor.execute(
                f"SELECT {self._ARTIFACT_COLUMNS} FROM execution_artifacts WHERE platform_id=%s AND tenant_id=%s AND orchestration_id=%s ORDER BY created_at,artifact_id",
                (*self._scope(identity), orchestration_id),
            )
            rows = cursor.fetchall()
        return tuple(self._artifact(row, identity) for row in rows)

    def save_delivery(self, identity: IdentityContext, value: ArtifactDelivery) -> None:
        if (value.platform_id, value.tenant_id) != self._scope(identity):
            raise DomainError("AUTHORIZATION_DENIED", "交付记录不属于当前租户")
        with self._transaction() as (_, cursor):
            cursor.execute(
                "INSERT INTO artifact_deliveries (platform_id,tenant_id,delivery_id,artifact_id,conversation_id,channel_name,idempotency_key,status,external_message_id,retry_count) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE status=VALUES(status),external_message_id=VALUES(external_message_id),retry_count=VALUES(retry_count)",
                (*self._scope(identity), value.delivery_id, value.artifact_id, value.conversation_id,
                 value.channel_name, value.idempotency_key, value.status, value.external_message_id,
                 value.retry_count),
            )

    def get_delivery_by_idempotency(
        self, identity: IdentityContext, artifact_id: str, channel_name: str, idempotency_key: str
    ) -> ArtifactDelivery | None:
        with self._reader() as (_, cursor):
            cursor.execute(
                "SELECT delivery_id,artifact_id,conversation_id,channel_name,idempotency_key,status,retry_count,external_message_id "
                "FROM artifact_deliveries WHERE platform_id=%s AND tenant_id=%s AND artifact_id=%s AND channel_name=%s AND idempotency_key=%s",
                (*self._scope(identity), artifact_id, channel_name, idempotency_key),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return ArtifactDelivery(str(row[0]), str(row[1]), identity.platform_id, identity.tenant_id,
                                str(row[2]), str(row[3]), str(row[4]), str(row[5]), int(row[6]), row[7])
