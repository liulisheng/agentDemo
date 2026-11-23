"""MySQL-backed CatalogService 的租户、发布、部署和回滚契约。"""

from __future__ import annotations

import os
import secrets
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from tests._runtime_config_helpers import configure_in_memory_deployment

mysql = pytest.importorskip("mysql.connector")


@pytest.fixture
def mysql_catalog_service_database():
    if os.environ.get("AGENT_PLATFORM_MYSQL_TEST_ALLOW_CREATE_DATABASE") != "1":
        pytest.skip("set AGENT_PLATFORM_MYSQL_TEST_ALLOW_CREATE_DATABASE=1 for disposable MySQL integration")
    database = f"agent_platform_catalog_svc_{secrets.token_hex(5)}"
    config = {
        "host": os.environ.get("MYSQL_HOST", "127.0.0.1"),
        "port": int(os.environ.get("MYSQL_PORT", "3306")),
        "user": os.environ.get("MYSQL_USER", "root"),
        "password": os.environ.get("MYSQL_PASSWORD", ""),
    }
    connection = mysql.connect(**config)
    cursor = connection.cursor()
    try:
        cursor.execute(f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci")
        connection.commit()
    finally:
        cursor.close()
        connection.close()
    try:
        from agent_platform.infrastructure.migrations import MigrationRunner, PreflightAction

        result = MigrationRunner(
            lambda: mysql.connect(**config, database=database),
            Path(__file__).parents[3] / "docs" / "migrations",
        ).apply()
        assert result.action is PreflightAction.APPLY_MIGRATIONS
        connection = mysql.connect(**config, database=database)
        cursor = connection.cursor()
        try:
            cursor.execute(
                "INSERT INTO platforms (platform_id, code, name, status) VALUES (%s,%s,%s,%s)",
                ("plt-local", "platform-local", "本地平台", "ACTIVE"),
            )
            cursor.execute(
                "INSERT INTO tenants (platform_id, tenant_id, code, name, status) VALUES (%s,%s,%s,%s,%s)",
                ("plt-local", "tenant-a", "tenant-a", "租户 A", "ACTIVE"),
            )
            cursor.execute(
                "INSERT INTO tenants (platform_id, tenant_id, code, name, status) VALUES (%s,%s,%s,%s,%s)",
                ("plt-local", "tenant-b", "tenant-b", "租户 B", "ACTIVE"),
            )
            cursor.execute(
                "INSERT INTO users (platform_id, tenant_id, user_id, external_subject, username, display_name, status) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                ("plt-local", "tenant-a", "admin-local", "subject-admin-local", "admin-local", "本地管理员", "ACTIVE"),
            )
            connection.commit()
        finally:
            cursor.close()
            connection.close()
        yield database, config
    finally:
        connection = mysql.connect(**config)
        cursor = connection.cursor()
        try:
            cursor.execute(f"DROP DATABASE IF EXISTS `{database}`")
            connection.commit()
        finally:
            cursor.close()
            connection.close()


def test_catalog_service_publishes_deploys_rolls_back_and_scopes_tenant(mysql_catalog_service_database) -> None:
    from agent_platform.adapters.identity.fake import FakeIdentityProvider
    from agent_platform.application.catalog_service import CatalogService
    from agent_platform.domain.catalog import LifecycleStatus, SkillType
    from agent_platform.domain.common import DomainError
    from agent_platform.infrastructure.mysql_catalog import MySqlCatalogRepository

    database, config = mysql_catalog_service_database
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    other = FakeIdentityProvider().resolve_identity("admin-other")
    service = CatalogService(MySqlCatalogRepository(lambda: mysql.connect(**config, database=database)))

    temporary_tool = service.create_tool(identity, "temporary", "临时", {"type": "object"})
    updated_tool = service.update_tool(identity, temporary_tool.tool_id, description="更新后的临时 Tool")
    assert updated_tool.description == "更新后的临时 Tool"
    service.delete_tool(identity, temporary_tool.tool_id)
    with pytest.raises(DomainError) as deleted_tool:
        service.repository.get_tool(identity, temporary_tool.tool_id)
    assert deleted_tool.value.code == "RESOURCE_NOT_FOUND"

    temporary_employee = service.create_employee(identity, "临时员工", "测试", "测试资料")
    updated_employee = service.update_employee(identity, temporary_employee.employee_id, name="已更新员工")
    assert updated_employee.name == "已更新员工"
    service.delete_employee(identity, temporary_employee.employee_id)
    with pytest.raises(DomainError) as deleted_employee:
        service.repository.get_employee(identity, temporary_employee.employee_id)
    assert deleted_employee.value.code == "RESOURCE_NOT_FOUND"

    tool = service.create_tool(identity, "echo", "回显文本", {"type": "object"})
    skill = service.create_skill(identity, "回声", SkillType.TOOL_BUNDLE, "调用 echo", [tool.tool_id])
    employee = service.create_employee(identity, "助手", "支持", "本地助手")
    version1 = service.create_employee_version(identity, employee.employee_id, "系统指令 v1", [skill.skill_id], [tool.tool_id])
    with pytest.raises(DomainError) as dependency_error:
        service.publish_employee_version(identity, employee.employee_id, version1.version)
    assert dependency_error.value.code == "DEPENDENCY_VALIDATION_ERROR"

    service.publish_tool(identity, tool.tool_id)
    service.publish_skill(identity, skill.skill_id)
    published1 = service.publish_employee_version(identity, employee.employee_id, version1.version)
    deployment = service.deploy(identity, employee.employee_id, published1.version)
    deployment = configure_in_memory_deployment(service, identity, deployment)
    from agent_platform.adapters.runtime.fake import FakeRuntimeAdapter
    from agent_platform.application.control_plane import ControlPlaneService

    control_plane = ControlPlaneService(service, FakeRuntimeAdapter())
    session = control_plane.create_session(identity, deployment.deployment_id)
    run = control_plane.create_run(identity, session.session_id, "执行 v1", "snapshot-v1")

    version2 = service.create_employee_version(identity, employee.employee_id, "系统指令 v2", [skill.skill_id], [tool.tool_id])
    published2 = service.publish_employee_version(identity, employee.employee_id, version2.version)
    deployment2 = service.deploy(identity, employee.employee_id, published2.version)
    restarted_service = CatalogService(
        MySqlCatalogRepository(lambda: mysql.connect(**config, database=database))
    )
    rolled_back = restarted_service.rollback(
        identity, deployment2.deployment_id, employee.employee_id, published1.version
    )

    assert published2.version == 2
    assert rolled_back.employee_version.version == 1
    assert service.list_employees(identity)[0].employee_id == employee.employee_id
    with pytest.raises(DomainError) as cross_tenant:
        restarted_service.resolve_deployment(other, deployment2.deployment_id)
    assert cross_tenant.value.code == "RESOURCE_NOT_FOUND"
    assert restarted_service.resolve_deployment(identity, deployment2.deployment_id).employee_version.status is LifecycleStatus.PUBLISHED
    assert restarted_service.resolve_deployment(identity, deployment2.deployment_id).employee_version.version == 1
    assert control_plane.get_run(identity, run.run_id).snapshot.employee_version == 1
    audit_page = restarted_service.list_audit_events_page(
        identity,
        resource_type="deployment",
        resource_id=deployment2.deployment_id,
        limit=10,
    )
    assert {event.event_type for event in audit_page.items} == {
        "DEPLOYMENT_CREATED",
        "DEPLOYMENT_ROLLED_BACK",
    }
    assert all(event.tenant_id == identity.tenant_id for event in audit_page.items)


def test_mysql_employee_fork_and_type_specific_bindings_preserve_other_configuration(
    mysql_catalog_service_database,
) -> None:
    from concurrent.futures import ThreadPoolExecutor

    from agent_platform.adapters.identity.fake import FakeIdentityProvider
    from agent_platform.application.catalog_service import CatalogService
    from agent_platform.domain.catalog import SkillType
    from agent_platform.infrastructure.mysql_catalog import MySqlCatalogRepository

    database, config = mysql_catalog_service_database
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    service = CatalogService(MySqlCatalogRepository(lambda: mysql.connect(**config, database=database)))
    first_skill = service.create_skill(identity, "Skill 一", SkillType.INSTRUCTION, "一号指令", ())
    second_skill = service.create_skill(identity, "Skill 二", SkillType.INSTRUCTION, "二号指令", ())
    first_tool = service.create_tool(identity, "Tool 一", "一号工具", {"type": "object"})
    second_tool = service.create_tool(identity, "Tool 二", "二号工具", {"type": "object"})
    for skill in (first_skill, second_skill):
        service.publish_skill(identity, skill.skill_id)
    for tool in (first_tool, second_tool):
        service.publish_tool(identity, tool.tool_id)
    employee = service.create_employee(identity, "定向绑定员工", "助理", "MySQL 部分更新")
    published = service.create_employee_version(
        identity,
        employee.employee_id,
        "保持系统指令",
        [first_skill.skill_id],
        [first_tool.tool_id],
        approval_policy="required",
    )
    published = service.publish_employee_version(identity, employee.employee_id, published.version)

    with ThreadPoolExecutor(max_workers=2) as executor:
        forks = tuple(
            executor.map(
                lambda _: service.fork_employee_version(identity, employee.employee_id, published.version),
                range(2),
            )
        )
    assert sorted(item.version for item in forks) == [2, 3]
    draft = next(item for item in forks if item.version == 2)
    assert draft.version == 2
    assert draft.skill_version_refs == ((first_skill.skill_id, 1),)
    assert draft.tool_version_refs == ((first_tool.tool_id, 1),)
    assert draft.system_prompt == "保持系统指令"
    assert draft.approval_policy is None
    assert {(item.slot, item.module_id, item.module_version) for item in service.repository.list_capability_bindings(
        identity, employee.employee_id, draft.version
    )} == {
        ("skill", first_skill.skill_id, 1),
        ("tool", first_tool.tool_id, 1),
    }

    changed_skills = service.replace_employee_skill_bindings(
        identity, employee.employee_id, draft.version, ((second_skill.skill_id, 1),)
    )
    assert changed_skills.skill_version_refs == ((second_skill.skill_id, 1),)
    assert changed_skills.tool_version_refs == ((first_tool.tool_id, 1),)
    assert changed_skills.system_prompt == "保持系统指令"
    assert changed_skills.approval_policy is None
    assert {(item.slot, item.module_id) for item in service.repository.list_capability_bindings(
        identity, employee.employee_id, draft.version
    )} == {("skill", second_skill.skill_id), ("tool", first_tool.tool_id)}

    changed_tools = service.replace_employee_tool_bindings(
        identity, employee.employee_id, draft.version, ((second_tool.tool_id, 1),)
    )
    assert changed_tools.skill_version_refs == ((second_skill.skill_id, 1),)
    assert changed_tools.tool_version_refs == ((second_tool.tool_id, 1),)
    assert changed_tools.system_prompt == "保持系统指令"
    assert service.repository.get_employee_version(identity, employee.employee_id, 1) == published


def test_mysql_lifecycle_transitions_rollback_with_audit_and_serialize_concurrent_writes(
    mysql_catalog_service_database,
) -> None:
    from agent_platform.adapters.identity.fake import FakeIdentityProvider
    from agent_platform.application.catalog_service import CatalogService
    from agent_platform.domain.catalog import LifecycleStatus, SkillType
    from agent_platform.domain.common import DomainError
    from agent_platform.infrastructure.mysql_catalog import MySqlCatalogRepository

    database, config = mysql_catalog_service_database
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    other = FakeIdentityProvider().resolve_identity("admin-other")
    service = CatalogService(MySqlCatalogRepository(lambda: mysql.connect(**config, database=database)))
    skill = service.create_skill(identity, "生命周期 Skill", SkillType.INSTRUCTION, "按步骤执行", ())
    service.publish_skill(identity, skill.skill_id)
    employee = service.create_employee(identity, "生命周期员工", "助理", "并发状态转换")
    published1 = service.create_employee_version(identity, employee.employee_id, "版本一", [skill.skill_id], [])
    service.publish_employee_version(identity, employee.employee_id, published1.version)
    published2 = service.create_employee_version(identity, employee.employee_id, "版本二", [skill.skill_id], [])
    service.publish_employee_version(identity, employee.employee_id, published2.version)
    deployment = service.deploy(identity, employee.employee_id, published2.version)
    deployment = configure_in_memory_deployment(service, identity, deployment)

    def create_rejecting_audit_trigger(trigger_name: str) -> None:
        connection = mysql.connect(**config, database=database)
        cursor = connection.cursor()
        try:
            cursor.execute(
                f"CREATE TRIGGER {trigger_name} BEFORE INSERT ON audit_events FOR EACH ROW "
                "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '测试注入审计失败'"
            )
            connection.commit()
        finally:
            cursor.close()
            connection.close()

    def drop_trigger(trigger_name: str) -> None:
        connection = mysql.connect(**config, database=database)
        cursor = connection.cursor()
        try:
            cursor.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")
            connection.commit()
        finally:
            cursor.close()
            connection.close()

    fail_deprecate_audit = "fail_deprecate_audit_for_test"
    create_rejecting_audit_trigger(fail_deprecate_audit)
    try:
        with pytest.raises(mysql.Error):
            service.deprecate_employee_version(identity, employee.employee_id, published1.version)
    finally:
        drop_trigger(fail_deprecate_audit)
    assert service.repository.get_employee_version(
        identity, employee.employee_id, published1.version
    ).status is LifecycleStatus.PUBLISHED
    assert service.list_audit_events_page(
        identity,
        resource_type="digital_employee_version",
        resource_id=employee.employee_id,
        limit=20,
    ).items == ()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(
            executor.map(
                lambda _: service.deprecate_employee_version(identity, employee.employee_id, published1.version),
                range(2),
            )
        )
    assert all(result.status is LifecycleStatus.DEPRECATED for result in results)
    deprecation_events = service.list_audit_events_page(
        identity,
        resource_type="digital_employee_version",
        resource_id=employee.employee_id,
        limit=20,
    ).items
    assert [event.event_type for event in deprecation_events].count("EMPLOYEE_VERSION_DEPRECATED") == 1

    fail_disable_audit = "fail_deployment_disable_audit_for_test"
    create_rejecting_audit_trigger(fail_disable_audit)
    try:
        with pytest.raises(mysql.Error):
            service.disable_deployment(identity, deployment.deployment_id)
    finally:
        drop_trigger(fail_disable_audit)
    assert service.resolve_deployment(identity, deployment.deployment_id).status is LifecycleStatus.ACTIVE
    audit_after_failed_disable = service.list_audit_events_page(
        identity,
        resource_type="deployment",
        resource_id=deployment.deployment_id,
        limit=20,
    ).items
    assert [event.event_type for event in audit_after_failed_disable] == ["DEPLOYMENT_CREATED"]

    def disable_once(_: int) -> str:
        try:
            return service.disable_deployment(identity, deployment.deployment_id).status.value
        except DomainError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        disable_results = tuple(executor.map(disable_once, range(2)))
    assert sorted(disable_results) == ["DISABLED", "INVALID_STATE"]
    assert service.resolve_deployment(identity, deployment.deployment_id).status is LifecycleStatus.DISABLED

    enabled = service.enable_deployment(identity, deployment.deployment_id)
    assert enabled.status is LifecycleStatus.ACTIVE
    assert enabled.employee_version.version == published2.version
    with pytest.raises(DomainError) as cross_tenant:
        service.disable_deployment(other, deployment.deployment_id)
    assert cross_tenant.value.code == "RESOURCE_NOT_FOUND"

    deployment_events = service.list_audit_events_page(
        identity,
        resource_type="deployment",
        resource_id=deployment.deployment_id,
        limit=20,
    ).items
    assert {event.event_type for event in deployment_events} == {
        "DEPLOYMENT_CREATED",
        "DEPLOYMENT_DISABLED",
        "DEPLOYMENT_ENABLED",
    }
