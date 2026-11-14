import pytest

from agent_platform.adapters.identity.fake import FakeIdentityProvider
from agent_platform.application.catalog_service import CatalogService, InMemoryCatalogRepository
from agent_platform.application.policy import PolicyResolver
from agent_platform.application.resolvers import AgentResolver, SkillResolver
from agent_platform.domain.catalog import ExecutionProfile, LifecycleStatus, SkillType
from agent_platform.domain.common import DomainError


def test_policy_resolver_precedence_and_provenance() -> None:
    resolver = PolicyResolver()
    resolver.bind("system", "default", "profile-system")
    resolver.bind("platform", "plt-local", "profile-platform")
    resolver.bind("tenant", "tenant-a", "profile-tenant")
    resolver.bind("user", "admin-local", "profile-user")
    identity = FakeIdentityProvider().resolve_identity("admin-local")

    resolved = resolver.resolve(identity)

    assert resolved.profile_id == "profile-user"
    assert resolved.resolved_by_scope == "user"


def test_skill_and_agent_resolvers_freeze_snapshot() -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    repo = InMemoryCatalogRepository()
    catalog = CatalogService(repo)
    tool = catalog.create_tool(identity, "echo", "回显", {"type": "object"})
    catalog.publish_tool(identity, tool.tool_id)
    skill = catalog.create_skill(identity, "分析", SkillType.INSTRUCTION, "分析输入", [])
    catalog.publish_skill(identity, skill.skill_id)
    employee = catalog.create_employee(identity, "助手", "支持", "支持")
    version = catalog.create_employee_version(identity, employee.employee_id, "prompt", [skill.skill_id], [])
    catalog.publish_employee_version(identity, employee.employee_id, version.version)
    deployment = catalog.deploy(identity, employee.employee_id, version.version)

    skills = SkillResolver(catalog).resolve(identity, deployment.employee_version)
    snapshot = AgentResolver(catalog).snapshot(identity, deployment)

    assert [item.skill_id for item in skills] == [skill.skill_id]
    assert snapshot.employee_version == version.version
    assert snapshot.skill_version_ids == (skill.skill_id,)


def test_execution_profile_is_immutable_after_publish() -> None:
    profile = ExecutionProfile("p-1", "tenant-a", "fake", "fake-model").publish()
    assert profile.status is LifecycleStatus.PUBLISHED
    with pytest.raises(DomainError) as error:
        profile.publish()
    assert error.value.code == "IMMUTABLE_VERSION"
