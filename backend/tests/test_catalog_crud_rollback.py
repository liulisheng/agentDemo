from agent_platform.adapters.identity.fake import FakeIdentityProvider
from agent_platform.application.catalog_service import CatalogService, InMemoryCatalogRepository
from agent_platform.domain.catalog import SkillType
from agent_platform.domain.common import DomainError


def test_catalog_lists_resources_and_rolls_back_deployment() -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    service = CatalogService(InMemoryCatalogRepository())
    tool = service.create_tool(identity, "echo", "回显", {"type": "object"})
    service.publish_tool(identity, tool.tool_id)
    skill = service.create_skill(identity, "技能", SkillType.INSTRUCTION, "回答", [])
    service.publish_skill(identity, skill.skill_id)
    employee = service.create_employee(identity, "助手", "支持", "支持")
    first = service.create_employee_version(identity, employee.employee_id, "v1", [skill.skill_id], [])
    service.publish_employee_version(identity, employee.employee_id, first.version)
    deployment = service.deploy(identity, employee.employee_id, first.version)
    second = service.create_employee_version(identity, employee.employee_id, "v2", [skill.skill_id], [])
    service.publish_employee_version(identity, employee.employee_id, second.version)
    rolled = service.rollback(identity, deployment.deployment_id, employee.employee_id, first.version)
    assert rolled.employee_version.version == 1
    assert service.list_tools(identity)
    assert service.list_skills(identity)
    assert service.list_employees(identity)


def test_catalog_draft_crud_never_mutates_published_version() -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    service = CatalogService(InMemoryCatalogRepository())
    tool = service.create_tool(identity, "echo", "回显", {"type": "object"})
    updated = service.update_tool(identity, tool.tool_id, description="新描述")
    assert updated.description == "新描述"
    service.delete_tool(identity, updated.tool_id)
    try:
        service.repository.get_tool(identity, updated.tool_id)
    except DomainError as error:
        assert error.code == "RESOURCE_NOT_FOUND"
    else:
        raise AssertionError("draft tool should be deletable")


def test_rollback_does_not_change_existing_run_snapshot() -> None:
    from agent_platform.adapters.runtime.fake import FakeRuntimeAdapter
    from agent_platform.application.control_plane import ControlPlaneService

    identity = FakeIdentityProvider().resolve_identity("admin-local")
    service = CatalogService(InMemoryCatalogRepository())
    skill = service.create_skill(identity, "技能", SkillType.INSTRUCTION, "回答", [])
    service.publish_skill(identity, skill.skill_id)
    employee = service.create_employee(identity, "助手", "支持", "支持")
    service.create_employee_version(identity, employee.employee_id, "v1", [skill.skill_id], [])
    service.publish_employee_version(identity, employee.employee_id, 1)
    deployment = service.deploy(identity, employee.employee_id, 1)
    cp = ControlPlaneService(service, FakeRuntimeAdapter())
    session = cp.create_session(identity, deployment.deployment_id)
    run = cp.create_run(identity, session.session_id, "before", "snap-1")
    version2 = service.create_employee_version(identity, employee.employee_id, "v2", [skill.skill_id], [])
    service.publish_employee_version(identity, employee.employee_id, version2.version)
    service.rollback(identity, deployment.deployment_id, employee.employee_id, 1)
    assert cp.get_run(identity, run.run_id).snapshot.employee_version == 1
