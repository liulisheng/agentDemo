import pytest

from agent_platform.adapters.identity.fake import FakeIdentityProvider
from agent_platform.application.catalog_service import CatalogService, InMemoryCatalogRepository
from agent_platform.domain.catalog import LifecycleStatus, SkillType
from agent_platform.domain.common import DomainError
from agent_platform.domain.model_routes import ModelProfile, ModelRoute
from agent_platform.ports.workspaces import SandboxProfile, WorkspaceProfile


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


def test_copied_tool_requires_recipient_secret_before_publish() -> None:
    service, identity = _service()
    tool = service.create_tool(
        identity,
        "复制的工单工具",
        "需要接收租户绑定自己的凭据",
        {"type": "object"},
        requires_recipient_credentials=True,
    )

    with pytest.raises(DomainError) as missing_credential:
        service.publish_tool(identity, tool.tool_id)
    assert missing_credential.value.code == "DEPENDENCY_VALIDATION_ERROR"

    configured = service.update_tool(
        identity, tool.tool_id, auth_secret_ref="secret/tenant-b/ticket-api"
    )
    assert configured.auth_secret_ref == "secret/tenant-b/ticket-api"
    published = service.publish_tool(identity, tool.tool_id)
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
    repo.save_model_route(
        identity,
        ModelRoute("route-1", identity.tenant_id, "route", ModelProfile("model-1", identity.tenant_id, "fake", "fake", "fake://model")),
    )
    repo.save_workspace_profile(identity, WorkspaceProfile("workspace-1"))
    repo.save_sandbox_profile(identity, SandboxProfile("sandbox-1"))
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


def test_publish_rejects_a_disabled_model_route() -> None:
    service, identity = _service()
    repository = service.repository
    route = ModelRoute(
        "route-disabled",
        identity.tenant_id,
        "disabled-route",
        ModelProfile("model-disabled", identity.tenant_id, "model-a", "fake", "fake://model"),
    )
    repository.save_model_route(identity, route)
    route = repository.get_model_route(identity, route.route_id)
    profile = service.create_profile(
        identity, "fake", "model-a", model_route_id=route.route_id
    )
    skill = service.create_skill(identity, "校验 Skill", SkillType.INSTRUCTION, "回答", [])
    service.publish_skill(identity, skill.skill_id)
    employee = service.create_employee(identity, "绑定模型助手", "助手", "模型路由发布校验")
    version = service.create_employee_version(
        identity,
        employee.employee_id,
        "回答问题",
        [skill.skill_id],
        [],
        execution_profile_id=profile.profile_id,
        model_route_id=route.route_id,
    )
    repository.save_model_route(
        identity,
        ModelRoute(
            route.route_id,
            route.tenant_id,
            route.alias,
            route.profile,
            status="DISABLED",
            route_version=route.route_version + 1,
        ),
    )

    with pytest.raises(DomainError) as disabled:
        service.publish_employee_version(identity, employee.employee_id, version.version)

    assert disabled.value.code == "DEPENDENCY_VALIDATION_ERROR"
    assert any(
        missing.get("kind") == "model_route"
        and missing.get("reason") == "ROUTE_DISABLED"
        for missing in disabled.value.details["missing"]
    )


def test_publish_rejects_a_missing_model_route() -> None:
    service, identity = _service()
    profile = service.create_profile(identity, "fake", "model-a")
    skill = service.create_skill(identity, "缺失路由 Skill", SkillType.INSTRUCTION, "回答", [])
    service.publish_skill(identity, skill.skill_id)
    employee = service.create_employee(identity, "缺失模型助手", "助手", "模型路由缺失校验")
    version = service.create_employee_version(
        identity,
        employee.employee_id,
        "回答问题",
        [skill.skill_id],
        [],
        execution_profile_id=profile.profile_id,
        model_route_id="missing-route",
    )

    with pytest.raises(DomainError) as missing:
        service.publish_employee_version(identity, employee.employee_id, version.version)

    assert missing.value.code == "DEPENDENCY_VALIDATION_ERROR"
    assert any(item["id"] == "missing-route" for item in missing.value.details["missing"])
