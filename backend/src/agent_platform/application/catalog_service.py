"""数字员工目录的内存仓储和发布、部署应用服务。"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from uuid import uuid4

from agent_platform.domain.catalog import (
    Deployment,
    DigitalEmployee,
    DigitalEmployeeVersion,
    ExecutionProfile,
    LifecycleStatus,
    SkillType,
    SkillVersion,
    ToolVersion,
)
from agent_platform.domain.common import DomainError
from agent_platform.domain.identity import IdentityContext
from agent_platform.domain.model_routes import ModelRoute
from agent_platform.ports.catalog_repository import CatalogRepository, DeploymentRepository
from agent_platform.ports.workspaces import SandboxProfile, WorkspaceProfile


def _key(identity: IdentityContext, resource_id: str) -> tuple[str, str, str]:
    return identity.platform_id, identity.tenant_id, resource_id


class InMemoryCatalogRepository:
    """进程内目录实现；所有索引包含平台和租户，避免裸 ID 越权。"""

    def __init__(self) -> None:
        self.tools: dict[tuple[str, str, str], ToolVersion] = {}
        self.tool_versions: dict[tuple[str, str, str, int], ToolVersion] = {}
        self.skills: dict[tuple[str, str, str], SkillVersion] = {}
        self.skill_versions: dict[tuple[str, str, str, int], SkillVersion] = {}
        self.employees: dict[tuple[str, str, str], DigitalEmployee] = {}
        self.employee_versions: dict[tuple[str, str, str, int], DigitalEmployeeVersion] = {}
        self.profiles: dict[tuple[str, str, str], ExecutionProfile] = {}
        self.profile_versions: dict[tuple[str, str, str, int], ExecutionProfile] = {}
        self.model_routes: dict[tuple[str, str, str], object] = {}
        self.workspace_profiles: dict[tuple[str, str, str], object] = {}
        self.sandbox_profiles: dict[tuple[str, str, str], object] = {}
        self.deployments: dict[tuple[str, str, str], Deployment] = {}

    def _get(self, values: Mapping, key: tuple, resource: str):
        try:
            return values[key]
        except KeyError as error:
            raise DomainError("RESOURCE_NOT_FOUND", "资源不存在", {"resource": resource}) from error

    def get_tool(self, identity: IdentityContext, tool_id: str) -> ToolVersion:
        return self._get(self.tools, _key(identity, tool_id), "tool")

    def get_tool_version(self, identity: IdentityContext, tool_id: str, version: int) -> ToolVersion:
        return self._get(self.tool_versions, (*_key(identity, tool_id), version), "tool")

    def get_skill(self, identity: IdentityContext, skill_id: str) -> SkillVersion:
        return self._get(self.skills, _key(identity, skill_id), "skill")

    def get_skill_version(self, identity: IdentityContext, skill_id: str, version: int) -> SkillVersion:
        return self._get(self.skill_versions, (*_key(identity, skill_id), version), "skill")

    def get_employee(self, identity: IdentityContext, employee_id: str) -> DigitalEmployee:
        return self._get(self.employees, _key(identity, employee_id), "digital_employee")

    def get_version(
        self, identity: IdentityContext, employee_id: str, version: int
    ) -> DigitalEmployeeVersion:
        return self._get(
            self.employee_versions,
            (*_key(identity, employee_id), version),
            "digital_employee_version",
        )

    def get_employee_version(
        self, identity: IdentityContext, employee_id: str, version: int
    ) -> DigitalEmployeeVersion:
        return self.get_version(identity, employee_id, version)

    def list_employee_versions(
        self, identity: IdentityContext, employee_id: str
    ) -> tuple[DigitalEmployeeVersion, ...]:
        prefix = (*_key(identity, employee_id),)
        return tuple(
            value for key, value in self.employee_versions.items()
            if key[:3] == prefix
        )

    def get_profile(self, identity: IdentityContext, profile_id: str) -> ExecutionProfile:
        return self._get(self.profiles, _key(identity, profile_id), "execution_profile")

    def get_profile_version(
        self, identity: IdentityContext, profile_id: str, version: int
    ) -> ExecutionProfile:
        return self._get(self.profile_versions, (*_key(identity, profile_id), version), "execution_profile")

    def get_deployment(self, identity: IdentityContext, deployment_id: str) -> Deployment:
        return self._get(self.deployments, _key(identity, deployment_id), "deployment")

    def list_deployments(self, identity: IdentityContext) -> tuple[Deployment, ...]:
        prefix = (identity.platform_id, identity.tenant_id)
        return tuple(
            deployment
            for (platform_id, tenant_id, _), deployment in self.deployments.items()
            if (platform_id, tenant_id) == prefix
        )

    def save_tool(self, identity: IdentityContext, value: ToolVersion) -> None:
        self.tools[_key(identity, value.tool_id)] = value
        self.tool_versions[(*_key(identity, value.tool_id), value.version)] = value

    def save_skill(self, identity: IdentityContext, value: SkillVersion) -> None:
        self.skills[_key(identity, value.skill_id)] = value
        self.skill_versions[(*_key(identity, value.skill_id), value.version)] = value

    def save_employee(self, identity: IdentityContext, value: DigitalEmployee) -> None:
        self.employees[_key(identity, value.employee_id)] = value

    def save_version(self, identity: IdentityContext, value: DigitalEmployeeVersion) -> None:
        self.employee_versions[(*_key(identity, value.employee_id), value.version)] = value

    def save_employee_version(self, identity: IdentityContext, value: DigitalEmployeeVersion) -> None:
        self.save_version(identity, value)

    def save_profile(self, identity: IdentityContext, value: ExecutionProfile) -> None:
        self.profiles[_key(identity, value.profile_id)] = value
        self.profile_versions[(*_key(identity, value.profile_id), value.version)] = value

    def save_model_route(self, identity: IdentityContext, value: ModelRoute) -> None:
        self.model_routes[_key(identity, value.route_id)] = value

    def save(self, identity: IdentityContext, value: ModelRoute) -> None:
        self.save_model_route(identity, value)

    def get_model_route(self, identity: IdentityContext, route_id: str) -> ModelRoute:
        return self._get(self.model_routes, _key(identity, route_id), "model_route")

    def get(self, identity: IdentityContext, route_id: str) -> ModelRoute | None:
        try:
            return self.get_model_route(identity, route_id)
        except DomainError as error:
            if error.code == "RESOURCE_NOT_FOUND":
                return None
            raise

    def list(self, identity: IdentityContext) -> tuple[ModelRoute, ...]:
        prefix = (identity.platform_id, identity.tenant_id)
        return tuple(
            route for (platform_id, tenant_id, _), route in self.model_routes.items()
            if (platform_id, tenant_id) == prefix
        )

    def save_workspace_profile(self, identity: IdentityContext, value: WorkspaceProfile, *, version: int = 1) -> None:
        self.workspace_profiles[(*_key(identity, value.profile_id), version)] = value

    def get_workspace_profile(self, identity: IdentityContext, profile_id: str, *, version: int = 1) -> WorkspaceProfile:
        return self._get(self.workspace_profiles, (*_key(identity, profile_id), version), "workspace_profile")

    def save_sandbox_profile(self, identity: IdentityContext, value: SandboxProfile, *, version: int = 1) -> None:
        self.sandbox_profiles[(*_key(identity, value.profile_id), version)] = value

    def get_sandbox_profile(self, identity: IdentityContext, profile_id: str, *, version: int = 1) -> SandboxProfile:
        return self._get(self.sandbox_profiles, (*_key(identity, profile_id), version), "sandbox_profile")

    def save_deployment(self, identity: IdentityContext, value: Deployment) -> None:
        self.deployments[_key(identity, value.deployment_id)] = value

    def delete_tool(self, identity: IdentityContext, tool_id: str) -> None:
        current = self.get_tool(identity, tool_id)
        self.tools.pop(_key(identity, tool_id), None)
        self.tool_versions.pop((*_key(identity, tool_id), current.version), None)

    def delete_skill(self, identity: IdentityContext, skill_id: str) -> None:
        current = self.get_skill(identity, skill_id)
        self.skills.pop(_key(identity, skill_id), None)
        self.skill_versions.pop((*_key(identity, skill_id), current.version), None)

    def delete_employee(self, identity: IdentityContext, employee_id: str) -> None:
        self.get_employee(identity, employee_id)
        self.employees.pop(_key(identity, employee_id), None)

    def list_tools(self, identity: IdentityContext) -> tuple[ToolVersion, ...]:
        prefix = (identity.platform_id, identity.tenant_id)
        return tuple(value for (platform, tenant, _), value in self.tools.items() if (platform, tenant) == prefix)

    def list_skills(self, identity: IdentityContext) -> tuple[SkillVersion, ...]:
        prefix = (identity.platform_id, identity.tenant_id)
        return tuple(value for (platform, tenant, _), value in self.skills.items() if (platform, tenant) == prefix)

    def list_employees(self, identity: IdentityContext) -> tuple[DigitalEmployee, ...]:
        prefix = (identity.platform_id, identity.tenant_id)
        return tuple(value for (platform, tenant, _), value in self.employees.items() if (platform, tenant) == prefix)

    def list_profiles(self, identity: IdentityContext) -> tuple[ExecutionProfile, ...]:
        prefix = (identity.platform_id, identity.tenant_id)
        return tuple(value for (platform, tenant, _), value in self.profiles.items() if (platform, tenant) == prefix)


class CatalogService:
    """执行目录写入、依赖交集和已发布版本部署。"""

    def __init__(
        self,
        repository: CatalogRepository & DeploymentRepository,
        id_factory: Callable[[str], str] | None = None,
    ) -> None:
        self.repository = repository
        self._id_factory = id_factory or (lambda prefix: f"{prefix}-{uuid4().hex[:10]}")

    def _new_id(self, prefix: str) -> str:
        return self._id_factory(prefix)

    def _ensure_profile(self, identity: IdentityContext) -> ExecutionProfile:
        try:
            return self.repository.get_profile(identity, "profile-fake")
        except DomainError as error:
            if error.code != "RESOURCE_NOT_FOUND":
                raise
        profile = ExecutionProfile(
            profile_id="profile-fake",
            tenant_id=identity.tenant_id,
            engine_name="fake",
            model_alias="fake-model",
        ).publish()
        self.repository.save_profile(identity, profile)
        return profile

    def create_tool(
        self,
        identity: IdentityContext,
        name: str,
        description: str,
        input_schema: Mapping[str, object],
    ) -> ToolVersion:
        value = ToolVersion(self._new_id("tool"), identity.tenant_id, name, description, input_schema)
        self.repository.save_tool(identity, value)
        return value

    def update_tool(self, identity: IdentityContext, tool_id: str, *, description: str | None = None, input_schema: Mapping[str, object] | None = None) -> ToolVersion:
        current = self.repository.get_tool(identity, tool_id)
        current.assert_mutable()
        updated = replace(current, description=description or current.description, input_schema=input_schema or current.input_schema)
        self.repository.save_tool(identity, updated)
        return updated

    def delete_tool(self, identity: IdentityContext, tool_id: str) -> None:
        current = self.repository.get_tool(identity, tool_id)
        current.assert_mutable()
        self.repository.delete_tool(identity, tool_id)

    def publish_tool(self, identity: IdentityContext, tool_id: str) -> ToolVersion:
        current = self.repository.get_tool(identity, tool_id)
        published = current.publish()
        self.repository.save_tool(identity, published)
        return published

    def create_skill(
        self,
        identity: IdentityContext,
        name: str,
        skill_type: SkillType,
        instruction: str,
        allowed_tool_ids: Iterable[str],
    ) -> SkillVersion:
        allowed_ids = tuple(allowed_tool_ids)
        value = SkillVersion(
            skill_id=self._new_id("skill"),
            tenant_id=identity.tenant_id,
            name=name,
            skill_type=skill_type,
            instruction=instruction,
            allowed_tool_ids=allowed_ids,
            allowed_tool_versions=tuple(
                (
                    tool_id,
                    self._latest_version(self.repository.get_tool, identity, tool_id, default=1),
                )
                for tool_id in allowed_ids
            ),
        )
        self.repository.save_skill(identity, value)
        return value

    def update_skill(self, identity: IdentityContext, skill_id: str, *, instruction: str | None = None, allowed_tool_ids: Iterable[str] | None = None) -> SkillVersion:
        current = self.repository.get_skill(identity, skill_id)
        current.assert_mutable()
        updated = replace(current, instruction=instruction or current.instruction, allowed_tool_ids=tuple(allowed_tool_ids) if allowed_tool_ids is not None else current.allowed_tool_ids)
        self.repository.save_skill(identity, updated)
        return updated

    def delete_skill(self, identity: IdentityContext, skill_id: str) -> None:
        current = self.repository.get_skill(identity, skill_id)
        current.assert_mutable()
        self.repository.delete_skill(identity, skill_id)

    def publish_skill(self, identity: IdentityContext, skill_id: str) -> SkillVersion:
        current = self.repository.get_skill(identity, skill_id)
        for tool_id, tool_version in current.allowed_tool_versions:
            tool = self.repository.get_tool_version(identity, tool_id, tool_version)
            if tool.status is not LifecycleStatus.PUBLISHED:
                raise DomainError(
                    "DEPENDENCY_VALIDATION_ERROR",
                    "Skill 引用的 Tool 尚未发布",
                    {"skill_id": skill_id, "tool_id": tool_id},
                )
        published = current.publish()
        self.repository.save_skill(identity, published)
        return published

    def update_employee(self, identity: IdentityContext, employee_id: str, *, name: str | None = None, role_name: str | None = None, description: str | None = None) -> DigitalEmployee:
        current = self.repository.get_employee(identity, employee_id)
        updated = replace(current, name=name or current.name, role_name=role_name or current.role_name, description=description or current.description)
        self.repository.save_employee(identity, updated)
        return updated

    def delete_employee(self, identity: IdentityContext, employee_id: str) -> None:
        self.repository.delete_employee(identity, employee_id)

    def create_profile(
        self,
        identity: IdentityContext,
        engine_name: str,
        model_alias: str,
        *,
        model_route_id: str | None = None,
        workspace_profile_id: str | None = None,
        workspace_profile_version: int | None = None,
        sandbox_profile_id: str | None = None,
        sandbox_profile_version: int | None = None,
        approval_policy: str = "none",
    ) -> ExecutionProfile:
        dependencies = []
        if model_route_id is not None:
            dependencies.append((model_route_id, self.repository.get_model_route, "model_route", None))
        if workspace_profile_id is not None:
            dependencies.append((workspace_profile_id, self.repository.get_workspace_profile, "workspace_profile", workspace_profile_version or 1))
        if sandbox_profile_id is not None:
            dependencies.append((sandbox_profile_id, self.repository.get_sandbox_profile, "sandbox_profile", sandbox_profile_version or 1))
        for ref, getter, kind, version in dependencies:
            try:
                if version is None:
                    getter(identity, ref)
                else:
                    getter(identity, ref, version=version)
            except DomainError as error:
                if error.code != "RESOURCE_NOT_FOUND":
                    raise
                raise DomainError(
                    "DEPENDENCY_VALIDATION_ERROR",
                    "ExecutionProfile 引用不存在",
                    {"kind": kind, "id": ref},
                ) from error
        profile = ExecutionProfile(
            profile_id=self._new_id("profile"),
            tenant_id=identity.tenant_id,
            engine_name=engine_name,
            model_alias=model_alias,
            model_route_id=model_route_id,
            workspace_profile_id=workspace_profile_id,
            workspace_profile_version=workspace_profile_version,
            sandbox_profile_id=sandbox_profile_id,
            sandbox_profile_version=sandbox_profile_version,
            approval_policy=approval_policy,
        ).publish()
        self.repository.save_profile(identity, profile)
        return profile

    def list_profiles(self, identity: IdentityContext) -> tuple[ExecutionProfile, ...]:
        return self.repository.list_profiles(identity)

    def create_employee(
        self, identity: IdentityContext, name: str, role_name: str, description: str
    ) -> DigitalEmployee:
        value = DigitalEmployee(
            self._new_id("employee"), identity.tenant_id, name, role_name, description
        )
        self.repository.save_employee(identity, value)
        self._ensure_profile(identity)
        return value

    def create_employee_version(
        self,
        identity: IdentityContext,
        employee_id: str,
        system_prompt: str,
        skill_version_ids: Iterable[str],
        tool_version_ids: Iterable[str],
        execution_profile_id: str = "profile-fake",
        model_route_id: str | None = None,
        workspace_profile_id: str | None = None,
        sandbox_profile_id: str | None = None,
        approval_policy: str = "none",
        workspace_profile_version: int | None = None,
        sandbox_profile_version: int | None = None,
    ) -> DigitalEmployeeVersion:
        employee = self.repository.get_employee(identity, employee_id)
        versions = self.repository.list_employee_versions(identity, employee.employee_id)
        next_version = max((version.version for version in versions), default=0) + 1
        self._ensure_profile(identity)
        try:
            profile_version = self.repository.get_profile(identity, execution_profile_id).version
        except DomainError:
            profile_version = 1
        skill_ids = tuple(skill_version_ids)
        tool_ids = tuple(tool_version_ids)
        value = DigitalEmployeeVersion(
            employee_id=employee.employee_id,
            tenant_id=identity.tenant_id,
            version=next_version,
            system_prompt=system_prompt,
            skill_version_ids=skill_ids,
            tool_version_ids=tool_ids,
            execution_profile_id=execution_profile_id,
            model_route_id=model_route_id,
            workspace_profile_id=workspace_profile_id,
            workspace_profile_version=workspace_profile_version,
            sandbox_profile_id=sandbox_profile_id,
            sandbox_profile_version=sandbox_profile_version,
            approval_policy=approval_policy,
            execution_profile_version=profile_version,
            skill_version_refs=tuple(
                (skill_id, self._latest_version(self.repository.get_skill, identity, skill_id, default=1))
                for skill_id in skill_ids
            ),
            tool_version_refs=tuple(
                (tool_id, self._latest_version(self.repository.get_tool, identity, tool_id, default=1))
                for tool_id in tool_ids
            ),
        )
        self.repository.save_employee_version(identity, value)
        return value

    def publish_employee_version(
        self, identity: IdentityContext, employee_id: str, version: int
    ) -> DigitalEmployeeVersion:
        current = self.repository.get_employee_version(identity, employee_id, version)
        missing: list[dict[str, str]] = []
        for skill_id, skill_version in current.skill_version_refs:
            try:
                skill = self.repository.get_skill_version(identity, skill_id, skill_version)
            except DomainError:
                missing.append({"kind": "skill", "id": skill_id})
            else:
                if skill.status is not LifecycleStatus.PUBLISHED:
                    missing.append({"kind": "skill", "id": skill_id})
        for tool_id, tool_version in current.tool_version_refs:
            try:
                tool = self.repository.get_tool_version(identity, tool_id, tool_version)
            except DomainError:
                missing.append({"kind": "tool", "id": tool_id})
            else:
                if tool.status is not LifecycleStatus.PUBLISHED:
                    missing.append({"kind": "tool", "id": tool_id})
        try:
            profile = self.repository.get_profile_version(
                identity, current.execution_profile_id, current.execution_profile_version
            )
        except DomainError:
            missing.append({"kind": "execution_profile", "id": current.execution_profile_id})
        else:
            if profile.status is not LifecycleStatus.PUBLISHED:
                missing.append({"kind": "execution_profile", "id": current.execution_profile_id})
        for field_name, version_field, getter, kind in (
            ("model_route_id", None, self.repository.get_model_route, "model_route"),
            ("workspace_profile_id", "workspace_profile_version", self.repository.get_workspace_profile, "workspace_profile"),
            ("sandbox_profile_id", "sandbox_profile_version", self.repository.get_sandbox_profile, "sandbox_profile"),
        ):
            ref = getattr(current, field_name)
            if ref is not None:
                try:
                    if version_field is None:
                        getter(identity, ref)
                    else:
                        getter(identity, ref, version=getattr(current, version_field) or 1)
                except DomainError as error:
                    if error.code != "RESOURCE_NOT_FOUND":
                        raise
                    missing.append({"kind": kind, "id": ref})
        if missing:
            raise DomainError(
                "DEPENDENCY_VALIDATION_ERROR",
                "数字员工版本依赖未满足发布条件",
                {"missing": missing},
            )
        published = current.publish()
        self.repository.save_employee_version(identity, published)
        return published

    def deploy(self, identity: IdentityContext, employee_id: str, version: int) -> Deployment:
        employee_version = self.repository.get_employee_version(identity, employee_id, version)
        deployment = Deployment(
            deployment_id=self._new_id("deployment"),
            tenant_id=identity.tenant_id,
            employee_id=employee_id,
            employee_version=employee_version,
        )
        self.repository.save_deployment(identity, deployment)
        return deployment

    def list_tools(self, identity: IdentityContext) -> tuple[ToolVersion, ...]:
        return self.repository.list_tools(identity)

    def list_skills(self, identity: IdentityContext) -> tuple[SkillVersion, ...]:
        return self.repository.list_skills(identity)

    def list_employees(self, identity: IdentityContext) -> tuple[DigitalEmployee, ...]:
        return self.repository.list_employees(identity)

    def list_employee_versions(
        self, identity: IdentityContext, employee_id: str
    ) -> tuple[DigitalEmployeeVersion, ...]:
        self.repository.get_employee(identity, employee_id)
        return self.repository.list_employee_versions(identity, employee_id)

    def rollback(self, identity: IdentityContext, deployment_id: str, employee_id: str, version: int) -> Deployment:
        current = self.repository.get_deployment(identity, deployment_id)
        target = self.repository.get_employee_version(identity, employee_id, version)
        if target.status is not LifecycleStatus.PUBLISHED:
            raise DomainError("DEPENDENCY_VALIDATION_ERROR", "回滚目标必须是已发布版本")
        updated = current.rollback(target)
        self.repository.save_deployment(identity, updated)
        return updated

    def resolve_deployment(self, identity: IdentityContext, deployment_id: str) -> Deployment:
        return self.repository.get_deployment(identity, deployment_id)

    def list_deployments(self, identity: IdentityContext) -> tuple[Deployment, ...]:
        return self.repository.list_deployments(identity)

    @staticmethod
    def _latest_version(getter: Callable, identity: IdentityContext, resource_id: str, *, default: int) -> int:
        try:
            return int(getter(identity, resource_id).version)
        except DomainError as error:
            if error.code == "RESOURCE_NOT_FOUND":
                return default
            raise
