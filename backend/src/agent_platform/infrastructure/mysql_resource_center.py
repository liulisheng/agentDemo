"""MySQL 平台级资源中心目录、Package 版本和租户 Grant 仓储。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from agent_platform.domain.common import DomainError
from agent_platform.domain.identity import IdentityContext
from agent_platform.domain.resource_center import (
    HubGrant,
    HubGrantStatus,
    HubImportMode,
    HubPackageManifest,
    HubResource,
    HubResourceDependency,
    HubResourceDetail,
    HubResourceStatus,
    HubResourceSummary,
    HubResourceType,
    HubResourceVersion,
    HubVersionStatus,
    HubVisibility,
    TenantImportStatus,
    TenantResourceImport,
)
from agent_platform.ports.resource_center import ResourceCenterRepository


class MySqlResourceCenterRepository(ResourceCenterRepository):
    """目录查询只返回脱敏元数据，Package 内容必须通过精确版本 Grant 授权。"""

    def __init__(self, connection_factory: Callable[[], Any]) -> None:
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
    def _not_found() -> DomainError:
        return DomainError("RESOURCE_NOT_FOUND", "资源不存在")

    @staticmethod
    def _db_datetime(value: datetime | None) -> datetime | None:
        if value is None or value.tzinfo is None:
            return value
        return value.astimezone(UTC).replace(tzinfo=None)

    @staticmethod
    def _json_object(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        if isinstance(value, str):
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        raise TypeError("Hub manifest JSON must be an object")

    @classmethod
    def _version_from_row(cls, row: tuple[Any, ...]) -> HubResourceVersion:
        manifest_json = (
            row[7].decode("utf-8") if isinstance(row[7], bytes) else
            row[7] if isinstance(row[7], str) else
            json.dumps(row[7], ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        )
        return HubResourceVersion(
            platform_id=str(row[0]),
            hub_resource_id=str(row[1]),
            version=int(row[2]),
            publisher_tenant_id=str(row[3]),
            source_resource_type=HubResourceType(str(row[4])),
            source_resource_id=str(row[5]),
            source_resource_version=int(row[6]),
            manifest=HubPackageManifest(
                data=cls._json_object(row[7]),
                manifest_json=manifest_json,
                sha256=str(row[8]),
            ),
            artifact_asset_id=str(row[9]) if row[9] is not None else None,
            created_by=str(row[11]),
            status=HubVersionStatus(str(row[10])),
            published_at=row[12].replace(tzinfo=UTC) if row[12] is not None else None,
        )

    @staticmethod
    def _version_select() -> str:
        return (
            "SELECT platform_id, hub_resource_id, version, publisher_tenant_id, source_resource_type, "
            "source_resource_id, source_resource_version, manifest_json, manifest_sha256, artifact_asset_id, "
            "status, created_by, published_at FROM hub_resource_versions"
        )

    def save_resource(self, identity: IdentityContext, value: HubResource) -> None:
        """仅允许当前租户为自己创建 Hub 草稿，目录 ID 处于平台范围。"""

        if value.platform_id != identity.platform_id or value.publisher_tenant_id != identity.tenant_id:
            raise self._not_found()
        if value.publisher_user_id != identity.user_id:
            raise DomainError("AUTHORIZATION_DENIED", "发布者必须是当前认证用户")
        with self._transaction() as (_, cursor):
            cursor.execute(
                "INSERT INTO hub_resources "
                "(platform_id, hub_resource_id, resource_type, publisher_tenant_id, publisher_user_id, "
                "name, description, visibility, status) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    value.platform_id,
                    value.hub_resource_id,
                    value.resource_type.value,
                    value.publisher_tenant_id,
                    value.publisher_user_id,
                    value.name,
                    value.description,
                    value.visibility.value,
                    value.status.value,
                ),
            )

    def save_version(self, identity: IdentityContext, value: HubResourceVersion) -> None:
        """插入不可变的资源版本草稿；manifest 必须与其 SHA-256 一致。"""

        if value.platform_id != identity.platform_id or value.publisher_tenant_id != identity.tenant_id:
            raise self._not_found()
        if value.created_by != identity.user_id:
            raise DomainError("AUTHORIZATION_DENIED", "Package 发布者必须是当前认证用户")
        if json.loads(value.manifest.manifest_json) != value.manifest.data:
            raise ValueError("manifest_json 与规范化 manifest data 不一致")
        calculated = hashlib.sha256(value.manifest.manifest_json.encode("utf-8")).hexdigest()
        if calculated != value.manifest.sha256:
            raise ValueError("manifest SHA-256 与内容不一致")
        with self._transaction() as (_, cursor):
            cursor.execute(
                "INSERT INTO hub_resource_versions "
                "(platform_id, hub_resource_id, version, publisher_tenant_id, source_resource_type, "
                "source_resource_id, source_resource_version, manifest_json, manifest_sha256, "
                "artifact_asset_id, status, created_by, published_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    value.platform_id,
                    value.hub_resource_id,
                    value.version,
                    value.publisher_tenant_id,
                    value.source_resource_type.value,
                    value.source_resource_id,
                    value.source_resource_version,
                    value.manifest.manifest_json,
                    value.manifest.sha256,
                    value.artifact_asset_id,
                    value.status.value,
                    value.created_by,
                    self._db_datetime(value.published_at),
                ),
            )

    def create_package(
        self,
        identity: IdentityContext,
        resource: HubResource,
        version: HubResourceVersion,
        dependencies: tuple[HubResourceDependency, ...],
    ) -> None:
        """把目录条目、Package 版本和依赖清单作为一个事务创建。"""

        if (
            resource.platform_id != identity.platform_id
            or resource.publisher_tenant_id != identity.tenant_id
            or resource.publisher_user_id != identity.user_id
            or version.platform_id != identity.platform_id
            or version.publisher_tenant_id != identity.tenant_id
            or version.created_by != identity.user_id
            or version.hub_resource_id != resource.hub_resource_id
            or version.version < 1
        ):
            raise self._not_found()
        calculated = hashlib.sha256(version.manifest.manifest_json.encode("utf-8")).hexdigest()
        if calculated != version.manifest.sha256:
            raise ValueError("manifest SHA-256 与内容不一致")
        if json.loads(version.manifest.manifest_json) != version.manifest.data:
            raise ValueError("manifest_json 与规范化 manifest data 不一致")
        if len({dependency.sequence_no for dependency in dependencies}) != len(dependencies):
            raise ValueError("Hub 依赖顺序号必须唯一")
        with self._transaction() as (_, cursor):
            cursor.execute(
                "INSERT INTO hub_resources "
                "(platform_id, hub_resource_id, resource_type, publisher_tenant_id, publisher_user_id, "
                "name, description, visibility, status) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    resource.platform_id,
                    resource.hub_resource_id,
                    resource.resource_type.value,
                    resource.publisher_tenant_id,
                    resource.publisher_user_id,
                    resource.name,
                    resource.description,
                    resource.visibility.value,
                    resource.status.value,
                ),
            )
            cursor.execute(
                "INSERT INTO hub_resource_versions "
                "(platform_id, hub_resource_id, version, publisher_tenant_id, source_resource_type, "
                "source_resource_id, source_resource_version, manifest_json, manifest_sha256, "
                "artifact_asset_id, status, created_by, published_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    version.platform_id,
                    version.hub_resource_id,
                    version.version,
                    version.publisher_tenant_id,
                    version.source_resource_type.value,
                    version.source_resource_id,
                    version.source_resource_version,
                    version.manifest.manifest_json,
                    version.manifest.sha256,
                    version.artifact_asset_id,
                    version.status.value,
                    version.created_by,
                    self._db_datetime(version.published_at),
                ),
            )
            for dependency in sorted(dependencies, key=lambda item: item.sequence_no):
                cursor.execute(
                    "INSERT INTO hub_resource_dependencies "
                    "(platform_id, hub_resource_id, hub_version, dependency_id, dependency_type, "
                    "dependency_hub_resource_id, dependency_hub_version, required, sequence_no) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        identity.platform_id,
                        resource.hub_resource_id,
                        version.version,
                        dependency.dependency_id,
                        dependency.dependency_type.value,
                        dependency.dependency_hub_resource_id,
                        dependency.dependency_hub_version,
                        dependency.required,
                        dependency.sequence_no,
                    ),
                )

    def get_owned_resource(self, identity: IdentityContext, hub_resource_id: str) -> HubResource:
        """只允许发布租户读取 Hub 条目的内部来源 ID 和状态。"""

        with self._reader() as (_, cursor):
            cursor.execute(
                "SELECT platform_id, hub_resource_id, resource_type, publisher_tenant_id, publisher_user_id, "
                "name, description, visibility, status FROM hub_resources "
                "WHERE platform_id=%s AND publisher_tenant_id=%s AND hub_resource_id=%s",
                (identity.platform_id, identity.tenant_id, hub_resource_id),
            )
            row = cursor.fetchone()
        if row is None:
            raise self._not_found()
        return HubResource(
            platform_id=str(row[0]),
            hub_resource_id=str(row[1]),
            resource_type=HubResourceType(str(row[2])),
            publisher_tenant_id=str(row[3]),
            publisher_user_id=str(row[4]),
            name=str(row[5]),
            description=str(row[6]),
            visibility=HubVisibility(str(row[7])),
            status=HubResourceStatus(str(row[8])),
        )

    def next_version(self, identity: IdentityContext, hub_resource_id: str) -> int:
        """读取当前资源下一个顺序版本；写入时仍在行锁内复核避免并发覆盖。"""

        with self._reader() as (_, cursor):
            cursor.execute(
                "SELECT COALESCE(MAX(v.version),0)+1 FROM hub_resource_versions v "
                "JOIN hub_resources r ON r.platform_id=v.platform_id AND r.hub_resource_id=v.hub_resource_id "
                "WHERE v.platform_id=%s AND r.publisher_tenant_id=%s AND v.hub_resource_id=%s",
                (identity.platform_id, identity.tenant_id, hub_resource_id),
            )
            row = cursor.fetchone()
        return int(row[0]) if row is not None else 1

    def create_version(
        self,
        identity: IdentityContext,
        version: HubResourceVersion,
        dependencies: tuple[HubResourceDependency, ...],
    ) -> None:
        """为已有 Hub 条目追加下一个不可变版本及其依赖快照。"""

        if (
            version.platform_id != identity.platform_id
            or version.publisher_tenant_id != identity.tenant_id
            or version.created_by != identity.user_id
        ):
            raise self._not_found()
        if hashlib.sha256(version.manifest.manifest_json.encode("utf-8")).hexdigest() != version.manifest.sha256:
            raise ValueError("manifest SHA-256 与内容不一致")
        if json.loads(version.manifest.manifest_json) != version.manifest.data:
            raise ValueError("manifest_json 与规范化 manifest data 不一致")
        if len({dependency.sequence_no for dependency in dependencies}) != len(dependencies):
            raise ValueError("Hub 依赖顺序号必须唯一")
        with self._transaction() as (_, cursor):
            cursor.execute(
                "SELECT resource_type, status FROM hub_resources WHERE platform_id=%s "
                "AND publisher_tenant_id=%s AND hub_resource_id=%s FOR UPDATE",
                (identity.platform_id, identity.tenant_id, version.hub_resource_id),
            )
            resource_row = cursor.fetchone()
            if resource_row is None or str(resource_row[0]) != version.source_resource_type.value:
                raise self._not_found()
            if str(resource_row[1]) not in {"DRAFT", "PUBLISHED"}:
                raise DomainError("INVALID_STATE", "已归档或撤销的 Hub 资源不能追加版本")
            cursor.execute(
                "SELECT COALESCE(MAX(version),0)+1 FROM hub_resource_versions "
                "WHERE platform_id=%s AND hub_resource_id=%s",
                (identity.platform_id, version.hub_resource_id),
            )
            expected_version = int(cursor.fetchone()[0])
            if version.version != expected_version:
                raise DomainError("INVALID_STATE", "Hub 资源版本已变化，请刷新后重试")
            cursor.execute(
                "INSERT INTO hub_resource_versions "
                "(platform_id, hub_resource_id, version, publisher_tenant_id, source_resource_type, "
                "source_resource_id, source_resource_version, manifest_json, manifest_sha256, artifact_asset_id, "
                "status, created_by, published_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    version.platform_id,
                    version.hub_resource_id,
                    version.version,
                    version.publisher_tenant_id,
                    version.source_resource_type.value,
                    version.source_resource_id,
                    version.source_resource_version,
                    version.manifest.manifest_json,
                    version.manifest.sha256,
                    version.artifact_asset_id,
                    version.status.value,
                    version.created_by,
                    self._db_datetime(version.published_at),
                ),
            )
            for dependency in sorted(dependencies, key=lambda item: item.sequence_no):
                cursor.execute(
                    "INSERT INTO hub_resource_dependencies "
                    "(platform_id, hub_resource_id, hub_version, dependency_id, dependency_type, "
                    "dependency_hub_resource_id, dependency_hub_version, required, sequence_no) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        identity.platform_id,
                        version.hub_resource_id,
                        version.version,
                        dependency.dependency_id,
                        dependency.dependency_type.value,
                        dependency.dependency_hub_resource_id,
                        dependency.dependency_hub_version,
                        dependency.required,
                        dependency.sequence_no,
                    ),
                )

    def find_published_source_version(
        self,
        identity: IdentityContext,
        resource_type: HubResourceType,
        source_resource_id: str,
        source_resource_version: int,
    ) -> HubResourceVersion:
        """查找同租户已发布的源版本映射；不猜测最新本地版本。"""

        with self._reader() as (_, cursor):
            cursor.execute(
                "SELECT v.platform_id, v.hub_resource_id, v.version, v.publisher_tenant_id, "
                "v.source_resource_type, v.source_resource_id, v.source_resource_version, v.manifest_json, "
                "v.manifest_sha256, v.artifact_asset_id, v.status, v.created_by, v.published_at "
                "FROM hub_resource_versions v JOIN hub_resources r ON r.platform_id=v.platform_id "
                "AND r.hub_resource_id=v.hub_resource_id "
                "WHERE v.platform_id=%s AND v.publisher_tenant_id=%s AND v.source_resource_type=%s "
                "AND v.source_resource_id=%s AND v.source_resource_version=%s "
                "AND v.status='PUBLISHED' AND r.status='PUBLISHED' "
                "ORDER BY v.published_at DESC, v.hub_resource_id LIMIT 2",
                (
                    identity.platform_id,
                    identity.tenant_id,
                    HubResourceType(resource_type).value,
                    source_resource_id,
                    source_resource_version,
                ),
            )
            rows = cursor.fetchall()
        if not rows:
            raise DomainError(
                "DEPENDENCY_VALIDATION_ERROR",
                "资源中心缺少依赖项的已发布 Hub 版本",
                {"resource_type": HubResourceType(resource_type).value, "source_version": source_resource_version},
            )
        if len(rows) > 1:
            raise DomainError(
                "DEPENDENCY_VALIDATION_ERROR",
                "依赖源版本对应多个 Hub 发布包，请先明确共享资源映射",
                {"resource_type": HubResourceType(resource_type).value, "source_version": source_resource_version},
            )
        return self._version_from_row(rows[0])

    def publish_version(
        self, identity: IdentityContext, hub_resource_id: str, version: int
    ) -> HubResourceVersion:
        """发布方只能发布自己的完整草稿版本；发布状态和资源目录在一个事务提交。"""

        with self._transaction() as (_, cursor):
            cursor.execute(
                "SELECT v.status, r.status FROM hub_resource_versions v "
                "JOIN hub_resources r ON r.platform_id=v.platform_id AND r.hub_resource_id=v.hub_resource_id "
                "WHERE v.platform_id=%s AND v.publisher_tenant_id=%s AND v.hub_resource_id=%s AND v.version=%s "
                "FOR UPDATE",
                (identity.platform_id, identity.tenant_id, hub_resource_id, version),
            )
            row = cursor.fetchone()
            if row is None:
                raise self._not_found()
            if str(row[0]) != HubVersionStatus.DRAFT.value:
                if str(row[0]) == HubVersionStatus.PUBLISHED.value:
                    cursor.execute(
                        self._version_select()
                        + " WHERE platform_id=%s AND hub_resource_id=%s AND version=%s",
                        (identity.platform_id, hub_resource_id, version),
                    )
                    published = cursor.fetchone()
                    return self._version_from_row(published)
                raise DomainError("INVALID_STATE", "只有 Hub DRAFT 版本可以发布")
            cursor.execute(
                "UPDATE hub_resource_versions SET status='PUBLISHED', published_at=CURRENT_TIMESTAMP(6) "
                "WHERE platform_id=%s AND publisher_tenant_id=%s AND hub_resource_id=%s AND version=%s AND status='DRAFT'",
                (identity.platform_id, identity.tenant_id, hub_resource_id, version),
            )
            if cursor.rowcount != 1:
                raise DomainError("INVALID_STATE", "Hub 版本状态已发生变化")
            cursor.execute(
                "UPDATE hub_resources SET status='PUBLISHED' WHERE platform_id=%s AND publisher_tenant_id=%s "
                "AND hub_resource_id=%s AND status IN ('DRAFT','PUBLISHED')",
                (identity.platform_id, identity.tenant_id, hub_resource_id),
            )
            cursor.execute(
                self._version_select()
                + " WHERE platform_id=%s AND hub_resource_id=%s AND version=%s",
                (identity.platform_id, hub_resource_id, version),
            )
            published = cursor.fetchone()
            if published is None:
                raise self._not_found()
            return self._version_from_row(published)

    def list_discoverable(
        self,
        identity: IdentityContext,
        *,
        resource_type: str | None = None,
        keyword: str | None = None,
        limit: int = 100,
    ) -> tuple[HubResourceSummary, ...]:
        """平台默认公开脱敏目录元数据；RESTRICTED 资源仅向发布方和获 Grant 租户展示。"""

        if not 1 <= limit <= 200:
            raise ValueError("资源中心列表 limit 必须处于 1 到 200 之间")
        normalized_type = HubResourceType(resource_type).value if resource_type else None
        normalized_keyword = keyword.strip() if keyword and keyword.strip() else None
        clauses = [
            "r.platform_id=%s",
            "r.status='PUBLISHED'",
            "v.status='PUBLISHED'",
            (
                "(r.visibility='PLATFORM' OR r.publisher_tenant_id=%s OR EXISTS ("
                "SELECT 1 FROM hub_resource_grants g WHERE g.platform_id=r.platform_id "
                "AND g.hub_resource_id=r.hub_resource_id AND g.recipient_tenant_id=%s "
                "AND g.status='ACTIVE' AND (g.expires_at IS NULL OR g.expires_at>CURRENT_TIMESTAMP(6))))"
            ),
        ]
        parameters: list[object] = [identity.platform_id, identity.tenant_id, identity.tenant_id]
        if normalized_type:
            clauses.append("r.resource_type=%s")
            parameters.append(normalized_type)
        if normalized_keyword:
            clauses.append("(r.name LIKE %s OR r.description LIKE %s)")
            escaped = normalized_keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            parameters.extend((f"%{escaped}%", f"%{escaped}%"))
        query = (
            "SELECT r.hub_resource_id, r.resource_type, r.name, r.description, t.name, r.visibility, MAX(v.version) "
            "FROM hub_resources r JOIN tenants t ON t.platform_id=r.platform_id "
            "AND t.tenant_id=r.publisher_tenant_id JOIN hub_resource_versions v "
            "ON v.platform_id=r.platform_id AND v.hub_resource_id=r.hub_resource_id WHERE "
            + " AND ".join(clauses)
            + " GROUP BY r.hub_resource_id, r.resource_type, r.name, r.description, t.name, r.visibility "
            "ORDER BY r.updated_at DESC, r.hub_resource_id LIMIT %s"
        )
        parameters.append(limit)
        with self._reader() as (_, cursor):
            cursor.execute(query, tuple(parameters))
            rows = cursor.fetchall()
            summaries: list[HubResourceSummary] = []
            for row in rows:
                hub_resource_id = str(row[0])
                latest_version = int(row[6])
                is_publisher = (
                    identity.tenant_id
                    == self._publisher_tenant_for_row(cursor, identity.platform_id, hub_resource_id)
                )
                if is_publisher:
                    modes = (HubImportMode.COPY, HubImportMode.REFERENCE)
                else:
                    cursor.execute(
                        "SELECT MAX(copy_allowed), MAX(reference_allowed) FROM hub_resource_grants "
                        "WHERE platform_id=%s AND hub_resource_id=%s AND hub_version=%s "
                        "AND recipient_tenant_id=%s AND status='ACTIVE' "
                        "AND (expires_at IS NULL OR expires_at>CURRENT_TIMESTAMP(6))",
                        (identity.platform_id, hub_resource_id, latest_version, identity.tenant_id),
                    )
                    grant = cursor.fetchone()
                    modes = tuple(
                        mode
                        for mode, allowed in (
                            (HubImportMode.COPY, bool(grant[0]) if grant[0] is not None else False),
                            (HubImportMode.REFERENCE, bool(grant[1]) if grant[1] is not None else False),
                        )
                        if allowed
                    )
                summaries.append(
                    HubResourceSummary(
                        hub_resource_id=hub_resource_id,
                        resource_type=HubResourceType(str(row[1])),
                        name=str(row[2]),
                        description=str(row[3]),
                        publisher_name=str(row[4]),
                        visibility=HubVisibility(str(row[5])),
                        latest_version=latest_version,
                        available_modes=modes,
                        is_publisher=is_publisher,
                    )
                )
        return tuple(summaries)

    def get_discoverable_version(
        self, identity: IdentityContext, hub_resource_id: str, version: int
    ) -> HubResourceDetail:
        """返回精确已发布版本的脱敏详情；不返回 Manifest/对象地址/源租户 ID。"""

        with self._reader() as (_, cursor):
            cursor.execute(
                "SELECT r.hub_resource_id, r.resource_type, r.name, r.description, t.name, r.visibility, "
                "v.version, v.manifest_sha256, "
                "(SELECT COUNT(*) FROM hub_resource_dependencies d WHERE d.platform_id=v.platform_id "
                "AND d.hub_resource_id=v.hub_resource_id AND d.hub_version=v.version), "
                "r.publisher_tenant_id "
                "FROM hub_resources r JOIN tenants t ON t.platform_id=r.platform_id "
                "AND t.tenant_id=r.publisher_tenant_id JOIN hub_resource_versions v "
                "ON v.platform_id=r.platform_id AND v.hub_resource_id=r.hub_resource_id "
                "WHERE r.platform_id=%s AND r.hub_resource_id=%s AND v.version=%s "
                "AND r.status='PUBLISHED' AND v.status='PUBLISHED' "
                "AND (r.visibility='PLATFORM' OR r.publisher_tenant_id=%s OR EXISTS ("
                "SELECT 1 FROM hub_resource_grants g WHERE g.platform_id=r.platform_id "
                "AND g.hub_resource_id=r.hub_resource_id AND g.hub_version=v.version "
                "AND g.recipient_tenant_id=%s AND g.status='ACTIVE' "
                "AND (g.expires_at IS NULL OR g.expires_at>CURRENT_TIMESTAMP(6))))",
                (
                    identity.platform_id,
                    hub_resource_id,
                    version,
                    identity.tenant_id,
                    identity.tenant_id,
                ),
            )
            row = cursor.fetchone()
            if row is None:
                raise self._not_found()
            if str(row[9]) == identity.tenant_id:
                modes = (HubImportMode.COPY, HubImportMode.REFERENCE)
            else:
                cursor.execute(
                    "SELECT MAX(copy_allowed), MAX(reference_allowed) FROM hub_resource_grants "
                    "WHERE platform_id=%s AND hub_resource_id=%s AND hub_version=%s "
                    "AND recipient_tenant_id=%s AND status='ACTIVE' "
                    "AND (expires_at IS NULL OR expires_at>CURRENT_TIMESTAMP(6))",
                    (identity.platform_id, hub_resource_id, version, identity.tenant_id),
                )
                grant = cursor.fetchone()
                modes = tuple(
                    mode
                    for mode, allowed in (
                        (HubImportMode.COPY, bool(grant[0]) if grant[0] is not None else False),
                        (HubImportMode.REFERENCE, bool(grant[1]) if grant[1] is not None else False),
                    )
                    if allowed
                )
        return HubResourceDetail(
            hub_resource_id=str(row[0]),
            resource_type=HubResourceType(str(row[1])),
            name=str(row[2]),
            description=str(row[3]),
            publisher_name=str(row[4]),
            visibility=HubVisibility(str(row[5])),
            version=int(row[6]),
            manifest_sha256=str(row[7]),
            dependency_count=int(row[8]),
            available_modes=modes,
        )

    @staticmethod
    def _publisher_tenant_for_row(cursor: Any, platform_id: str, hub_resource_id: str) -> str:
        cursor.execute(
            "SELECT publisher_tenant_id FROM hub_resources WHERE platform_id=%s AND hub_resource_id=%s",
            (platform_id, hub_resource_id),
        )
        row = cursor.fetchone()
        return str(row[0]) if row else ""

    def get_authorized_version(
        self,
        identity: IdentityContext,
        hub_resource_id: str,
        version: int,
        mode: HubImportMode,
    ) -> HubResourceVersion:
        """返回 Hub Package 内容前校验发布状态、平台、租户和精确模式 Grant。"""

        selected_mode = HubImportMode(mode)
        with self._reader() as (_, cursor):
            cursor.execute(
                "SELECT v.platform_id, v.hub_resource_id, v.version, v.publisher_tenant_id, "
                "v.source_resource_type, v.source_resource_id, v.source_resource_version, "
                "v.manifest_json, v.manifest_sha256, v.artifact_asset_id, v.status, v.created_by, v.published_at, "
                "r.status FROM hub_resource_versions v JOIN hub_resources r "
                "ON r.platform_id=v.platform_id AND r.hub_resource_id=v.hub_resource_id "
                "WHERE v.platform_id=%s AND v.hub_resource_id=%s AND v.version=%s",
                (identity.platform_id, hub_resource_id, version),
            )
            row = cursor.fetchone()
            if row is None or str(row[10]) != HubVersionStatus.PUBLISHED.value or str(row[13]) != HubResourceStatus.PUBLISHED.value:
                raise self._not_found()
            if str(row[3]) != identity.tenant_id:
                grant_column = "copy_allowed" if selected_mode is HubImportMode.COPY else "reference_allowed"
                cursor.execute(
                    "SELECT 1 FROM hub_resource_grants WHERE platform_id=%s AND hub_resource_id=%s "
                    "AND hub_version=%s AND publisher_tenant_id=%s AND recipient_tenant_id=%s "
                    "AND status='ACTIVE' AND " + grant_column + "=TRUE "
                    "AND (expires_at IS NULL OR expires_at>CURRENT_TIMESTAMP(6)) LIMIT 1",
                    (identity.platform_id, hub_resource_id, version, str(row[3]), identity.tenant_id),
                )
                if cursor.fetchone() is None:
                    # 目录元数据可见而 Package 内容按不存在处理，避免资源包探测。
                    raise self._not_found()
        return self._version_from_row(row[:13])

    @staticmethod
    def _import_from_row(row: tuple[Any, ...]) -> TenantResourceImport:
        return TenantResourceImport(
            platform_id=str(row[0]),
            tenant_id=str(row[1]),
            import_id=str(row[2]),
            grant_id=str(row[3]),
            hub_resource_id=str(row[4]),
            hub_version=int(row[5]),
            mode=HubImportMode(str(row[6])),
            local_resource_type=HubResourceType(str(row[7])),
            local_resource_id=str(row[8]),
            local_resource_version=int(row[9]),
            manifest_sha256=str(row[10]),
            status=TenantImportStatus(str(row[11])),
            imported_by=str(row[12]),
        )

    @staticmethod
    def _import_select() -> str:
        return (
            "SELECT platform_id, tenant_id, import_id, grant_id, hub_resource_id, hub_version, mode, "
            "local_resource_type, local_resource_id, local_resource_version, manifest_sha256, status, imported_by "
            "FROM tenant_resource_imports"
        )

    def get_authorized_grant(
        self,
        identity: IdentityContext,
        hub_resource_id: str,
        version: int,
        mode: HubImportMode,
    ) -> HubGrant:
        """取接收租户该精确版本/模式的有效授权，供导入事务再次锁定校验。"""

        selected_mode = HubImportMode(mode)
        grant_column = "copy_allowed" if selected_mode is HubImportMode.COPY else "reference_allowed"
        with self._reader() as (_, cursor):
            cursor.execute(
                "SELECT grant_id, platform_id, hub_resource_id, hub_version, publisher_tenant_id, "
                "recipient_tenant_id, copy_allowed, reference_allowed, created_by, expires_at, status, revoked_at "
                "FROM hub_resource_grants WHERE platform_id=%s AND hub_resource_id=%s AND hub_version=%s "
                "AND recipient_tenant_id=%s AND status='ACTIVE' AND " + grant_column + "=TRUE "
                "AND (expires_at IS NULL OR expires_at>CURRENT_TIMESTAMP(6)) "
                "ORDER BY created_at DESC, grant_id LIMIT 1",
                (identity.platform_id, hub_resource_id, version, identity.tenant_id),
            )
            row = cursor.fetchone()
        if row is None:
            raise self._not_found()
        return HubGrant(
            grant_id=str(row[0]),
            platform_id=str(row[1]),
            hub_resource_id=str(row[2]),
            hub_version=int(row[3]),
            publisher_tenant_id=str(row[4]),
            recipient_tenant_id=str(row[5]),
            copy_allowed=bool(row[6]),
            reference_allowed=bool(row[7]),
            created_by=str(row[8]),
            expires_at=row[9].replace(tzinfo=UTC) if row[9] is not None else None,
            status=HubGrantStatus(str(row[10])),
            revoked_at=row[11].replace(tzinfo=UTC) if row[11] is not None else None,
        )

    def list_grants(
        self, identity: IdentityContext, hub_resource_id: str, version: int
    ) -> tuple[HubGrant, ...]:
        """发布租户读取自己资源精确版本的 Grant 管理视图。"""

        with self._reader() as (_, cursor):
            cursor.execute(
                "SELECT grant_id, platform_id, hub_resource_id, hub_version, publisher_tenant_id, "
                "recipient_tenant_id, copy_allowed, reference_allowed, created_by, expires_at, status, revoked_at "
                "FROM hub_resource_grants WHERE platform_id=%s AND publisher_tenant_id=%s "
                "AND hub_resource_id=%s AND hub_version=%s ORDER BY created_at DESC, grant_id DESC",
                (identity.platform_id, identity.tenant_id, hub_resource_id, version),
            )
            rows = cursor.fetchall()
        return tuple(
            HubGrant(
                grant_id=str(row[0]),
                platform_id=str(row[1]),
                hub_resource_id=str(row[2]),
                hub_version=int(row[3]),
                publisher_tenant_id=str(row[4]),
                recipient_tenant_id=str(row[5]),
                copy_allowed=bool(row[6]),
                reference_allowed=bool(row[7]),
                created_by=str(row[8]),
                expires_at=row[9].replace(tzinfo=UTC) if row[9] is not None else None,
                status=HubGrantStatus(str(row[10])),
                revoked_at=row[11].replace(tzinfo=UTC) if row[11] is not None else None,
            )
            for row in rows
        )

    def get_import(
        self,
        identity: IdentityContext,
        hub_resource_id: str,
        version: int,
        mode: HubImportMode,
    ) -> TenantResourceImport | None:
        with self._reader() as (_, cursor):
            cursor.execute(
                self._import_select()
                + " WHERE platform_id=%s AND tenant_id=%s AND hub_resource_id=%s AND hub_version=%s AND mode=%s",
                (
                    identity.platform_id,
                    identity.tenant_id,
                    hub_resource_id,
                    version,
                    HubImportMode(mode).value,
                ),
            )
            row = cursor.fetchone()
        return self._import_from_row(row) if row is not None else None

    def get_import_for_local_resource(
        self,
        identity: IdentityContext,
        resource_type: HubResourceType,
        local_resource_id: str,
        local_resource_version: int,
    ) -> TenantResourceImport | None:
        """只在当前平台与租户范围内反查本地资源的 Hub 来源映射。"""

        with self._reader() as (_, cursor):
            cursor.execute(
                self._import_select()
                + " WHERE platform_id=%s AND tenant_id=%s AND local_resource_type=%s "
                "AND local_resource_id=%s AND local_resource_version=%s AND status='ACTIVE'",
                (
                    identity.platform_id,
                    identity.tenant_id,
                    HubResourceType(resource_type).value,
                    local_resource_id,
                    local_resource_version,
                ),
            )
            row = cursor.fetchone()
        return self._import_from_row(row) if row is not None else None

    def get_import_by_id(
        self, identity: IdentityContext, import_id: str
    ) -> TenantResourceImport:
        with self._reader() as (_, cursor):
            cursor.execute(
                self._import_select()
                + " WHERE platform_id=%s AND tenant_id=%s AND import_id=%s",
                (identity.platform_id, identity.tenant_id, import_id),
            )
            row = cursor.fetchone()
        if row is None:
            raise self._not_found()
        return self._import_from_row(row)

    def list_imports(
        self,
        identity: IdentityContext,
        *,
        status: TenantImportStatus | None = None,
        resource_type: HubResourceType | None = None,
        limit: int = 100,
    ) -> tuple[TenantResourceImport, ...]:
        if not 1 <= limit <= 200:
            raise DomainError("INVALID_ARGUMENT", "limit 必须在 1 到 200 之间")
        conditions = ["platform_id=%s", "tenant_id=%s"]
        parameters: list[object] = [identity.platform_id, identity.tenant_id]
        if status is not None:
            conditions.append("status=%s")
            parameters.append(TenantImportStatus(status).value)
        if resource_type is not None:
            conditions.append("local_resource_type=%s")
            parameters.append(HubResourceType(resource_type).value)
        parameters.append(limit)
        with self._reader() as (_, cursor):
            cursor.execute(
                self._import_select()
                + " WHERE "
                + " AND ".join(conditions)
                + " ORDER BY imported_at DESC, import_id DESC LIMIT %s",
                tuple(parameters),
            )
            rows = cursor.fetchall()
        return tuple(self._import_from_row(row) for row in rows)

    def revoke_import(self, identity: IdentityContext, import_id: str) -> None:
        """仅切断当前租户的 Hub 导入映射，保留代理与历史 Run 审计。"""

        with self._transaction() as (_, cursor):
            cursor.execute(
                "UPDATE tenant_resource_imports SET status='REVOKED' "
                "WHERE platform_id=%s AND tenant_id=%s AND import_id=%s AND status='ACTIVE'",
                (identity.platform_id, identity.tenant_id, import_id),
            )
            if cursor.rowcount != 1:
                cursor.execute(
                    self._import_select()
                    + " WHERE platform_id=%s AND tenant_id=%s AND import_id=%s AND status='REVOKED'",
                    (identity.platform_id, identity.tenant_id, import_id),
                )
                if cursor.fetchone() is None:
                    raise self._not_found()

    def begin_import(
        self, identity: IdentityContext, value: TenantResourceImport
    ) -> TenantResourceImport:
        """先持久化 IMPORTING 记录并锁定有效 Grant，再由应用物化租户资源代理。"""

        if (
            value.platform_id != identity.platform_id
            or value.tenant_id != identity.tenant_id
            or value.imported_by != identity.user_id
            or value.status is not TenantImportStatus.IMPORTING
        ):
            raise self._not_found()
        grant_column = "copy_allowed" if value.mode is HubImportMode.COPY else "reference_allowed"
        with self._transaction() as (_, cursor):
            cursor.execute(
                "SELECT status, " + grant_column + " FROM hub_resource_grants "
                "WHERE platform_id=%s AND grant_id=%s AND hub_resource_id=%s AND hub_version=%s "
                "AND recipient_tenant_id=%s FOR UPDATE",
                (
                    identity.platform_id,
                    value.grant_id,
                    value.hub_resource_id,
                    value.hub_version,
                    identity.tenant_id,
                ),
            )
            grant = cursor.fetchone()
            if grant is None or str(grant[0]) != HubGrantStatus.ACTIVE.value or not bool(grant[1]):
                raise self._not_found()
            cursor.execute(
                "SELECT expires_at>CURRENT_TIMESTAMP(6) FROM hub_resource_grants "
                "WHERE platform_id=%s AND grant_id=%s AND expires_at IS NOT NULL",
                (identity.platform_id, value.grant_id),
            )
            expiration = cursor.fetchone()
            if expiration is not None and not bool(expiration[0]):
                raise self._not_found()
            cursor.execute(
                self._import_select()
                + " WHERE platform_id=%s AND tenant_id=%s AND hub_resource_id=%s AND hub_version=%s AND mode=%s FOR UPDATE",
                (
                    identity.platform_id,
                    identity.tenant_id,
                    value.hub_resource_id,
                    value.hub_version,
                    value.mode.value,
                ),
            )
            existing = cursor.fetchone()
            if existing is not None:
                imported = self._import_from_row(existing)
                if imported.manifest_sha256 != value.manifest_sha256:
                    raise DomainError("INVALID_STATE", "同一 Hub 版本的清单摘要已变化")
                if imported.status is not TenantImportStatus.ACTIVE:
                    cursor.execute(
                        "UPDATE tenant_resource_imports SET grant_id=%s, status='IMPORTING', "
                        "imported_by=%s WHERE platform_id=%s AND tenant_id=%s AND import_id=%s",
                        (
                            value.grant_id,
                            identity.user_id,
                            identity.platform_id,
                            identity.tenant_id,
                            imported.import_id,
                        ),
                    )
                    imported = TenantResourceImport(
                        imported.platform_id,
                        imported.tenant_id,
                        imported.import_id,
                        value.grant_id,
                        imported.hub_resource_id,
                        imported.hub_version,
                        imported.mode,
                        imported.local_resource_type,
                        imported.local_resource_id,
                        imported.local_resource_version,
                        imported.manifest_sha256,
                        identity.user_id,
                        TenantImportStatus.IMPORTING,
                    )
                return imported
            cursor.execute(
                "INSERT INTO tenant_resource_imports "
                "(platform_id, tenant_id, import_id, grant_id, hub_resource_id, hub_version, mode, "
                "local_resource_type, local_resource_id, local_resource_version, manifest_sha256, status, imported_by) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'IMPORTING',%s)",
                (
                    value.platform_id,
                    value.tenant_id,
                    value.import_id,
                    value.grant_id,
                    value.hub_resource_id,
                    value.hub_version,
                    value.mode.value,
                    value.local_resource_type.value,
                    value.local_resource_id,
                    value.local_resource_version,
                    value.manifest_sha256,
                    value.imported_by,
                ),
            )
        return value

    def complete_import(
        self, identity: IdentityContext, import_id: str
    ) -> TenantResourceImport:
        with self._transaction() as (_, cursor):
            cursor.execute(
                "UPDATE tenant_resource_imports SET status='ACTIVE' "
                "WHERE platform_id=%s AND tenant_id=%s AND import_id=%s AND status='IMPORTING'",
                (identity.platform_id, identity.tenant_id, import_id),
            )
            if cursor.rowcount != 1:
                raise self._not_found()
            cursor.execute(
                self._import_select()
                + " WHERE platform_id=%s AND tenant_id=%s AND import_id=%s",
                (identity.platform_id, identity.tenant_id, import_id),
            )
            row = cursor.fetchone()
        if row is None:
            raise self._not_found()
        return self._import_from_row(row)

    def fail_import(self, identity: IdentityContext, import_id: str) -> None:
        """保留导入记录和租户资源 ID 以便安全重试，不将部分内容标为 ACTIVE。"""

        with self._transaction() as (_, cursor):
            cursor.execute(
                "UPDATE tenant_resource_imports SET status='FAILED' "
                "WHERE platform_id=%s AND tenant_id=%s AND import_id=%s AND status='IMPORTING'",
                (identity.platform_id, identity.tenant_id, import_id),
            )
            if cursor.rowcount != 1:
                raise self._not_found()

    def save_grant(self, identity: IdentityContext, value: HubGrant) -> None:
        """只有该资源版本的发布租户管理员可向同平台接收租户授予导入模式。"""

        if value.platform_id != identity.platform_id or value.publisher_tenant_id != identity.tenant_id:
            raise self._not_found()
        if value.created_by != identity.user_id:
            raise DomainError("AUTHORIZATION_DENIED", "授权人必须是当前认证用户")
        with self._transaction() as (_, cursor):
            cursor.execute(
                "SELECT 1 FROM hub_resource_versions v JOIN hub_resources r "
                "ON r.platform_id=v.platform_id AND r.hub_resource_id=v.hub_resource_id "
                "WHERE v.platform_id=%s AND v.hub_resource_id=%s AND v.version=%s "
                "AND v.publisher_tenant_id=%s AND v.status='PUBLISHED' AND r.status='PUBLISHED' FOR UPDATE",
                (identity.platform_id, value.hub_resource_id, value.hub_version, identity.tenant_id),
            )
            if cursor.fetchone() is None:
                raise self._not_found()
            cursor.execute(
                "INSERT INTO hub_resource_grants "
                "(platform_id, grant_id, hub_resource_id, hub_version, publisher_tenant_id, recipient_tenant_id, "
                "copy_allowed, reference_allowed, status, expires_at, revoked_at, created_by) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    value.platform_id,
                    value.grant_id,
                    value.hub_resource_id,
                    value.hub_version,
                    value.publisher_tenant_id,
                    value.recipient_tenant_id,
                    value.copy_allowed,
                    value.reference_allowed,
                    value.status.value,
                    self._db_datetime(value.expires_at),
                    self._db_datetime(value.revoked_at),
                    value.created_by,
                ),
            )

    def revoke_grant(self, identity: IdentityContext, grant_id: str) -> None:
        """发布租户撤销权限后阻止后续 Package 读取；历史授权行保留审计。"""

        with self._transaction() as (_, cursor):
            cursor.execute(
                "UPDATE hub_resource_grants SET status='REVOKED', revoked_at=CURRENT_TIMESTAMP(6) "
                "WHERE platform_id=%s AND publisher_tenant_id=%s AND grant_id=%s AND status='ACTIVE'",
                (identity.platform_id, identity.tenant_id, grant_id),
            )
            if cursor.rowcount != 1:
                raise self._not_found()
