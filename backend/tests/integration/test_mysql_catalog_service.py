"""MySQL-backed CatalogService 的租户、发布、部署和回滚契约。"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

import pytest

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
