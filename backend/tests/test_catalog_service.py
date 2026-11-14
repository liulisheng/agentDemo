import pytest

from agent_platform.adapters.identity.fake import FakeIdentityProvider
from agent_platform.application.catalog_service import CatalogService, InMemoryCatalogRepository
from agent_platform.domain.catalog import LifecycleStatus, SkillType
from agent_platform.domain.common import DomainError


def _service() -> tuple[CatalogService, object]:
    repo = InMemoryCatalogRepository()
    service = CatalogService(repo, id_factory=lambda prefix: f"{prefix}-1")
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    return service, identity


def test_publish_requires_same_tenant_published_dependencies() -> None:
    service, identity = _service()
    tool = service.create_tool(identity, "echo", "回显", {"type": "object"})
    skill = service.create_skill(
        identity,
        "回声技能",
        SkillType.TOOL_BUNDLE,
        "调用回声工具",
        [tool.tool_id],
    )
    employee = service.create_employee(identity, "行政助手", "行政", "处理事务")
    version = service.create_employee_version(
        identity,
        employee.employee_id,
        "你是行政助手",
        [skill.skill_id],
        [tool.tool_id],
    )

    with pytest.raises(DomainError) as error:
        service.publish_employee_version(identity, employee.employee_id, version.version)
    assert error.value.code == "DEPENDENCY_VALIDATION_ERROR"

    service.publish_tool(identity, tool.tool_id)
    service.publish_skill(identity, skill.skill_id)
    published = service.publish_employee_version(identity, employee.employee_id, version.version)
    assert published.status is LifecycleStatus.PUBLISHED


def test_cross_tenant_deployment_is_hidden() -> None:
    service, identity = _service()
    other = FakeIdentityProvider().resolve_identity("admin-other")
    tool = service.create_tool(identity, "echo", "回显", {"type": "object"})
    service.publish_tool(identity, tool.tool_id)
    skill = service.create_skill(identity, "回声", SkillType.INSTRUCTION, "回答", [])
    service.publish_skill(identity, skill.skill_id)
    employee = service.create_employee(identity, "助手", "支持", "支持")
    version = service.create_employee_version(
        identity, employee.employee_id, "prompt", [skill.skill_id], [tool.tool_id]
    )
    service.publish_employee_version(identity, employee.employee_id, version.version)
    deployment = service.deploy(identity, employee.employee_id, version.version)

    with pytest.raises(DomainError) as error:
        service.resolve_deployment(other, deployment.deployment_id)
    assert error.value.code == "RESOURCE_NOT_FOUND"


def test_publish_validates_model_route_workspace_and_sandbox_references() -> None:
    service, identity = _service()
    repo = service.repository
    repo.model_routes[(identity.platform_id, identity.tenant_id, "route-1")] = object()
    repo.workspace_profiles[(identity.platform_id, identity.tenant_id, "workspace-1")] = object()
    repo.sandbox_profiles[(identity.platform_id, identity.tenant_id, "sandbox-1")] = object()
    tool = service.create_tool(identity, "echo", "回显", {"type": "object"})
    service.publish_tool(identity, tool.tool_id)
    skill = service.create_skill(identity, "技能", SkillType.INSTRUCTION, "回答", [])
    service.publish_skill(identity, skill.skill_id)
    employee = service.create_employee(identity, "助手", "支持", "支持")
    version = service.create_employee_version(
        identity,
        employee.employee_id,
        "prompt",
        [skill.skill_id],
        [],
        model_route_id="route-1",
        workspace_profile_id="workspace-1",
        sandbox_profile_id="sandbox-1",
    )
    assert service.publish_employee_version(identity, employee.employee_id, version.version).status is LifecycleStatus.PUBLISHED
