"""模型路由租户目录和可替换 Provider 选择服务。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol
from uuid import uuid4

from agent_platform.domain.common import DomainError
from agent_platform.domain.identity import IdentityContext
from agent_platform.domain.model_routes import (
    ModelCatalogStatus,
    ModelConnectionStatus,
    ModelProfile,
    ModelRoute,
    SecretRef,
)


class ModelRouteRepository(Protocol):
    """模型路由的租户范围持久化端口。"""

    def save(self, identity: IdentityContext, route: ModelRoute) -> None: ...

    def get(self, identity: IdentityContext, route_id: str) -> ModelRoute | None: ...

    def list(self, identity: IdentityContext) -> tuple[ModelRoute, ...]: ...


class InMemoryModelRouteRepository:
    def __init__(self) -> None:
        self._routes: dict[tuple[str, str, str], ModelRoute] = {}

    def save(self, identity: IdentityContext, route: ModelRoute) -> None:
        self._routes[(identity.platform_id, identity.tenant_id, route.route_id)] = route

    def get(self, identity: IdentityContext, route_id: str) -> ModelRoute | None:
        return self._routes.get((identity.platform_id, identity.tenant_id, route_id))

    def list(self, identity: IdentityContext) -> tuple[ModelRoute, ...]:
        return tuple(
            route for (platform_id, tenant_id, _), route in self._routes.items()
            if platform_id == identity.platform_id and tenant_id == identity.tenant_id
        )


class ModelRouteService:
    """创建、切换和查询模型路由；Secret 只作为 SecretRef 保存。"""

    def __init__(
        self,
        repository: ModelRouteRepository,
        model_catalog_repository: object | None = None,
    ) -> None:
        self.repository = repository
        self.model_catalog_repository = model_catalog_repository or repository

    def create(
        self,
        identity: IdentityContext,
        alias: str,
        model_name: str,
        provider: str,
        endpoint: str,
        secret_id: str | None,
    ) -> ModelRoute:
        self._ensure_alias_available(identity, alias)
        route = ModelRoute(
            route_id=f"route-{uuid4().hex}",
            tenant_id=identity.tenant_id,
            alias=alias,
            profile=ModelProfile(
                profile_id=f"model-profile-{uuid4().hex}",
                tenant_id=identity.tenant_id,
                model_name=model_name,
                provider=provider,
                endpoint=endpoint,
                secret_ref=(
                    SecretRef(secret_id, scope=identity.tenant_id, provider="environment")
                    if secret_id
                    else None
                ),
            ),
        )
        self.repository.save(identity, route)
        return self.get(identity, route.route_id)

    def _ensure_alias_available(
        self, identity: IdentityContext, alias: str, *, excluding_route_id: str | None = None
    ) -> None:
        if any(
            route.alias == alias and route.route_id != excluding_route_id
            for route in self.repository.list(identity)
        ):
            raise DomainError("INVALID_ARGUMENT", "当前租户已存在相同别名的模型路由")

    def _catalog_connection(self, identity: IdentityContext, connection_id: str):
        getter = getattr(self.model_catalog_repository, "get_model_connection", None)
        if not callable(getter):
            raise DomainError("INVALID_STATE", "模型路由仓储未配置模型连接目录")
        connection = getter(identity, connection_id)
        if connection.tenant_id != identity.tenant_id:
            raise DomainError("AUTHORIZATION_DENIED", "模型连接不属于当前租户")
        if connection.status is not ModelConnectionStatus.ACTIVE:
            raise DomainError("DEPENDENCY_VALIDATION_ERROR", "模型连接已停用")
        return connection

    def _catalog_item(self, identity: IdentityContext, connection_id: str, model_id: str):
        getter = getattr(self.model_catalog_repository, "get_model_catalog_item", None)
        if not callable(getter):
            raise DomainError("INVALID_STATE", "模型路由仓储未配置模型目录")
        try:
            item = getter(identity, connection_id, model_id)
        except DomainError as error:
            if error.code == "RESOURCE_NOT_FOUND":
                raise DomainError(
                    "DEPENDENCY_VALIDATION_ERROR",
                    "模型目录项不存在于当前租户连接",
                    {"connection_id": connection_id, "model_id": model_id},
                ) from error
            raise
        if item.tenant_id != identity.tenant_id or item.connection_id != connection_id:
            raise DomainError("AUTHORIZATION_DENIED", "模型目录项不属于当前租户连接")
        if item.status is not ModelCatalogStatus.ACTIVE:
            raise DomainError("DEPENDENCY_VALIDATION_ERROR", "模型目录项已停用")
        return item

    def create_from_catalog(
        self,
        identity: IdentityContext,
        *,
        alias: str,
        connection_id: str,
        model_id: str,
        parameters: Mapping[str, object] | None = None,
        pricing_snapshot_ref: str | None = None,
    ) -> ModelRoute:
        """从当前租户的启用目录项创建可供员工绑定的 ModelRoute。"""

        self._ensure_alias_available(identity, alias)
        connection = self._catalog_connection(identity, connection_id)
        item = self._catalog_item(identity, connection_id, model_id)
        route = ModelRoute(
            route_id=f"route-{uuid4().hex}",
            tenant_id=identity.tenant_id,
            alias=alias,
            profile=ModelProfile(
                profile_id=f"model-profile-{uuid4().hex}",
                tenant_id=identity.tenant_id,
                model_name=item.model_id,
                provider=connection.provider_kind,
                endpoint=connection.endpoint,
                protocol=connection.protocol,
                secret_ref=connection.secret_ref,
                pricing_snapshot_ref=pricing_snapshot_ref,
                parameters=parameters or {},
                connection_id=connection.connection_id,
                catalog_model_id=item.model_id,
            ),
        )
        self.repository.save(identity, route)
        return self.get(identity, route.route_id)

    def update(
        self,
        identity: IdentityContext,
        route_id: str,
        *,
        alias: str | None = None,
        connection_id: str | None = None,
        model_id: str | None = None,
        parameters: Mapping[str, object] | None = None,
        pricing_snapshot_ref: str | None = None,
    ) -> ModelRoute:
        """发布路由的新配置版本；Run 快照继续固定已提交时的值。"""

        current = self.get(identity, route_id)
        selected_alias = alias if alias is not None else current.alias
        self._ensure_alias_available(identity, selected_alias, excluding_route_id=route_id)
        selected_connection_id = connection_id or current.profile.connection_id
        selected_model_id = model_id or current.profile.catalog_model_id or current.model_name
        if selected_connection_id is None:
            if connection_id is not None or model_id is not None:
                raise DomainError("DEPENDENCY_VALIDATION_ERROR", "旧版模型路由需先迁移到模型连接")
            updated_profile = current.profile
        else:
            connection = self._catalog_connection(identity, selected_connection_id)
            item = self._catalog_item(identity, selected_connection_id, selected_model_id)
            updated_profile = ModelProfile(
                profile_id=current.profile.profile_id,
                tenant_id=identity.tenant_id,
                model_name=item.model_id,
                provider=connection.provider_kind,
                endpoint=connection.endpoint,
                protocol=connection.protocol,
                secret_ref=connection.secret_ref,
                pricing_snapshot_ref=(
                    pricing_snapshot_ref
                    if pricing_snapshot_ref is not None
                    else current.profile.pricing_snapshot_ref
                ),
                parameters=(
                    dict(parameters)
                    if parameters is not None
                    else dict(current.profile.parameters)
                ),
                connection_id=connection.connection_id,
                catalog_model_id=item.model_id,
            )
        updated = ModelRoute(
            route_id=current.route_id,
            tenant_id=current.tenant_id,
            alias=selected_alias,
            profile=updated_profile,
            status=current.status,
            route_version=current.route_version + 1,
        )
        self.repository.save(identity, updated)
        return self.get(identity, route_id)

    def set_status(self, identity: IdentityContext, route_id: str, status: str) -> ModelRoute:
        """停用路由或在其连接和模型仍有效时重新启用。"""

        current = self.get(identity, route_id)
        normalized = status.upper().strip()
        if normalized not in {"ACTIVE", "DISABLED"}:
            raise DomainError("INVALID_ARGUMENT", "模型路由状态只能为 ACTIVE 或 DISABLED")
        if normalized == "ACTIVE" and current.profile.connection_id is not None:
            self._catalog_connection(identity, current.profile.connection_id)
            self._catalog_item(
                identity,
                current.profile.connection_id,
                current.profile.catalog_model_id or current.model_name,
            )
        updated = ModelRoute(
            route_id=current.route_id,
            tenant_id=current.tenant_id,
            alias=current.alias,
            profile=current.profile,
            status=normalized,
            route_version=current.route_version + 1,
        )
        self.repository.save(identity, updated)
        return self.get(identity, route_id)

    def disable(self, identity: IdentityContext, route_id: str) -> ModelRoute:
        return self.set_status(identity, route_id, "DISABLED")

    def enable(self, identity: IdentityContext, route_id: str) -> ModelRoute:
        return self.set_status(identity, route_id, "ACTIVE")

    def get(self, identity: IdentityContext, route_id: str) -> ModelRoute:
        route = self.repository.get(identity, route_id)
        if route is None:
            from agent_platform.domain.common import DomainError

            raise DomainError("RESOURCE_NOT_FOUND", "模型路由不存在")
        return route

    def list(self, identity: IdentityContext) -> tuple[ModelRoute, ...]:
        return self.repository.list(identity)
