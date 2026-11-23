"""管理接收方 Deployment 的不可变运行配置修订。"""

from __future__ import annotations

from collections.abc import Mapping

from agent_platform.application.engine_providers import (
    EngineProviderRegistry,
    validate_profile_worker_policy,
)
from agent_platform.application.model_management import ModelManagementService
from agent_platform.domain.catalog import Deployment, LifecycleStatus
from agent_platform.domain.common import DomainError
from agent_platform.domain.deployment_runtime import (
    DeploymentRuntimeConfig,
    DeploymentRuntimeMode,
)
from agent_platform.domain.identity import IdentityContext
from agent_platform.domain.model_routes import ModelRouteSnapshot
from agent_platform.ports.catalog_repository import CatalogRepository, DeploymentRepository


class DeploymentRuntimeConfigService:
    """按租户权限校验运行资源，并以期望修订写入 Deployment 配置。"""

    _approval_policy_keys = frozenset({"require_approval_for"})
    _approval_requirements = frozenset({"high_risk", "external_write", "destructive", "all"})

    def __init__(
        self,
        repository: CatalogRepository & DeploymentRepository,
        model_management: ModelManagementService,
        engine_providers: EngineProviderRegistry | None = None,
    ) -> None:
        self.repository = repository
        self.model_management = model_management
        self.engine_providers = engine_providers

    @staticmethod
    def _require_member(identity: IdentityContext) -> None:
        if not identity.roles.intersection({"admin", "member"}):
            raise DomainError("AUTHORIZATION_DENIED", "当前用户无权读取 Deployment 运行配置")

    @staticmethod
    def _require_admin(identity: IdentityContext) -> None:
        if "admin" not in identity.roles:
            raise DomainError("AUTHORIZATION_DENIED", "只有租户管理员可以修改 Deployment 运行配置")

    def get(
        self, identity: IdentityContext, deployment_id: str
    ) -> tuple[Deployment, DeploymentRuntimeConfig | None]:
        self._require_member(identity)
        deployment = self.repository.get_deployment(identity, deployment_id)
        return deployment, self.repository.get_deployment_runtime_config(identity, deployment_id)

    def save(
        self,
        identity: IdentityContext,
        deployment_id: str,
        *,
        expected_config_version: int | None,
        mode: str,
        execution_profile_id: str | None = None,
        execution_profile_version: int | None = None,
        model_route_id: str | None = None,
        workspace_profile_id: str | None = None,
        workspace_profile_version: int | None = None,
        sandbox_profile_id: str | None = None,
        sandbox_profile_version: int | None = None,
        approval_policy: Mapping[str, object] | None = None,
        runtime_options: Mapping[str, object] | None = None,
    ) -> tuple[Deployment, DeploymentRuntimeConfig]:
        self._require_admin(identity)
        deployment = self.repository.get_deployment(identity, deployment_id)
        if deployment.status is LifecycleStatus.ARCHIVED:
            raise DomainError("INVALID_STATE", "已归档 Deployment 不能修改运行配置")

        try:
            config_mode = DeploymentRuntimeMode(mode)
        except ValueError as error:
            raise DomainError("INVALID_ARGUMENT", "运行配置模式必须为 INHERIT 或 OVERRIDE") from error

        if workspace_profile_id is not None or sandbox_profile_id is not None:
            raise DomainError(
                "ENGINE_CAPABILITY_UNSUPPORTED",
                "当前 Worker 尚未应用 Deployment 级 Workspace/Sandbox 覆盖；请清除这些字段后保存",
            )
        approval = dict(approval_policy or {})
        unknown_approval_keys = set(approval).difference(self._approval_policy_keys)
        if unknown_approval_keys:
            raise DomainError("INVALID_ARGUMENT", "审批策略只能增加受控审批要求")
        requirements = approval.get("require_approval_for", [])
        if not isinstance(requirements, list) or any(
            not isinstance(item, str) or item not in self._approval_requirements
            for item in requirements
        ):
            raise DomainError("INVALID_ARGUMENT", "附加审批要求不在允许列表中")
        if requirements:
            raise DomainError(
                "ENGINE_CAPABILITY_UNSUPPORTED",
                "当前 Worker 尚未把 Deployment 附加审批要求接入工具审批流程",
            )

        if execution_profile_id is not None:
            if execution_profile_version is None:
                raise DomainError("INVALID_ARGUMENT", "Deployment Profile 覆盖需要精确版本号")
            profile = self.repository.get_profile_version(
                identity, execution_profile_id, execution_profile_version
            )
            if profile.status is not LifecycleStatus.PUBLISHED:
                raise DomainError("DEPENDENCY_VALIDATION_ERROR", "Deployment 必须引用已发布 ExecutionProfile")
            validate_profile_worker_policy(profile)
            if self.engine_providers is not None:
                self.engine_providers.resolve(profile.engine_name)
        elif execution_profile_version is not None:
            raise DomainError("INVALID_ARGUMENT", "Deployment Profile 版本不能单独提供")
        if model_route_id is not None:
            available = {
                route.route_id for route in self.model_management.selectable_routes(identity)
            }
            if model_route_id not in available:
                raise DomainError("RESOURCE_NOT_FOUND", "可用模型路由不存在")
        if workspace_profile_id is not None:
            if workspace_profile_version is None:
                workspace_profile_version = 1
            self.repository.get_workspace_profile(
                identity, workspace_profile_id, version=workspace_profile_version
            )
        elif workspace_profile_version is not None:
            raise DomainError("INVALID_ARGUMENT", "Workspace 版本不能单独提供")
        if sandbox_profile_id is not None:
            if sandbox_profile_version is None:
                sandbox_profile_version = 1
            self.repository.get_sandbox_profile(
                identity, sandbox_profile_id, version=sandbox_profile_version
            )
        elif sandbox_profile_version is not None:
            raise DomainError("INVALID_ARGUMENT", "Sandbox 版本不能单独提供")

        options = dict(runtime_options or {})
        if options:
            raise DomainError("INVALID_ARGUMENT", "当前没有已启用的高级运行参数")

        route_snapshot = self._model_route_snapshot(identity, model_route_id) if model_route_id else None
        current_version = deployment.runtime_config_version or 0
        config = DeploymentRuntimeConfig(
            platform_id=identity.platform_id,
            tenant_id=identity.tenant_id,
            deployment_id=deployment_id,
            config_version=current_version + 1,
            config_mode=config_mode,
            execution_profile_id=execution_profile_id,
            execution_profile_version=execution_profile_version,
            model_route_id=model_route_id,
            model_route_snapshot=route_snapshot,
            workspace_profile_id=workspace_profile_id,
            workspace_profile_version=workspace_profile_version,
            sandbox_profile_id=sandbox_profile_id,
            sandbox_profile_version=sandbox_profile_version,
            approval_policy=approval,
            runtime_options=options,
            created_by=identity.user_id,
        )
        saved = self.repository.save_deployment_runtime_config(
            identity,
            deployment_id,
            expected_config_version,
            config,
        )
        updated_deployment = self.repository.get_deployment(identity, deployment_id)
        return updated_deployment, saved

    def _model_route_snapshot(
        self, identity: IdentityContext, route_id: str
    ) -> dict[str, object]:
        route = self.repository.get_model_route(identity, route_id)
        if route.profile.connection_id is None:
            raise DomainError("DEPENDENCY_VALIDATION_ERROR", "模型路由尚未绑定租户连接")
        connection = self.repository.get_model_connection(identity, route.profile.connection_id)
        snapshot = ModelRouteSnapshot(
            tenant_id=identity.tenant_id,
            route_id=route.route_id,
            route_version=route.route_version,
            route_alias=route.alias,
            connection_id=connection.connection_id,
            connection_version=connection.version,
            provider_kind=connection.provider_kind,
            model_id=route.profile.catalog_model_id or route.profile.model_name,
            protocol=route.profile.protocol,
            endpoint=route.profile.endpoint,
            secret_ref=route.profile.secret_ref,
            parameters=route.profile.parameters,
            pricing_snapshot_ref=route.profile.pricing_snapshot_ref,
        )
        return snapshot.to_snapshot_dict()
