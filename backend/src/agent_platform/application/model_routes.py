"""模型路由租户目录和可替换 Provider 选择服务。"""

from __future__ import annotations

from typing import Protocol
from uuid import uuid4

from agent_platform.domain.identity import IdentityContext
from agent_platform.domain.model_routes import ModelProfile, ModelRoute, SecretRef


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

    def __init__(self, repository: ModelRouteRepository) -> None:
        self.repository = repository

    def create(
        self,
        identity: IdentityContext,
        alias: str,
        model_name: str,
        provider: str,
        endpoint: str,
        secret_id: str | None,
    ) -> ModelRoute:
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
                secret_ref=SecretRef(secret_id) if secret_id else None,
            ),
        )
        self.repository.save(identity, route)
        return route

    def get(self, identity: IdentityContext, route_id: str) -> ModelRoute:
        route = self.repository.get(identity, route_id)
        if route is None:
            from agent_platform.domain.common import DomainError

            raise DomainError("RESOURCE_NOT_FOUND", "模型路由不存在")
        return route

    def list(self, identity: IdentityContext) -> tuple[ModelRoute, ...]:
        return self.repository.list(identity)
