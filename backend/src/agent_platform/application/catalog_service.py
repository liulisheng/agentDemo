"""数字员工目录的内存仓储和发布、部署应用服务。"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
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


def _key(identity: IdentityContext, resource_id: str) -> tuple[str, str, str]:
    return identity.platform_id, identity.tenant_id, resource_id


class InMemoryCatalogRepository:
    """进程内目录实现；所有索引包含平台和租户，避免裸 ID 越权。"""

    def __init__(self) -> None:
        self.tools: dict[tuple[str, str, str], ToolVersion] = {}
        self.skills: dict[tuple[str, str, str], SkillVersion] = {}
        self.employees: dict[tuple[str, str, str], DigitalEmployee] = {}
        self.employee_versions: dict[tuple[str, str, str, int], DigitalEmployeeVersion] = {}
        self.profiles: dict[tuple[str, str, str], ExecutionProfile] = {}
        self.deployments: dict[tuple[str, str, str], Deployment] = {}

    def _get(self, values: Mapping, key: tuple, resource: str):
        try:
            return values[key]
        except KeyError as error:
            raise DomainError("RESOURCE_NOT_FOUND", "资源不存在", {"resource": resource}) from error

    def get_tool(self, identity: IdentityContext, tool_id: str) -> ToolVersion:
        return self._get(self.tools, _key(identity, tool_id), "tool")

    def get_skill(self, identity: IdentityContext, skill_id: str) -> SkillVersion:
        return self._get(self.skills, _key(identity, skill_id), "skill")

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

    def get_profile(self, identity: IdentityContext, profile_id: str) -> ExecutionProfile:
        return self._get(self.profiles, _key(identity, profile_id), "execution_profile")

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

    def save_skill(self, identity: IdentityContext, value: SkillVersion) -> None:
        self.skills[_key(identity, value.skill_id)] = value

    def save_employee(self, identity: IdentityContext, value: DigitalEmployee) -> None:
        self.employees[_key(identity, value.employee_id)] = value

    def save_version(self, identity: IdentityContext, value: DigitalEmployeeVersion) -> None:
        self.employee_versions[(*_key(identity, value.employee_id), value.version)] = value

    def save_profile(self, identity: IdentityContext, value: ExecutionProfile) -> None:
        self.profiles[_key(identity, value.profile_id)] = value

    def save_deployment(self, identity: IdentityContext, value: Deployment) -> None:
        self.deployments[_key(identity, value.deployment_id)] = value


class CatalogService:
    """执行目录写入、依赖交集和已发布版本部署。"""

    def __init__(
        self,
        repository: InMemoryCatalogRepository,
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
        value = SkillVersion(
            skill_id=self._new_id("skill"),
            tenant_id=identity.tenant_id,
            name=name,
            skill_type=skill_type,
            instruction=instruction,
            allowed_tool_ids=tuple(allowed_tool_ids),
        )
        self.repository.save_skill(identity, value)
        return value

    def publish_skill(self, identity: IdentityContext, skill_id: str) -> SkillVersion:
        current = self.repository.get_skill(identity, skill_id)
        for tool_id in current.allowed_tool_ids:
            tool = self.repository.get_tool(identity, tool_id)
            if tool.status is not LifecycleStatus.PUBLISHED:
                raise DomainError(
                    "DEPENDENCY_VALIDATION_ERROR",
                    "Skill 引用的 Tool 尚未发布",
                    {"skill_id": skill_id, "tool_id": tool_id},
                )
        published = current.publish()
        self.repository.save_skill(identity, published)
        return published

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
    ) -> DigitalEmployeeVersion:
        employee = self.repository.get_employee(identity, employee_id)
        versions = [
            version
            for (platform_id, tenant_id, current_employee_id, _), version in self.repository.employee_versions.items()
            if platform_id == identity.platform_id
            and tenant_id == identity.tenant_id
            and current_employee_id == employee.employee_id
        ]
        next_version = max((version.version for version in versions), default=0) + 1
        self._ensure_profile(identity)
        value = DigitalEmployeeVersion(
            employee_id=employee.employee_id,
            tenant_id=identity.tenant_id,
            version=next_version,
            system_prompt=system_prompt,
            skill_version_ids=tuple(skill_version_ids),
            tool_version_ids=tuple(tool_version_ids),
            execution_profile_id=execution_profile_id,
        )
        self.repository.save_version(identity, value)
        return value

    def publish_employee_version(
        self, identity: IdentityContext, employee_id: str, version: int
    ) -> DigitalEmployeeVersion:
        current = self.repository.get_version(identity, employee_id, version)
        missing: list[dict[str, str]] = []
        for skill_id in current.skill_version_ids:
            try:
                skill = self.repository.get_skill(identity, skill_id)
            except DomainError:
                missing.append({"kind": "skill", "id": skill_id})
            else:
                if skill.status is not LifecycleStatus.PUBLISHED:
                    missing.append({"kind": "skill", "id": skill_id})
        for tool_id in current.tool_version_ids:
            try:
                tool = self.repository.get_tool(identity, tool_id)
            except DomainError:
                missing.append({"kind": "tool", "id": tool_id})
            else:
                if tool.status is not LifecycleStatus.PUBLISHED:
                    missing.append({"kind": "tool", "id": tool_id})
        try:
            profile = self.repository.get_profile(identity, current.execution_profile_id)
        except DomainError:
            missing.append({"kind": "execution_profile", "id": current.execution_profile_id})
        else:
            if profile.status is not LifecycleStatus.PUBLISHED:
                missing.append({"kind": "execution_profile", "id": current.execution_profile_id})
        if missing:
            raise DomainError(
                "DEPENDENCY_VALIDATION_ERROR",
                "数字员工版本依赖未满足发布条件",
                {"missing": missing},
            )
        published = current.publish()
        self.repository.save_version(identity, published)
        return published

    def deploy(self, identity: IdentityContext, employee_id: str, version: int) -> Deployment:
        employee_version = self.repository.get_version(identity, employee_id, version)
        deployment = Deployment(
            deployment_id=self._new_id("deployment"),
            tenant_id=identity.tenant_id,
            employee_id=employee_id,
            employee_version=employee_version,
        )
        self.repository.save_deployment(identity, deployment)
        return deployment

    def resolve_deployment(self, identity: IdentityContext, deployment_id: str) -> Deployment:
        return self.repository.get_deployment(identity, deployment_id)

    def list_deployments(self, identity: IdentityContext) -> tuple[Deployment, ...]:
        return self.repository.list_deployments(identity)
