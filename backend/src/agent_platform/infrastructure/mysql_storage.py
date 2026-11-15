"""MySQL MediaAsset 元数据、上传 lease 和可重试回收队列。"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from agent_platform.domain.common import DomainError, generate_database_id
from agent_platform.ports.storage import (
    MediaAsset,
    MediaAssetStatus,
    ObjectStoreError,
    RetentionPolicy,
    UploadRequest,
)


class MySqlMediaAssetRepository:
    """所有对象元数据查询均用 platform_id + tenant_id 限制授权范围。"""

    def __init__(
        self,
        connection_factory: Callable[[], Any],
        *,
        failure_injector: Callable[[str], None] | None = None,
    ) -> None:
        self._connection_factory = connection_factory
        self._failure_injector = failure_injector

    def _inject_failure(self, stage: str) -> None:
        """只允许测试显式注入提交故障，正式运行不从环境变量开启。"""

        if self._failure_injector is not None:
            self._failure_injector(stage)

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
    def _not_found() -> DomainError:
        return DomainError("RESOURCE_NOT_FOUND", "对象不存在")

    @staticmethod
    def _enqueue_gc(cursor: Any, platform_id: str, tenant_id: str, object_ref: str) -> None:
        cursor.execute(
            "INSERT INTO content_gc_queue "
            "(platform_id, tenant_id, cleanup_id, object_ref, object_type, status, next_attempt_at) "
            "VALUES (%s,%s,%s,%s,'MEDIA_ASSET','QUEUED',CURRENT_TIMESTAMP(6)) "
            "ON DUPLICATE KEY UPDATE status='QUEUED', next_attempt_at=CURRENT_TIMESTAMP(6), last_error_code=NULL",
            (platform_id, tenant_id, generate_database_id(), object_ref),
        )

    @staticmethod
    def _load_asset(row: tuple[Any, ...]) -> MediaAsset:
        return MediaAsset(
            asset_id=str(row[0]),
            object_ref=str(row[1]),
            platform_id=str(row[2]),
            tenant_id=str(row[3]),
            filename=str(row[4]),
            mime_type=str(row[5]),
            size_bytes=int(row[6]),
            sha256=str(row[7]),
            retention_policy=RetentionPolicy(str(row[8])),
            status=MediaAssetStatus(str(row[9])),
            encryption_key_version=str(row[10]) if row[10] is not None else None,
            retention_expires_at=row[11].replace(tzinfo=UTC) if row[11] is not None else None,
        )

    @staticmethod
    def _db_datetime(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value
        return value.astimezone(UTC).replace(tzinfo=None)

    def create_upload_intent(
        self,
        upload_id: str,
        object_ref: str,
        request: UploadRequest,
        *,
        encryption_key_version: str | None,
    ) -> None:
        with self._transaction() as (_, cursor):
            cursor.execute(
                "INSERT INTO content_upload_intents "
                "(platform_id, tenant_id, upload_id, object_ref, object_type, status, lease_expires_at, "
                "original_filename, mime_type, retention_expires_at, expected_size_bytes, expected_sha256, retention_policy, encryption_key_version) "
                "VALUES (%s,%s,%s,%s,'MEDIA_ASSET','UPLOADING',TIMESTAMPADD(SECOND,600,CURRENT_TIMESTAMP(6)),%s,%s,%s,%s,%s,%s,%s)",
                (
                    request.platform_id,
                    request.tenant_id,
                    upload_id,
                    object_ref,
                    request.filename,
                    request.mime_type,
                    self._db_datetime(request.retention_expires_at),
                    request.size_bytes,
                    request.expected_sha256,
                    request.retention_policy,
                    encryption_key_version,
                ),
            )

    def get_upload_intent(self, upload_id: str) -> tuple[UploadRequest, str, str | None] | None:
        """按随机 upload ID 恢复 lease；调用方不得将该方法暴露为用户可枚举 API。"""

        with self._reader() as (_, cursor):
            cursor.execute(
                "SELECT platform_id, tenant_id, object_ref, original_filename, mime_type, expected_size_bytes, "
                "expected_sha256, retention_policy, encryption_key_version, status, lease_expires_at, retention_expires_at "
                "FROM content_upload_intents WHERE upload_id=%s",
                (upload_id,),
            )
            row = cursor.fetchone()
            if row is None or str(row[9]) != "UPLOADING":
                return None
            cursor.execute("SELECT %s > CURRENT_TIMESTAMP(6)", (row[10],))
            if not bool(cursor.fetchone()[0]):
                return None
        request = UploadRequest(
            platform_id=str(row[0]),
            tenant_id=str(row[1]),
            filename=str(row[3]),
            mime_type=str(row[4]),
            size_bytes=int(row[5]) if row[5] is not None else 0,
            retention_policy=str(row[7]),
            expected_sha256=str(row[6]) if row[6] is not None else None,
            retention_expires_at=row[11].replace(tzinfo=UTC) if row[11] is not None else None,
        )
        return request, str(row[2]), str(row[8]) if row[8] is not None else None

    def publish_upload(self, upload_id: str, asset: MediaAsset) -> None:
        """文件原子发布后，仅在有效 upload lease 内登记 MediaAsset 并封存 intent。"""

        expired = False
        with self._transaction() as (_, cursor):
            cursor.execute(
                "SELECT platform_id, tenant_id, object_ref, expected_size_bytes, expected_sha256, "
                "retention_policy, encryption_key_version, status, lease_expires_at, original_filename, mime_type, "
                "retention_expires_at "
                "FROM content_upload_intents WHERE upload_id=%s FOR UPDATE",
                (upload_id,),
            )
            row = cursor.fetchone()
            if row is None:
                raise ObjectStoreError("upload intent not found or already completed")
            if str(row[7]) == "EXPIRED":
                self._enqueue_gc(cursor, str(row[0]), str(row[1]), str(row[2]))
                expired = True
            elif str(row[7]) != "UPLOADING":
                raise ObjectStoreError("upload intent not found or already completed")
            else:
                cursor.execute("SELECT %s > CURRENT_TIMESTAMP(6)", (row[8],))
                if not bool(cursor.fetchone()[0]):
                    cursor.execute(
                        "UPDATE content_upload_intents SET status='EXPIRED' WHERE upload_id=%s",
                        (upload_id,),
                    )
                    self._enqueue_gc(cursor, str(row[0]), str(row[1]), str(row[2]))
                    expired = True
                else:
                    if (
                        str(row[0]) != asset.platform_id
                        or str(row[1]) != asset.tenant_id
                        or str(row[2]) != asset.object_ref
                        or int(row[3]) != asset.size_bytes
                        or (row[4] is not None and str(row[4]) != asset.sha256)
                        or str(row[5]) != str(asset.retention_policy)
                        or (str(row[6]) if row[6] is not None else None) != asset.encryption_key_version
                        or str(row[9]) != asset.filename
                        or str(row[10]) != asset.mime_type
                        or self._db_datetime(row[11]) != self._db_datetime(asset.retention_expires_at)
                    ):
                        raise ObjectStoreError("uploaded object metadata does not match intent")
                    cursor.execute(
                        "INSERT INTO media_assets "
                        "(platform_id, tenant_id, asset_id, object_ref, filename, mime_type, size_bytes, content_sha256, "
                        "retention_policy, retention_expires_at, status, encryption_key_version) "
                        "SELECT platform_id, tenant_id, %s, object_ref, original_filename, mime_type, expected_size_bytes, %s, "
                        "retention_policy, retention_expires_at, 'UPLOADED', encryption_key_version FROM content_upload_intents "
                        "WHERE upload_id=%s AND status='UPLOADING'",
                        (asset.asset_id, asset.sha256, upload_id),
                    )
                    if cursor.rowcount != 1:
                        raise ObjectStoreError("upload intent lost before metadata commit")
                    self._inject_failure("after_asset_insert")
                    cursor.execute(
                        "UPDATE content_upload_intents SET status='PUBLISHED' WHERE upload_id=%s AND status='UPLOADING'",
                        (upload_id,),
                    )
                    if cursor.rowcount != 1:
                        raise ObjectStoreError("upload intent lost before metadata commit")
                    self._inject_failure("after_intent_update")
        if expired:
            raise ObjectStoreError("upload intent lease expired")

    def get_asset(self, object_ref: str, platform_id: str, tenant_id: str) -> MediaAsset | None:
        with self._reader() as (_, cursor):
            cursor.execute(
                "SELECT asset_id, object_ref, platform_id, tenant_id, filename, mime_type, size_bytes, "
                "content_sha256, retention_policy, status, encryption_key_version, retention_expires_at FROM media_assets "
                "WHERE platform_id=%s AND tenant_id=%s AND object_ref=%s",
                (platform_id, tenant_id, object_ref),
            )
            row = cursor.fetchone()
        return self._load_asset(row) if row is not None else None

    def mark_deleted_and_enqueue(self, object_ref: str, platform_id: str, tenant_id: str) -> None:
        with self._transaction() as (_, cursor):
            cursor.execute(
                "SELECT asset_id FROM media_assets WHERE platform_id=%s AND tenant_id=%s AND object_ref=%s FOR UPDATE",
                (platform_id, tenant_id, object_ref),
            )
            if cursor.fetchone() is None:
                raise self._not_found()
            cursor.execute(
                "UPDATE media_assets SET status='DELETED', deleted_at=CURRENT_TIMESTAMP(6) "
                "WHERE platform_id=%s AND tenant_id=%s AND object_ref=%s",
                (platform_id, tenant_id, object_ref),
            )
            self._enqueue_gc(cursor, platform_id, tenant_id, object_ref)

    def claim_gc_jobs(self, *, limit: int = 100) -> tuple[tuple[str, str, str], ...]:
        if limit < 1 or limit > 500:
            raise ValueError("GC 批大小必须处于 1 到 500 之间")
        with self._transaction() as (_, cursor):
            cursor.execute(
                "SELECT platform_id, tenant_id, object_ref FROM content_upload_intents "
                "WHERE status='UPLOADING' AND lease_expires_at<=CURRENT_TIMESTAMP(6) "
                "ORDER BY lease_expires_at, upload_id LIMIT %s FOR UPDATE SKIP LOCKED",
                (limit,),
            )
            expired_intents = cursor.fetchall()
            for platform_id, tenant_id, object_ref in expired_intents:
                cursor.execute(
                    "UPDATE content_upload_intents SET status='EXPIRED' WHERE platform_id=%s AND tenant_id=%s "
                    "AND object_ref=%s AND status='UPLOADING'",
                    (platform_id, tenant_id, object_ref),
                )
                self._enqueue_gc(cursor, str(platform_id), str(tenant_id), str(object_ref))
            cursor.execute(
                "SELECT platform_id, tenant_id, object_ref FROM media_assets "
                "WHERE status<>'DELETED' AND retention_expires_at IS NOT NULL "
                "AND retention_expires_at<=CURRENT_TIMESTAMP(6) "
                "OR (status='UPLOADED' AND created_at<=DATE_SUB(CURRENT_TIMESTAMP(6), INTERVAL 1 DAY)) "
                "ORDER BY COALESCE(retention_expires_at, created_at), asset_id LIMIT %s FOR UPDATE SKIP LOCKED",
                (limit,),
            )
            expired_assets = cursor.fetchall()
            for platform_scope, tenant_scope, expired_ref in expired_assets:
                if self._has_live_reference(cursor, str(platform_scope), str(tenant_scope), str(expired_ref)):
                    continue
                cursor.execute(
                    "UPDATE media_assets SET status='DELETED', deleted_at=CURRENT_TIMESTAMP(6) "
                    "WHERE platform_id=%s AND tenant_id=%s AND object_ref=%s AND status<>'DELETED'",
                    (platform_scope, tenant_scope, expired_ref),
                )
                self._enqueue_gc(cursor, str(platform_scope), str(tenant_scope), str(expired_ref))
            cursor.execute(
                "SELECT platform_id, tenant_id, object_ref FROM content_gc_queue "
                "WHERE (status IN ('QUEUED','RETRY') AND next_attempt_at<=CURRENT_TIMESTAMP(6)) "
                "OR (status='DELETING' AND updated_at<=DATE_SUB(CURRENT_TIMESTAMP(6), INTERVAL 60 SECOND)) "
                "ORDER BY next_attempt_at, created_at, cleanup_id LIMIT %s FOR UPDATE SKIP LOCKED",
                (limit,),
            )
            rows = cursor.fetchall()
            for platform_id, tenant_id, object_ref in rows:
                cursor.execute(
                    "UPDATE content_gc_queue SET status='DELETING', attempt_count=attempt_count+1 "
                    "WHERE platform_id=%s AND tenant_id=%s AND object_ref=%s "
                    "AND (status IN ('QUEUED','RETRY') OR status='DELETING')",
                    (platform_id, tenant_id, object_ref),
                )
        return tuple((str(row[0]), str(row[1]), str(row[2])) for row in rows)

    def is_object_protected(self, platform_id: str, tenant_id: str, object_ref: str) -> bool:
        with self._reader() as (_, cursor):
            return self._has_live_reference(cursor, platform_id, tenant_id, object_ref)

    @staticmethod
    def _has_live_reference(cursor: Any, platform_id: str, tenant_id: str, object_ref: str) -> bool:
        """共享的保护谓词供 GC 领取事务和状态查询复用。"""

        checks = (
            ("messages", "content_ref"),
            ("message_chunks", "content_ref"),
            ("runs", "result_ref"),
            ("runs", "runtime_result_ref"),
            ("sessions", "harness_home_ref"),
            ("sessions", "workspace_ref"),
            ("sessions", "runtime_thread_ref"),
            ("sessions", "runtime_workspace_ref"),
            ("sessions", "runtime_result_ref"),
        )
        for table, column in checks:
            cursor.execute(
                f"SELECT 1 FROM {table} WHERE platform_id=%s AND tenant_id=%s AND {column}=%s LIMIT 1",
                (platform_id, tenant_id, object_ref),
            )
            if cursor.fetchone() is not None:
                return True
        cursor.execute(
            "SELECT 1 FROM content_upload_intents WHERE platform_id=%s AND tenant_id=%s AND object_ref=%s "
            "AND status='UPLOADING' AND lease_expires_at>CURRENT_TIMESTAMP(6) LIMIT 1",
            (platform_id, tenant_id, object_ref),
        )
        return cursor.fetchone() is not None

    def finish_gc(self, platform_id: str, tenant_id: str, object_ref: str) -> None:
        with self._transaction() as (_, cursor):
            cursor.execute(
                "UPDATE content_gc_queue SET status='DELETED', last_error_code=NULL "
                "WHERE platform_id=%s AND tenant_id=%s AND object_ref=%s AND status='DELETING'",
                (platform_id, tenant_id, object_ref),
            )

    def retry_gc(self, platform_id: str, tenant_id: str, object_ref: str, error_code: str) -> None:
        with self._transaction() as (_, cursor):
            cursor.execute(
                "UPDATE content_gc_queue SET status='RETRY', last_error_code=%s, "
                "next_attempt_at=DATE_ADD(CURRENT_TIMESTAMP(6), INTERVAL LEAST(attempt_count,60) SECOND) "
                "WHERE platform_id=%s AND tenant_id=%s AND object_ref=%s AND status='DELETING'",
                (error_code[:128], platform_id, tenant_id, object_ref),
            )
