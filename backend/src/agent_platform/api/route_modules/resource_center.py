"""Hub 资源发现、发布、Grant 和租户导入 API。"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Query

from agent_platform.api.schemas import (
    HubGrantCreateRequest,
    HubImportUpgradeRequest,
    HubPackageCreateRequest,
    HubPackageVersionCreateRequest,
    HubResourceImportRequest,
)
from agent_platform.domain.resource_center import (
    HubImportMode,
    HubResource,
    HubResourceType,
    HubResourceVersion,
    TenantImportStatus,
)

from .common import Identity, ResourceCenter, _check_tenant

router = APIRouter()

def _hub_version_response(resource: HubResource, version: HubResourceVersion) -> dict[str, object]:
    return {
        "hub_resource_id": resource.hub_resource_id,
        "resource_type": resource.resource_type.value,
        "name": resource.name,
        "visibility": resource.visibility.value,
        "version": version.version,
        "status": version.status.value,
        "manifest_sha256": version.manifest.sha256,
        "dependency_count": len(version.manifest.data.get("dependencies", [])),
    }


def _hub_summary_response(value: object) -> dict[str, object]:
    return {
        "hub_resource_id": value.hub_resource_id,
        "resource_type": value.resource_type.value,
        "name": value.name,
        "description": value.description,
        "publisher_name": value.publisher_name,
        "visibility": value.visibility.value,
        "latest_version": value.latest_version,
        "available_modes": [mode.value for mode in value.available_modes],
        "is_publisher": value.is_publisher,
    }


@router.get("/hub/resources")
def list_hub_resources(
    identity: Identity,
    service: ResourceCenter,
    resource_type: Annotated[HubResourceType | None, Query()] = None,
    keyword: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
    mode: Annotated[HubImportMode | None, Query()] = None,
    scope: Annotated[Literal["DISCOVERABLE", "OWNED"], Query()] = "DISCOVERABLE",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, object]:
    """浏览平台可发现的脱敏目录元数据；模式筛选只显示当前租户已获授权的导入能力。"""

    return {
        "items": [
            _hub_summary_response(item)
            for item in service.list_discoverable(
                identity,
                resource_type=resource_type.value if resource_type else None,
                keyword=keyword,
                mode=mode,
                owned_only=scope == "OWNED",
                limit=limit,
            )
        ]
    }


@router.get("/hub/resources/{hub_resource_id}/versions/{version}")
def get_hub_resource_version_detail(
    hub_resource_id: str, version: int, identity: Identity, service: ResourceCenter
) -> dict[str, object]:
    """返回脱敏版本详情/摘要，不返回 Package Manifest 或源租户内部标识。"""

    value = service.get_discoverable_version(identity, hub_resource_id, version)
    return {
        "hub_resource_id": value.hub_resource_id,
        "resource_type": value.resource_type.value,
        "name": value.name,
        "description": value.description,
        "publisher_name": value.publisher_name,
        "visibility": value.visibility.value,
        "version": value.version,
        "manifest_sha256": value.manifest_sha256,
        "dependency_count": value.dependency_count,
        "available_modes": [mode.value for mode in value.available_modes],
    }


@router.post("/hub/resources", status_code=201)
def create_hub_resource_package(
    request: HubPackageCreateRequest, identity: Identity, service: ResourceCenter
) -> dict[str, object]:
    """将本租户已发布的 Employee/Skill/Tool 精确版本打成 Hub DRAFT Package。"""

    _check_tenant(identity, request)
    resource, version = service.create_package(
        identity,
        request.resource_type,
        request.source_resource_id,
        request.source_resource_version,
        visibility=request.visibility,
    )
    return _hub_version_response(resource, version)


@router.post("/hub/resources/{hub_resource_id}/versions", status_code=201)
def create_hub_resource_version(
    hub_resource_id: str,
    request: HubPackageVersionCreateRequest,
    identity: Identity,
    service: ResourceCenter,
) -> dict[str, object]:
    """给当前租户已有 Hub 条目追加新源版本，不覆盖旧 Package。"""

    _check_tenant(identity, request)
    resource, version = service.create_package(
        identity,
        service.repository.get_owned_resource(identity, hub_resource_id).resource_type,
        request.source_resource_id,
        request.source_resource_version,
        hub_resource_id=hub_resource_id,
    )
    return _hub_version_response(resource, version)


@router.post("/hub/resources/{hub_resource_id}/versions/{version}/publish")
def publish_hub_resource_version(
    hub_resource_id: str, version: int, identity: Identity, service: ResourceCenter
) -> dict[str, object]:
    """发布当前租户的 Hub Package 版本，返回固定摘要供后续授权。"""

    published = service.publish_version(identity, hub_resource_id, version)
    resource = service.repository.get_owned_resource(identity, hub_resource_id)
    return _hub_version_response(resource, published)


def _hub_grant_response(grant: object) -> dict[str, object]:
    return {
        "grant_id": grant.grant_id,
        "hub_resource_id": grant.hub_resource_id,
        "hub_version": grant.hub_version,
        "recipient_tenant_id": grant.recipient_tenant_id,
        "copy_allowed": grant.copy_allowed,
        "reference_allowed": grant.reference_allowed,
        "expires_at": grant.expires_at.isoformat() if grant.expires_at else None,
        "status": grant.status.value,
        "created_by": grant.created_by,
    }


@router.post("/hub/resources/{hub_resource_id}/versions/{version}/grants", status_code=201)
def create_hub_grant(
    hub_resource_id: str,
    version: int,
    request: HubGrantCreateRequest,
    identity: Identity,
    service: ResourceCenter,
) -> dict[str, object]:
    """配置精确 Hub 版本的 COPY/REFERENCE Grant。"""

    _check_tenant(identity, request)
    grant = service.create_grant(
        identity,
        hub_resource_id,
        version,
        request.recipient_tenant_id,
        copy_allowed=request.copy_allowed,
        reference_allowed=request.reference_allowed,
        expires_at=request.expires_at,
    )
    return _hub_grant_response(grant)


@router.get("/hub/resources/{hub_resource_id}/versions/{version}/grants")
def list_hub_grants(
    hub_resource_id: str, version: int, identity: Identity, service: ResourceCenter
) -> dict[str, object]:
    """仅发布租户管理员可查看该精确版本的接收租户授权。"""

    return {
        "items": [
            _hub_grant_response(item)
            for item in service.list_grants(identity, hub_resource_id, version)
        ]
    }


@router.get("/hub/resources/{hub_resource_id}/versions/{version}/manifest")
def get_authorized_hub_manifest(
    hub_resource_id: str,
    version: int,
    identity: Identity,
    service: ResourceCenter,
    mode: Annotated[HubImportMode, Query()],
) -> dict[str, object]:
    """先按接收方 Grant 校验模式，再返回固定 Manifest 以展示已授权依赖。"""

    package = service.get_authorized_manifest(identity, hub_resource_id, version, mode)
    return {
        "hub_resource_id": package.hub_resource_id,
        "version": package.version,
        "manifest_sha256": package.manifest.sha256,
        "manifest": package.manifest.data,
    }


@router.post("/hub/imports", status_code=201)
def import_hub_resource(
    request: HubResourceImportRequest, identity: Identity, service: ResourceCenter
) -> dict[str, object]:
    """按有效 Grant 将精确 Hub 版本导入为本地 COPY 副本或 REFERENCE 代理。"""

    _check_tenant(identity, request)
    imported = service.import_resource(
        identity,
        request.hub_resource_id,
        request.hub_version,
        request.mode,
    )
    return {
        "import_id": imported.import_id,
        "hub_resource_id": imported.hub_resource_id,
        "hub_version": imported.hub_version,
        "mode": imported.mode.value,
        "local_resource_type": imported.local_resource_type.value,
        "local_resource_id": imported.local_resource_id,
        "local_resource_version": imported.local_resource_version,
        "manifest_sha256": imported.manifest_sha256,
        "status": imported.status.value,
    }


def _hub_import_response(imported: object) -> dict[str, object]:
    return {
        "import_id": imported.import_id,
        "hub_resource_id": imported.hub_resource_id,
        "hub_version": imported.hub_version,
        "mode": imported.mode.value,
        "local_resource_type": imported.local_resource_type.value,
        "local_resource_id": imported.local_resource_id,
        "local_resource_version": imported.local_resource_version,
        "manifest_sha256": imported.manifest_sha256,
        "status": imported.status.value,
    }


@router.get("/hub/imports")
def list_hub_imports(
    identity: Identity,
    service: ResourceCenter,
    status: Annotated[TenantImportStatus | None, Query()] = None,
    resource_type: Annotated[HubResourceType | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, object]:
    """列出当前租户的导入状态；不返回发布租户内部 ID 或 Package 正文。"""

    return {
        "items": [
            _hub_import_response(item)
            for item in service.list_imports(
                identity,
                status=status,
                resource_type=resource_type,
                limit=limit,
            )
        ]
    }


@router.post("/hub/imports/{import_id}/upgrade", status_code=201)
def upgrade_hub_import(
    import_id: str,
    request: HubImportUpgradeRequest,
    identity: Identity,
    service: ResourceCenter,
) -> dict[str, object]:
    """显式导入同一 Hub 条目的更高版本，返回新代理而不改写旧绑定。"""

    _check_tenant(identity, request)
    return _hub_import_response(service.upgrade_import(identity, import_id, request.hub_version))


@router.delete("/hub/imports/{import_id}")
def revoke_hub_import(
    import_id: str, identity: Identity, service: ResourceCenter
) -> dict[str, object]:
    """撤销当前租户 REFERENCE 映射；保留历史代理与已完成 Run 快照。"""

    return _hub_import_response(service.revoke_import(identity, import_id))


@router.delete("/hub/grants/{grant_id}")
def revoke_hub_grant(
    grant_id: str, identity: Identity, service: ResourceCenter
) -> dict[str, str]:
    """发布租户管理员撤销精确版本 Grant，后续 Reference Run 会重新授权失败。"""

    service.revoke_grant(identity, grant_id)
    return {"grant_id": grant_id, "status": "REVOKED"}
