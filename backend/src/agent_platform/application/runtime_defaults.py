"""分别维护并解析用户、租户和平台的执行引擎与模型默认。"""

from __future__ import annotations

from dataclasses import dataclass

from agent_platform.application.engine_providers import (
    EngineProviderRegistry,
    validate_profile_worker_policy,
)
from agent_platform.application.model_management import ModelManagementService
from agent_platform.domain.catalog import ExecutionProfile, LifecycleStatus
from agent_platform.domain.common import DomainError
from agent_platform.domain.identity import IdentityContext
from agent_platform.domain.model_routes import ModelRoute
from agent_platform.domain.runtime_defaults import (
    EngineProfileDefaultBinding,
    ModelRouteDefaultBinding,
)
from agent_platform.ports.catalog_repository import CatalogRepository


@dataclass(frozen=True, slots=True)
class EffectiveEngineProfileDefault:
    """已解析的执行 Profile、精确版本、作用域和绑定来源。"""

    binding: EngineProfileDefaultBinding
    profile: ExecutionProfile
    source: str


@dataclass(frozen=True, slots=True)
class EffectiveModelRouteDefault:
    """已解析的 ModelRoute 和绑定来源；不携带 Secret 明文。"""

    binding: ModelRouteDefaultBinding
    route: ModelRoute
    source: str


class RuntimeDefaultsService:
    """在一个身份上下文中独立维护引擎和模型默认，禁止跨租户绑定。"""

    _scope_precedence = (
        ("user", "USER_DEFAULT"),
        ("tenant", "TENANT_DEFAULT"),
        ("platform", "PLATFORM_DEFAULT"),
    )

    def __init__(
        self,
        repository: CatalogRepository,
        model_management: ModelManagementService,
        engine_providers: EngineProviderRegistry | None = None,
    ) -> None:
        self.repository = repository
        self.model_management = model_management
        self.engine_providers = engine_providers

    @staticmethod
    def _require_member(identity: IdentityContext) -> None:
        if not identity.roles.intersection({"admin", "member"}):
            raise DomainError("AUTHORIZATION_DENIED", "当前用户无权管理个人运行默认")

    @staticmethod
    def _require_tenant_admin(identity: IdentityContext) -> None:
        if "admin" not in identity.roles:
            raise DomainError("AUTHORIZATION_DENIED", "只有租户管理员可以修改租户运行默认")

    @staticmethod
    def _scope_id(identity: IdentityContext, scope_type: str) -> str:
        if scope_type == "user":
            return identity.user_id
        if scope_type == "tenant":
            return identity.tenant_id
        if scope_type == "platform":
            return identity.platform_id
        raise DomainError("INVALID_ARGUMENT", "运行默认作用域不支持")

    def get_engine_profile_default(
        self, identity: IdentityContext, scope_type: str
    ) -> EngineProfileDefaultBinding | None:
        self._require_member(identity)
        if scope_type not in {"user", "tenant"}:
            raise DomainError("INVALID_ARGUMENT", "只能直接读取个人或租户作用域默认")
        return self.repository.get_engine_profile_default(
            identity, scope_type, self._scope_id(identity, scope_type)
        )

    def set_engine_profile_default(
        self,
        identity: IdentityContext,
        scope_type: str,
        profile_id: str | None,
        profile_version: int | None,
    ) -> EngineProfileDefaultBinding | None:
        if scope_type == "user":
            self._require_member(identity)
        elif scope_type == "tenant":
            self._require_tenant_admin(identity)
        else:
            raise DomainError("INVALID_ARGUMENT", "只能写入个人或租户作用域默认")
        if (profile_id is None) != (profile_version is None):
            raise DomainError("INVALID_ARGUMENT", "Profile 标识和版本必须同时提供或同时清除")
        if profile_id is not None and profile_version is not None:
            profile = self.repository.get_profile_version(identity, profile_id, profile_version)
            if profile.status is not LifecycleStatus.PUBLISHED:
                raise DomainError("DEPENDENCY_VALIDATION_ERROR", "默认引擎必须引用已发布 Profile")
            validate_profile_worker_policy(profile)
            if self.engine_providers is not None:
                self.engine_providers.resolve(profile.engine_name)
        return self.repository.set_engine_profile_default(
            identity,
            scope_type,
            self._scope_id(identity, scope_type),
            profile_id,
            profile_version,
        )

    def resolve_engine_profile_default(
        self, identity: IdentityContext
    ) -> EffectiveEngineProfileDefault | None:
        self._require_member(identity)
        for scope_type, source in self._scope_precedence:
            binding = self.repository.get_engine_profile_default(
                identity, scope_type, self._scope_id(identity, scope_type)
            )
            if binding is None or binding.status != "ACTIVE":
                continue
            profile = self.repository.get_profile_version(
                identity, binding.profile_id, binding.profile_version
            )
            if profile.status is not LifecycleStatus.PUBLISHED:
                raise DomainError(
                    "DEPENDENCY_VALIDATION_ERROR",
                    "作用域默认引用的 ExecutionProfile 不再处于已发布状态",
                    {"scope_type": scope_type, "profile_id": binding.profile_id},
                )
            return EffectiveEngineProfileDefault(binding, profile, source)
        return None

    def get_model_route_default(
        self, identity: IdentityContext, scope_type: str
    ) -> ModelRouteDefaultBinding | None:
        self._require_member(identity)
        if scope_type not in {"user", "tenant"}:
            raise DomainError("INVALID_ARGUMENT", "只能直接读取个人或租户作用域默认")
        return self.repository.get_model_route_default(
            identity, scope_type, self._scope_id(identity, scope_type)
        )

    def set_model_route_default(
        self,
        identity: IdentityContext,
        scope_type: str,
        route_id: str | None,
    ) -> ModelRouteDefaultBinding | None:
        if scope_type == "user":
            self._require_member(identity)
        elif scope_type == "tenant":
            self._require_tenant_admin(identity)
        else:
            raise DomainError("INVALID_ARGUMENT", "只能写入个人或租户作用域默认")
        if route_id is not None:
            selectable = {
                route.route_id for route in self.model_management.selectable_routes(identity)
            }
            if route_id not in selectable:
                # Route ID 不在当前租户的可用集合中时统一隐藏跨租户资源是否存在。
                raise DomainError("RESOURCE_NOT_FOUND", "可用模型路由不存在")
        return self.repository.set_model_route_default(
            identity, scope_type, self._scope_id(identity, scope_type), route_id
        )

    def get_selectable_model_route(
        self, identity: IdentityContext, route_id: str
    ) -> ModelRoute:
        """只解析当前身份可用路由，隐藏跨租户或未授权资源是否存在。"""

        route = next(
            (
                item
                for item in self.model_management.selectable_routes(identity)
                if item.route_id == route_id
            ),
            None,
        )
        if route is None:
            raise DomainError("RESOURCE_NOT_FOUND", "可用模型路由不存在")
        return route

    def resolve_model_route_default(
        self, identity: IdentityContext
    ) -> EffectiveModelRouteDefault | None:
        self._require_member(identity)
        for scope_type, source in self._scope_precedence:
            binding = self.repository.get_model_route_default(
                identity, scope_type, self._scope_id(identity, scope_type)
            )
            if binding is None or binding.status != "ACTIVE":
                continue
            try:
                route = self.get_selectable_model_route(identity, binding.route_id)
            except DomainError as error:
                if error.code != "RESOURCE_NOT_FOUND":
                    raise
                raise DomainError(
                    "DEPENDENCY_VALIDATION_ERROR",
                    "作用域默认引用的 ModelRoute 已停用或不再授权",
                    {"scope_type": scope_type},
                ) from error
            return EffectiveModelRouteDefault(binding, route, source)
        return None
