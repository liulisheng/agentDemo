"""将已发布目录资源解析为一次 Run 使用的不可变快照。"""

from __future__ import annotations

from agent_platform.application.catalog_service import CatalogService
from agent_platform.domain.catalog import (
    Deployment,
    DigitalEmployeeVersion,
    LifecycleStatus,
    SkillVersion,
)
from agent_platform.domain.common import DomainError
from agent_platform.domain.identity import IdentityContext
from agent_platform.domain.observability import RunSnapshot


class SkillResolver:
    """按员工版本固化的顺序解析 Skill，禁止运行时读取草稿。"""

    def __init__(self, catalog: CatalogService) -> None:
        self.catalog = catalog

    def resolve(
        self, identity: IdentityContext, employee_version: DigitalEmployeeVersion
    ) -> tuple[SkillVersion, ...]:
        resolved: list[SkillVersion] = []
        for skill_id in employee_version.skill_version_ids:
            skill = self.catalog.repository.get_skill(identity, skill_id)
            if skill.status is not LifecycleStatus.PUBLISHED:
                raise DomainError("DEPENDENCY_VALIDATION_ERROR", "运行只能使用已发布 Skill")
            resolved.append(skill)
        return tuple(resolved)


class AgentResolver:
    """把 Deployment 固定的员工版本复制成 RunSnapshot。"""

    def __init__(self, catalog: CatalogService) -> None:
        self.catalog = catalog
        self.skills = SkillResolver(catalog)

    def snapshot(self, identity: IdentityContext, deployment: Deployment) -> RunSnapshot:
        version = deployment.employee_version
        self.skills.resolve(identity, version)
        profile = self.catalog.repository.get_profile_version(
            identity, version.execution_profile_id, version.execution_profile_version
        )
        return RunSnapshot(
            employee_id=version.employee_id,
            employee_version=version.version,
            skill_version_ids=tuple(version.skill_version_ids),
            tool_version_ids=tuple(version.tool_version_ids),
            execution_profile_id=version.execution_profile_id,
            skill_version_refs=tuple(version.skill_version_refs),
            tool_version_refs=tuple(version.tool_version_refs),
            execution_profile_version=version.execution_profile_version,
            model_route_id=version.model_route_id or profile.model_route_id,
        )
