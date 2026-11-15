"""资源中心发布包、目录发现与租户授权的持久化端口。"""

from __future__ import annotations

from typing import Protocol

from agent_platform.domain.identity import IdentityContext
from agent_platform.domain.resource_center import (
    HubGrant,
    HubImportMode,
    HubResource,
    HubResourceDependency,
    HubResourceDetail,
    HubResourceSummary,
    HubResourceType,
    HubResourceVersion,
    TenantImportStatus,
    TenantResourceImport,
)


class ResourceCenterRepository(Protocol):
    """隔离平台级目录元数据与通过 Grant 保护的 Package 内容读取。"""

    def save_resource(self, identity: IdentityContext, value: HubResource) -> None: ...

    def save_version(self, identity: IdentityContext, value: HubResourceVersion) -> None: ...

    def create_package(
        self,
        identity: IdentityContext,
        resource: HubResource,
        version: HubResourceVersion,
        dependencies: tuple[HubResourceDependency, ...],
    ) -> None: ...

    def create_version(
        self,
        identity: IdentityContext,
        version: HubResourceVersion,
        dependencies: tuple[HubResourceDependency, ...],
    ) -> None: ...

    def get_owned_resource(self, identity: IdentityContext, hub_resource_id: str) -> HubResource: ...

    def next_version(self, identity: IdentityContext, hub_resource_id: str) -> int: ...

    def find_published_source_version(
        self,
        identity: IdentityContext,
        resource_type: HubResourceType,
        source_resource_id: str,
        source_resource_version: int,
    ) -> HubResourceVersion: ...

    def publish_version(
        self, identity: IdentityContext, hub_resource_id: str, version: int
    ) -> HubResourceVersion: ...

    def list_discoverable(
        self,
        identity: IdentityContext,
        *,
        resource_type: str | None = None,
        keyword: str | None = None,
        limit: int = 100,
    ) -> tuple[HubResourceSummary, ...]: ...

    def list_grants(
        self, identity: IdentityContext, hub_resource_id: str, version: int
    ) -> tuple[HubGrant, ...]: ...

    def get_discoverable_version(
        self, identity: IdentityContext, hub_resource_id: str, version: int
    ) -> HubResourceDetail: ...

    def get_authorized_version(
        self,
        identity: IdentityContext,
        hub_resource_id: str,
        version: int,
        mode: HubImportMode,
    ) -> HubResourceVersion: ...

    def get_authorized_grant(
        self,
        identity: IdentityContext,
        hub_resource_id: str,
        version: int,
        mode: HubImportMode,
    ) -> HubGrant: ...

    def get_import(
        self,
        identity: IdentityContext,
        hub_resource_id: str,
        version: int,
        mode: HubImportMode,
    ) -> TenantResourceImport | None: ...

    def get_import_for_local_resource(
        self,
        identity: IdentityContext,
        resource_type: HubResourceType,
        local_resource_id: str,
        local_resource_version: int,
    ) -> TenantResourceImport | None: ...

    def get_import_by_id(
        self, identity: IdentityContext, import_id: str
    ) -> TenantResourceImport: ...

    def list_imports(
        self,
        identity: IdentityContext,
        *,
        status: TenantImportStatus | None = None,
        resource_type: HubResourceType | None = None,
        limit: int = 100,
    ) -> tuple[TenantResourceImport, ...]: ...

    def revoke_import(self, identity: IdentityContext, import_id: str) -> None: ...

    def begin_import(
        self, identity: IdentityContext, value: TenantResourceImport
    ) -> TenantResourceImport: ...

    def complete_import(self, identity: IdentityContext, import_id: str) -> TenantResourceImport: ...

    def fail_import(self, identity: IdentityContext, import_id: str) -> None: ...

    def save_grant(self, identity: IdentityContext, value: HubGrant) -> None: ...

    def revoke_grant(self, identity: IdentityContext, grant_id: str) -> None: ...
