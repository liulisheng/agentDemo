"""Resource Center 发布服務的來源版本/依赖快照租户集成契约。"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

import pytest

from tests._runtime_config_helpers import configure_in_memory_deployment

mysql = pytest.importorskip("mysql.connector")


@pytest.fixture
def mysql_resource_center_service_database():
    if os.environ.get("AGENT_PLATFORM_MYSQL_TEST_ALLOW_CREATE_DATABASE") != "1":
        pytest.skip("set AGENT_PLATFORM_MYSQL_TEST_ALLOW_CREATE_DATABASE=1 for disposable MySQL integration")
    database = f"agent_platform_hub_service_{secrets.token_hex(5)}"
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
        from agent_platform.infrastructure.migrations import MigrationRunner

        MigrationRunner(
            lambda: mysql.connect(**config, database=database),
            Path(__file__).parents[3] / "docs" / "migrations",
        ).apply()
        connection = mysql.connect(**config, database=database)
        cursor = connection.cursor()
        try:
            cursor.execute("INSERT INTO platforms (platform_id,code,name,status) VALUES ('plt-local','local','本地平台','ACTIVE')")
            cursor.execute("INSERT INTO tenants (platform_id,tenant_id,code,name,status) VALUES ('plt-local','tenant-a','tenant-a','发布方租户','ACTIVE')")
            cursor.execute("INSERT INTO tenants (platform_id,tenant_id,code,name,status) VALUES ('plt-local','tenant-b','tenant-b','其他租户','ACTIVE')")
            cursor.execute("INSERT INTO users (platform_id,tenant_id,user_id,external_subject,username,display_name,status) VALUES ('plt-local','tenant-a','admin-local','subject-a','admin-local','发布管理员','ACTIVE')")
            cursor.execute("INSERT INTO users (platform_id,tenant_id,user_id,external_subject,username,display_name,status) VALUES ('plt-local','tenant-b','admin-other','subject-b','admin-other','其他管理员','ACTIVE')")
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


def test_publish_local_resources_to_hub_with_exact_dependency_versions(
    mysql_resource_center_service_database,
) -> None:
    from agent_platform.adapters.identity.fake import FakeIdentityProvider
    from agent_platform.application.catalog_service import CatalogService
    from agent_platform.application.resource_center import ResourceCenterService
    from agent_platform.domain.catalog import LifecycleStatus, SkillType
    from agent_platform.domain.common import DomainError, generate_database_id
    from agent_platform.domain.resource_center import (
        HubGrant,
        HubImportMode,
        HubResourceType,
        build_hub_manifest,
    )
    from agent_platform.infrastructure.mysql_catalog import MySqlCatalogRepository
    from agent_platform.infrastructure.mysql_resource_center import MySqlResourceCenterRepository

    database, config = mysql_resource_center_service_database
    connection_factory = lambda: mysql.connect(**config, database=database)
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    other = FakeIdentityProvider().resolve_identity("admin-other")
    catalog = CatalogService(MySqlCatalogRepository(connection_factory))
    resource_center = ResourceCenterService(
        catalog, MySqlResourceCenterRepository(connection_factory)
    )

    draft_tool = catalog.create_tool(identity, "查询工单", "读取工单", {"type": "object"})
    with pytest.raises(DomainError) as draft_error:
        resource_center.create_package(
            identity, HubResourceType.TOOL, draft_tool.tool_id, draft_tool.version
        )
    assert draft_error.value.code == "DEPENDENCY_VALIDATION_ERROR"

    tool = catalog.publish_tool(identity, draft_tool.tool_id)
    connection = mysql.connect(**config, database=database)
    cursor = connection.cursor()
    try:
        cursor.execute(
            "UPDATE tool_versions SET auth_secret_ref=%s WHERE platform_id=%s AND tenant_id=%s "
            "AND tool_id=%s AND version=%s",
            ("tenant-a/secret/internal-api", identity.platform_id, identity.tenant_id, tool.tool_id, tool.version),
        )
        connection.commit()
    finally:
        cursor.close()
        connection.close()
    tool_hub, tool_hub_version = resource_center.create_package(
        identity, HubResourceType.TOOL, tool.tool_id, tool.version
    )
    assert "tenant-a/secret/internal-api" not in tool_hub_version.manifest.manifest_json
    assert tool_hub_version.manifest.data["requires_recipient_credentials"] is True
    resource_center.publish_version(identity, tool_hub.hub_resource_id, tool_hub_version.version)
    from dataclasses import replace

    tampered_version = replace(
        tool_hub_version,
        manifest=build_hub_manifest(
            resource_type="TOOL",
            name=tool_hub.name,
            version=tool_hub_version.version,
            content={"description": "试图修改已发布 Hub 包"},
        ),
    )
    with pytest.raises(mysql.IntegrityError):
        resource_center.repository.save_version(identity, tampered_version)
    assert resource_center.repository.get_authorized_version(
        identity, tool_hub.hub_resource_id, tool_hub_version.version, "COPY"
    ).manifest.sha256 == tool_hub_version.manifest.sha256

    skill = catalog.create_skill(
        identity, "工单处理", SkillType.TOOL_BUNDLE, "先查询工单", [tool.tool_id]
    )
    catalog.publish_skill(identity, skill.skill_id)
    skill_hub, skill_hub_version = resource_center.create_package(
        identity, HubResourceType.SKILL, skill.skill_id, skill.version
    )
    assert skill_hub_version.manifest.data["dependencies"] == [
        {
            "sequence_no": 1,
            "type": "TOOL",
            "resource_id": tool_hub.hub_resource_id,
            "version": tool_hub_version.version,
            "required": True,
        }
    ]
    resource_center.publish_version(identity, skill_hub.hub_resource_id, skill_hub_version.version)

    employee = catalog.create_employee(identity, "工单助手", "客服", "处理客户工单")
    employee_version = catalog.create_employee_version(
        identity, employee.employee_id, "安全处理工单", [skill.skill_id], [tool.tool_id]
    )
    catalog.publish_employee_version(identity, employee.employee_id, employee_version.version)
    employee_hub, employee_hub_version = resource_center.create_package(
        identity,
        HubResourceType.DIGITAL_EMPLOYEE,
        employee.employee_id,
        employee_version.version,
    )
    assert [item["resource_id"] for item in employee_hub_version.manifest.data["dependencies"]] == [
        skill_hub.hub_resource_id,
        tool_hub.hub_resource_id,
    ]
    resource_center.publish_version(identity, employee_hub.hub_resource_id, employee_hub_version.version)

    grants_by_resource = {}
    for hub_resource, hub_version in (
        (tool_hub, tool_hub_version),
        (skill_hub, skill_hub_version),
        (employee_hub, employee_hub_version),
    ):
        grant = HubGrant(
            generate_database_id(),
            identity.platform_id,
            hub_resource.hub_resource_id,
            hub_version.version,
            identity.tenant_id,
            other.tenant_id,
            copy_allowed=True,
            reference_allowed=True,
            created_by=identity.user_id,
        )
        resource_center.repository.save_grant(identity, grant)
        grants_by_resource[hub_resource.hub_resource_id] = grant

    recipient_catalog = CatalogService(MySqlCatalogRepository(connection_factory))
    recipient_resource_center = ResourceCenterService(
        recipient_catalog, resource_center.repository
    )
    from fastapi.testclient import TestClient

    from agent_platform.api.app import create_app

    recipient_client = TestClient(
        create_app(
            mode="fake",
            catalog_repository=recipient_catalog.repository,
            resource_center_repository=resource_center.repository,
        )
    )
    import_response = recipient_client.post(
        "/api/v1/hub/imports",
        headers={"X-Fake-User": other.user_id},
        json={
            "hub_resource_id": employee_hub.hub_resource_id,
            "hub_version": employee_hub_version.version,
            "mode": "COPY",
        },
    )
    assert import_response.status_code == 201
    imported_employee = recipient_resource_center.repository.get_import(
        other,
        employee_hub.hub_resource_id,
        employee_hub_version.version,
        HubImportMode.COPY,
    )
    assert imported_employee is not None
    assert imported_employee.local_resource_id != employee.employee_id
    assert imported_employee.status.value == "ACTIVE"
    repeated_import = recipient_resource_center.import_resource(
        other,
        employee_hub.hub_resource_id,
        employee_hub_version.version,
        HubImportMode.COPY,
    )
    assert repeated_import.import_id == imported_employee.import_id
    copied_employee = recipient_catalog.repository.get_employee(
        other, imported_employee.local_resource_id
    )
    copied_version = recipient_catalog.repository.get_employee_version(
        other, copied_employee.employee_id, imported_employee.local_resource_version
    )
    assert copied_version.system_prompt == employee_version.system_prompt
    assert copied_version.status.value == "DRAFT"
    copied_tool_import = recipient_resource_center.repository.get_import(
        other, tool_hub.hub_resource_id, tool_hub_version.version, HubImportMode.COPY
    )
    copied_tool = recipient_catalog.repository.get_tool_version(
        other, copied_tool_import.local_resource_id, copied_tool_import.local_resource_version
    )
    assert copied_tool.requires_recipient_credentials is True
    assert copied_tool.auth_secret_ref is None
    with pytest.raises(DomainError) as secret_required:
        recipient_catalog.publish_tool(other, copied_tool.tool_id)
    assert secret_required.value.code == "DEPENDENCY_VALIDATION_ERROR"
    recipient_catalog.update_tool(
        other, copied_tool.tool_id, auth_secret_ref="secret/tenant-b/ticket-api"
    )
    recipient_catalog.publish_tool(other, copied_tool.tool_id)

    referenced_employee = recipient_resource_center.import_resource(
        other,
        employee_hub.hub_resource_id,
        employee_hub_version.version,
        HubImportMode.REFERENCE,
    )
    assert referenced_employee.local_resource_id != employee.employee_id
    referenced_employee_version = recipient_catalog.repository.get_employee_version(
        other,
        referenced_employee.local_resource_id,
        referenced_employee.local_resource_version,
    )
    assert referenced_employee_version.status.value == "PUBLISHED"
    referenced_skill = recipient_resource_center.repository.get_import(
        other, skill_hub.hub_resource_id, skill_hub_version.version, HubImportMode.REFERENCE
    )
    referenced_tool = recipient_resource_center.repository.get_import(
        other, tool_hub.hub_resource_id, tool_hub_version.version, HubImportMode.REFERENCE
    )
    with pytest.raises(DomainError) as reference_skill_read_only:
        recipient_catalog.update_skill(
            other, referenced_skill.local_resource_id, instruction="尝试编辑引用 Skill"
        )
    assert reference_skill_read_only.value.code == "IMMUTABLE_VERSION"
    with pytest.raises(DomainError) as reference_tool_read_only:
        recipient_catalog.update_tool(
            other, referenced_tool.local_resource_id, description="尝试编辑引用 Tool"
        )
    assert reference_tool_read_only.value.code == "IMMUTABLE_VERSION"

    from agent_platform.adapters.runtime.fake import FakeRuntimeAdapter
    from agent_platform.application.control_plane import ControlPlaneService
    from agent_platform.infrastructure.mysql_execution import MySqlExecutionRepository

    reference_deployment = recipient_catalog.deploy(
        other,
        referenced_employee.local_resource_id,
        referenced_employee.local_resource_version,
    )
    reference_deployment = configure_in_memory_deployment(
        recipient_catalog, other, reference_deployment
    )
    reference_control_plane = ControlPlaneService(recipient_catalog, FakeRuntimeAdapter())
    reference_session = reference_control_plane.create_session(
        other, reference_deployment.deployment_id
    )
    with pytest.raises(DomainError) as unresolved_reference:
        reference_control_plane.create_run(
            other, reference_session.session_id, "请执行共享员工", "reference-run-denied"
        )
    assert unresolved_reference.value.code == "DEPENDENCY_VALIDATION_ERROR"

    # 通过独立的无凭据资源验证 REFERENCE 的解析内容进入 Runtime 与 RunSnapshot。
    shared_tool = catalog.create_tool(identity, "echo", "无副作用回显", {"type": "object"})
    catalog.publish_tool(identity, shared_tool.tool_id)
    shared_tool_hub, shared_tool_hub_v1 = resource_center.create_package(
        identity, HubResourceType.TOOL, shared_tool.tool_id, shared_tool.version
    )
    resource_center.publish_version(identity, shared_tool_hub.hub_resource_id, 1)
    shared_skill = catalog.create_skill(
        identity, "只读共享技能", SkillType.TOOL_BUNDLE, "共享技能指令 v1", [shared_tool.tool_id]
    )
    catalog.publish_skill(identity, shared_skill.skill_id)
    shared_skill_hub, shared_skill_hub_v1 = resource_center.create_package(
        identity, HubResourceType.SKILL, shared_skill.skill_id, shared_skill.version
    )
    resource_center.publish_version(identity, shared_skill_hub.hub_resource_id, 1)
    shared_employee = catalog.create_employee(identity, "共享运行员工", "助手", "共享运行员工")
    shared_employee_v1 = catalog.create_employee_version(
        identity,
        shared_employee.employee_id,
        "共享员工系统指令 v1",
        [shared_skill.skill_id],
        [shared_tool.tool_id],
    )
    catalog.publish_employee_version(identity, shared_employee.employee_id, shared_employee_v1.version)
    shared_employee_hub, shared_employee_hub_v1 = resource_center.create_package(
        identity,
        HubResourceType.DIGITAL_EMPLOYEE,
        shared_employee.employee_id,
        shared_employee_v1.version,
    )
    resource_center.publish_version(identity, shared_employee_hub.hub_resource_id, 1)
    shared_grants = {}
    for hub_resource, hub_version in (
        (shared_tool_hub, shared_tool_hub_v1),
        (shared_skill_hub, shared_skill_hub_v1),
        (shared_employee_hub, shared_employee_hub_v1),
    ):
        grant = HubGrant(
            generate_database_id(),
            identity.platform_id,
            hub_resource.hub_resource_id,
            hub_version.version,
            identity.tenant_id,
            other.tenant_id,
            copy_allowed=True,
            reference_allowed=True,
            created_by=identity.user_id,
        )
        resource_center.repository.save_grant(identity, grant)
        shared_grants[hub_resource.hub_resource_id] = grant
    class CapturingRuntime(FakeRuntimeAdapter):
        last_request = None

        def run(self, request):
            self.last_request = request
            return super().run(request)

    referenced_runtime = CapturingRuntime()
    runtime_employee = recipient_resource_center.import_resource(
        other,
        shared_employee_hub.hub_resource_id,
        shared_employee_hub_v1.version,
        HubImportMode.REFERENCE,
    )
    runtime_deployment = recipient_catalog.deploy(
        other, runtime_employee.local_resource_id, runtime_employee.local_resource_version
    )
    runtime_deployment = configure_in_memory_deployment(
        recipient_catalog, other, runtime_deployment
    )
    resolved_control_plane = ControlPlaneService(
        recipient_catalog,
        referenced_runtime,
        persistence=MySqlExecutionRepository(connection_factory),
        resource_center_repository=resource_center.repository,
    )
    runtime_session = resolved_control_plane.create_session(other, runtime_deployment.deployment_id)
    reference_run = resolved_control_plane.create_run(
        other, runtime_session.session_id, "echo: hello", "reference-run-v1"
    )
    assert reference_run.status.value == "succeeded"
    assert referenced_runtime.last_request.system_prompt == "共享员工系统指令 v1"
    assert referenced_runtime.last_request.skill_instructions == ("共享技能指令 v1",)
    assert referenced_runtime.last_request.allowed_tools == ("echo",)
    hub_snapshot = reference_run.snapshot.hub_resource_refs
    assert {(item.hub_resource_id, item.hub_version, item.mode) for item in hub_snapshot} == {
        (shared_tool_hub.hub_resource_id, 1, "REFERENCE"),
        (shared_skill_hub.hub_resource_id, 1, "REFERENCE"),
        (shared_employee_hub.hub_resource_id, 1, "REFERENCE"),
    }
    captured_trace = resolved_control_plane.get_trace(other, reference_run.run_id)
    assert captured_trace.run.snapshot.hub_resource_refs == hub_snapshot
    restarted_control_plane = ControlPlaneService(
        recipient_catalog,
        CapturingRuntime(),
        persistence=MySqlExecutionRepository(connection_factory),
        resource_center_repository=resource_center.repository,
    )
    persisted_trace = restarted_control_plane.get_trace(other, reference_run.run_id)
    assert persisted_trace.run.snapshot.hub_resource_refs == hub_snapshot

    shared_tool_v2 = replace(
        shared_tool,
        version=2,
        description="无副作用回显 v2",
        status=LifecycleStatus.DRAFT,
    )
    catalog.repository.save_tool(identity, shared_tool_v2)
    catalog.publish_tool(identity, shared_tool.tool_id)
    resource_center.create_package(
        identity,
        HubResourceType.TOOL,
        shared_tool.tool_id,
        2,
        hub_resource_id=shared_tool_hub.hub_resource_id,
    )
    resource_center.publish_version(identity, shared_tool_hub.hub_resource_id, 2)
    assert resolved_control_plane.get_trace(other, reference_run.run_id).run.snapshot.hub_resource_refs == hub_snapshot
    assert referenced_runtime.last_request.skill_instructions == ("共享技能指令 v1",)

    resource_center.repository.revoke_grant(
        identity,
        shared_grants[shared_employee_hub.hub_resource_id].grant_id,
    )
    with pytest.raises(DomainError) as revoked_run:
        resolved_control_plane.create_run(
            other, runtime_session.session_id, "echo: after revoke", "reference-run-revoked"
        )
    assert revoked_run.value.code == "DEPENDENCY_VALIDATION_ERROR"

    resource_center.repository.revoke_grant(
        identity, grants_by_resource[employee_hub.hub_resource_id].grant_id
    )
    with pytest.raises(DomainError) as revoked_reference:
        recipient_resource_center.import_resource(
            other,
            employee_hub.hub_resource_id,
            employee_hub_version.version,
            HubImportMode.REFERENCE,
        )
    assert revoked_reference.value.code == "RESOURCE_NOT_FOUND"

    from dataclasses import replace

    source_tool_v2 = replace(
        tool,
        version=2,
        description="更新后的发布方 Tool v2",
        status=LifecycleStatus.DRAFT,
    )
    catalog.repository.save_tool(identity, source_tool_v2)
    catalog.publish_tool(identity, tool.tool_id)
    tool_hub_v2, tool_hub_version_v2 = resource_center.create_package(
        identity,
        HubResourceType.TOOL,
        tool.tool_id,
        2,
        hub_resource_id=tool_hub.hub_resource_id,
    )
    resource_center.publish_version(identity, tool_hub_v2.hub_resource_id, tool_hub_version_v2.version)
    copied_tool_after_update = recipient_catalog.repository.get_tool_version(
        other, copied_tool.tool_id, copied_tool.version
    )
    assert copied_tool_after_update.description == "读取工单"
    assert tool_hub_version_v2.version == 2

    with pytest.raises(DomainError) as cross_tenant:
        resource_center.create_package(
            other, HubResourceType.TOOL, tool.tool_id, tool.version
        )
    assert cross_tenant.value.code == "RESOURCE_NOT_FOUND"
    member = FakeIdentityProvider().resolve_identity("member-local")
    with pytest.raises(DomainError) as non_admin:
        resource_center.create_package(member, HubResourceType.TOOL, tool.tool_id, tool.version)
    assert non_admin.value.code == "AUTHORIZATION_DENIED"


def test_resource_center_publish_api_uses_authenticated_tenant_scope(
    mysql_resource_center_service_database,
) -> None:
    from fastapi.testclient import TestClient

    from agent_platform.api.app import create_app
    from agent_platform.infrastructure.mysql_resource_center import MySqlResourceCenterRepository

    database, config = mysql_resource_center_service_database
    connection_factory = lambda: mysql.connect(**config, database=database)
    app = create_app(
        mode="fake",
        resource_center_repository=MySqlResourceCenterRepository(connection_factory),
    )
    client = TestClient(app)
    headers = {"X-Fake-User": "admin-local"}
    tool = client.post(
        "/api/v1/tools",
        headers=headers,
        json={"name": "Hub API Tool", "description": "测试发布接口"},
    )
    assert tool.status_code == 201
    tool_id = tool.json()["id"]
    assert client.post(f"/api/v1/tools/{tool_id}/publish", headers=headers).status_code == 200
    package = client.post(
        "/api/v1/hub/resources",
        headers=headers,
        json={
            "resource_type": "TOOL",
            "source_resource_id": tool_id,
            "source_resource_version": 1,
            "visibility": "PLATFORM",
        },
    )
    assert package.status_code == 201
    assert package.json()["status"] == "DRAFT"
    assert package.json()["manifest_sha256"]

    hub_id = package.json()["hub_resource_id"]
    published = client.post(f"/api/v1/hub/resources/{hub_id}/versions/1/publish", headers=headers)
    assert published.status_code == 200
    assert published.json()["status"] == "PUBLISHED"

    discovered = client.get(
        "/api/v1/hub/resources",
        headers={"X-Fake-User": "admin-other"},
        params={"resource_type": "TOOL", "keyword": "Hub API"},
    )
    assert discovered.status_code == 200
    assert len(discovered.json()["items"]) == 1
    card = discovered.json()["items"][0]
    assert card["hub_resource_id"] == hub_id
    assert card["publisher_name"] == "发布方租户"
    assert card["available_modes"] == []
    assert card["is_publisher"] is False
    assert "publisher_tenant_id" not in card
    owned = client.get(
        "/api/v1/hub/resources",
        headers=headers,
        params={"scope": "OWNED", "resource_type": "TOOL"},
    )
    assert len(owned.json()["items"]) == 1
    assert owned.json()["items"][0]["is_publisher"] is True
    assert client.get(
        "/api/v1/hub/resources",
        headers={"X-Fake-User": "admin-other"},
        params={"mode": "COPY"},
    ).json()["items"] == []
    detail = client.get(
        f"/api/v1/hub/resources/{hub_id}/versions/1",
        headers={"X-Fake-User": "admin-other"},
    )
    assert detail.status_code == 200
    assert detail.json()["manifest_sha256"] == package.json()["manifest_sha256"]
    assert "manifest_json" not in detail.json()
    assert "publisher_tenant_id" not in detail.json()

    unauthorized_manifest = client.get(
        f"/api/v1/hub/resources/{hub_id}/versions/1/manifest",
        headers={"X-Fake-User": "admin-other"},
        params={"mode": "COPY"},
    )
    assert unauthorized_manifest.status_code == 404
    grant = client.post(
        f"/api/v1/hub/resources/{hub_id}/versions/1/grants",
        headers=headers,
        json={
            "recipient_tenant_id": "tenant-b",
            "copy_allowed": True,
            "reference_allowed": False,
        },
    )
    assert grant.status_code == 201
    grants = client.get(
        f"/api/v1/hub/resources/{hub_id}/versions/1/grants", headers=headers
    )
    assert grants.json()["items"][0]["grant_id"] == grant.json()["grant_id"]
    authorized_manifest = client.get(
        f"/api/v1/hub/resources/{hub_id}/versions/1/manifest",
        headers={"X-Fake-User": "admin-other"},
        params={"mode": "COPY"},
    )
    assert authorized_manifest.status_code == 200
    assert authorized_manifest.json()["manifest_sha256"] == package.json()["manifest_sha256"]
    assert client.get(
        f"/api/v1/hub/resources/{hub_id}/versions/1/grants",
        headers={"X-Fake-User": "admin-other"},
    ).status_code == 404
    assert client.delete(
        f"/api/v1/hub/grants/{grant.json()['grant_id']}", headers=headers
    ).status_code == 200

    other_headers = {"X-Fake-User": "admin-other"}
    forged = client.post(
        "/api/v1/hub/resources",
        headers=other_headers,
        json={"resource_type": "TOOL", "source_resource_id": tool_id, "source_resource_version": 1},
    )
    assert forged.status_code == 404


def test_hub_import_list_upgrade_and_revoke_api_preserves_old_proxy(
    mysql_resource_center_service_database,
) -> None:
    from dataclasses import replace

    from fastapi.testclient import TestClient

    from agent_platform.adapters.identity.fake import FakeIdentityProvider
    from agent_platform.api.app import create_app
    from agent_platform.application.catalog_service import CatalogService
    from agent_platform.application.resource_center import ResourceCenterService
    from agent_platform.domain.catalog import LifecycleStatus
    from agent_platform.domain.common import DomainError, generate_database_id
    from agent_platform.domain.resource_center import HubGrant, HubImportMode, HubResourceType
    from agent_platform.infrastructure.mysql_catalog import MySqlCatalogRepository
    from agent_platform.infrastructure.mysql_resource_center import MySqlResourceCenterRepository

    database, config = mysql_resource_center_service_database
    connection_factory = lambda: mysql.connect(**config, database=database)
    publisher = FakeIdentityProvider().resolve_identity("admin-local")
    recipient = FakeIdentityProvider().resolve_identity("admin-other")
    catalog = CatalogService(MySqlCatalogRepository(connection_factory))
    hub = MySqlResourceCenterRepository(connection_factory)
    service = ResourceCenterService(catalog, hub)
    tool = catalog.create_tool(publisher, "api-tool", "v1", {"type": "object"})
    catalog.publish_tool(publisher, tool.tool_id)
    resource, version1 = service.create_package(
        publisher, HubResourceType.TOOL, tool.tool_id, tool.version
    )
    service.publish_version(publisher, resource.hub_resource_id, version1.version)

    def grant(version: int) -> str:
        value = HubGrant(
            generate_database_id(),
            publisher.platform_id,
            resource.hub_resource_id,
            version,
            publisher.tenant_id,
            recipient.tenant_id,
            copy_allowed=True,
            reference_allowed=True,
            created_by=publisher.user_id,
        )
        hub.save_grant(publisher, value)
        return value.grant_id

    grant(version1.version)
    first_import = service.import_resource(
        recipient, resource.hub_resource_id, version1.version, HubImportMode.REFERENCE
    )
    catalog.repository.save_tool(
        publisher,
        replace(tool, version=2, description="v2", status=LifecycleStatus.DRAFT),
    )
    catalog.publish_tool(publisher, tool.tool_id)
    _, version2 = service.create_package(
        publisher,
        HubResourceType.TOOL,
        tool.tool_id,
        2,
        hub_resource_id=resource.hub_resource_id,
    )
    service.publish_version(publisher, resource.hub_resource_id, version2.version)
    grant2_id = grant(version2.version)

    client = TestClient(
        create_app(
            mode="fake",
            catalog_repository=catalog.repository,
            resource_center_repository=hub,
        )
    )
    headers = {"X-Fake-User": recipient.user_id}
    listed = client.get("/api/v1/hub/imports", headers=headers)
    assert listed.status_code == 200
    assert [item["import_id"] for item in listed.json()["items"]] == [first_import.import_id]

    upgraded_response = client.post(
        f"/api/v1/hub/imports/{first_import.import_id}/upgrade",
        headers=headers,
        json={"hub_version": version2.version},
    )
    assert upgraded_response.status_code == 201
    upgraded = upgraded_response.json()
    assert upgraded["hub_version"] == version2.version
    assert upgraded["mode"] == "REFERENCE"
    assert upgraded["local_resource_id"] != first_import.local_resource_id
    assert hub.get_import(
        recipient, resource.hub_resource_id, version1.version, HubImportMode.REFERENCE
    ).local_resource_id == first_import.local_resource_id

    revoked_response = client.delete(
        f"/api/v1/hub/imports/{first_import.import_id}", headers=headers
    )
    assert revoked_response.status_code == 200
    assert revoked_response.json()["status"] == "REVOKED"
    assert hub.get_import(
        recipient, resource.hub_resource_id, version1.version, HubImportMode.REFERENCE
    ).status.value == "REVOKED"

    publisher_client = TestClient(
        create_app(
            mode="fake",
            catalog_repository=catalog.repository,
            resource_center_repository=hub,
        )
    )
    revoked_grant = publisher_client.delete(
        f"/api/v1/hub/grants/{grant2_id}", headers={"X-Fake-User": publisher.user_id}
    )
    assert revoked_grant.status_code == 200
    with pytest.raises(DomainError) as unauthorized_after_revoke:
        hub.get_authorized_version(
            recipient, resource.hub_resource_id, version2.version, HubImportMode.REFERENCE
        )
    assert unauthorized_after_revoke.value.code == "RESOURCE_NOT_FOUND"


def test_hub_file_package_copy_and_reference_run_keep_tenant_boundary(
    mysql_resource_center_service_database, tmp_path
) -> None:
    import io
    import zipfile

    from agent_platform.adapters.identity.fake import FakeIdentityProvider
    from agent_platform.adapters.runtime.fake import FakeRuntimeAdapter
    from agent_platform.application.catalog_service import CatalogService
    from agent_platform.application.control_plane import ControlPlaneService
    from agent_platform.application.resource_center import ResourceCenterService
    from agent_platform.application.skill_files import SkillFilePackageService
    from agent_platform.domain.catalog import SkillContentFormat, SkillType
    from agent_platform.domain.common import DomainError, generate_database_id
    from agent_platform.domain.resource_center import HubGrant, HubImportMode, HubResourceType
    from agent_platform.infrastructure.mysql_catalog import MySqlCatalogRepository
    from agent_platform.infrastructure.mysql_resource_center import MySqlResourceCenterRepository
    from agent_platform.infrastructure.mysql_storage import MySqlMediaAssetRepository
    from agent_platform.ports.storage import LocalObjectStore

    database, config = mysql_resource_center_service_database
    connection_factory = lambda: mysql.connect(**config, database=database)
    publisher = FakeIdentityProvider().resolve_identity("admin-local")
    recipient = FakeIdentityProvider().resolve_identity("admin-other")
    catalog = CatalogService(MySqlCatalogRepository(connection_factory))
    hub_repository = MySqlResourceCenterRepository(connection_factory)
    media_assets = MySqlMediaAssetRepository(connection_factory)
    objects = LocalObjectStore(tmp_path / "objects", metadata_repository=media_assets)
    skill = catalog.create_skill(publisher, "共享文件包 Skill", SkillType.INSTRUCTION, "待替换", ())
    file_service = SkillFilePackageService(catalog, objects)
    archive_stream = io.BytesIO()
    with zipfile.ZipFile(archive_stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("SKILL.md", "共享文件包入口指令。")
        archive.writestr("helpers/do_not_execute.py", 'print("不应执行")')
    archive = archive_stream.getvalue()
    preview = file_service.preview_zip(publisher, skill.skill_id, 1, archive)
    source_skill = file_service.commit_zip(publisher, skill.skill_id, 1, preview.staging_id, archive)
    catalog.publish_skill(publisher, source_skill.skill_id)

    publisher_resource_center = ResourceCenterService(catalog, hub_repository, object_store=objects)
    hub_skill, hub_skill_version = publisher_resource_center.create_package(
        publisher, HubResourceType.SKILL, source_skill.skill_id, source_skill.version
    )
    assert hub_skill_version.manifest.data["content"]["content_format"] == "FILE_PACKAGE"
    assert hub_skill_version.manifest.data["content"]["file_manifest_sha256"] == preview.manifest_sha256
    publisher_resource_center.publish_version(publisher, hub_skill.hub_resource_id, 1)

    source_employee = catalog.create_employee(publisher, "共享文件员工", "助手", "共享固定版本 Skill")
    source_employee_version = catalog.create_employee_version(
        publisher, source_employee.employee_id, "员工系统指令", [source_skill.skill_id], []
    )
    catalog.publish_employee_version(publisher, source_employee.employee_id, source_employee_version.version)
    hub_employee, _hub_employee_version = publisher_resource_center.create_package(
        publisher,
        HubResourceType.DIGITAL_EMPLOYEE,
        source_employee.employee_id,
        source_employee_version.version,
    )
    publisher_resource_center.publish_version(publisher, hub_employee.hub_resource_id, 1)

    hub_skill_grant = None
    for resource in (hub_skill, hub_employee):
        grant = HubGrant(
            generate_database_id(),
            publisher.platform_id,
            resource.hub_resource_id,
            1,
            publisher.tenant_id,
            recipient.tenant_id,
            copy_allowed=True,
            reference_allowed=True,
            created_by=publisher.user_id,
        )
        hub_repository.save_grant(publisher, grant)
        if resource.hub_resource_id == hub_skill.hub_resource_id:
            hub_skill_grant = grant

    recipient_catalog = CatalogService(
        MySqlCatalogRepository(connection_factory),
        resource_center_repository=hub_repository,
    )
    recipient_center = ResourceCenterService(recipient_catalog, hub_repository, object_store=objects)
    copied_skill_import = recipient_center.import_resource(
        recipient, hub_skill.hub_resource_id, 1, HubImportMode.COPY
    )
    copied_skill = recipient_catalog.repository.get_skill_version(
        recipient, copied_skill_import.local_resource_id, copied_skill_import.local_resource_version
    )
    assert copied_skill.content_format is SkillContentFormat.FILE_PACKAGE
    assert copied_skill.file_manifest_sha256 == preview.manifest_sha256
    assert copied_skill.file_entries[0].media_asset_id != source_skill.file_entries[0].media_asset_id
    assert objects.read_asset(
        copied_skill.file_entries[0].media_asset_id,
        recipient.platform_id,
        recipient.tenant_id,
    ) == "共享文件包入口指令。".encode()

    for owner, asset_id in (
        (publisher, source_skill.file_entries[0].media_asset_id),
        (recipient, copied_skill.file_entries[0].media_asset_id),
    ):
        connection = connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                "SELECT object_ref FROM media_assets WHERE platform_id=%s AND tenant_id=%s AND asset_id=%s",
                (owner.platform_id, owner.tenant_id, asset_id),
            )
            object_ref = str(cursor.fetchone()[0])
            assert media_assets.is_object_protected(owner.platform_id, owner.tenant_id, object_ref)
        finally:
            cursor.close()
            connection.close()

    referenced_employee = recipient_center.import_resource(
        recipient, hub_employee.hub_resource_id, 1, HubImportMode.REFERENCE
    )
    referenced_skill_import = hub_repository.get_import(
        recipient, hub_skill.hub_resource_id, 1, HubImportMode.REFERENCE
    )
    referenced_skill = recipient_catalog.repository.get_skill_version(
        recipient,
        referenced_skill_import.local_resource_id,
        referenced_skill_import.local_resource_version,
    )
    assert referenced_skill.content_format is SkillContentFormat.FILE_PACKAGE
    assert referenced_skill.file_entries == ()
    assert referenced_skill.file_manifest_sha256 == preview.manifest_sha256
    from fastapi.testclient import TestClient

    from agent_platform.api.app import create_app

    recipient_client = TestClient(
        create_app(
            mode="fake",
            catalog_repository=recipient_catalog.repository,
            resource_center_repository=hub_repository,
            object_store=objects,
        )
    )
    referenced_files = recipient_client.get(
        f"/api/v1/skills/{referenced_skill.skill_id}/versions/1/files",
        headers={"X-Fake-User": recipient.user_id},
    )
    assert referenced_files.status_code == 200
    assert referenced_files.json()["read_only"] is True
    assert [item["path"] for item in referenced_files.json()["files"]] == [
        "SKILL.md",
        "helpers/do_not_execute.py",
    ]
    referenced_entry = recipient_client.get(
        f"/api/v1/skills/{referenced_skill.skill_id}/versions/1/files/content",
        headers={"X-Fake-User": recipient.user_id},
        params={"file_path": "SKILL.md"},
    )
    assert referenced_entry.status_code == 200
    assert referenced_entry.json()["content_text"] == "共享文件包入口指令。"
    readonly_edit = recipient_client.post(
        f"/api/v1/skills/{referenced_skill.skill_id}/versions/1/files",
        headers={"X-Fake-User": recipient.user_id},
        json={"file_path": "SKILL.md", "content_text": "修改共享入口"},
    )
    assert readonly_edit.status_code == 409
    with pytest.raises(DomainError) as immutable_reference:
        recipient_catalog.update_skill(recipient, referenced_skill.skill_id, instruction="修改共享入口")
    assert immutable_reference.value.code == "IMMUTABLE_VERSION"

    class CapturingRuntime(FakeRuntimeAdapter):
        last_request = None

        def run(self, request):
            self.last_request = request
            return super().run(request)

    runtime = CapturingRuntime()
    deployment = recipient_catalog.deploy(
        recipient, referenced_employee.local_resource_id, referenced_employee.local_resource_version
    )
    deployment = configure_in_memory_deployment(recipient_catalog, recipient, deployment)
    control_plane = ControlPlaneService(
        recipient_catalog,
        runtime,
        resource_center_repository=hub_repository,
        object_store=objects,
    )
    session = control_plane.create_session(recipient, deployment.deployment_id)
    run = control_plane.create_run(recipient, session.session_id, "按共享入口处理")
    assert runtime.last_request.skill_instructions == ("共享文件包入口指令。",)
    assert runtime.last_request.skill_file_packages[0].manifest_sha256 == preview.manifest_sha256
    assert runtime.last_request.skill_file_packages[0].files[0].asset_tenant_id == publisher.tenant_id
    assert runtime.last_request.skill_file_packages[0].files[0].source_mode == "REFERENCE"
    assert run.snapshot.hub_resource_refs
    assert "不应执行" not in run.result_text

    digest_employee = recipient_catalog.create_employee(
        recipient, "摘要预检员工", "助手", "校验引用摘要"
    )
    digest_version = recipient_catalog.create_employee_version(
        recipient, digest_employee.employee_id, "校验引用", [referenced_skill.skill_id], []
    )
    connection = connection_factory()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "UPDATE tenant_resource_imports SET manifest_sha256=%s WHERE platform_id=%s AND tenant_id=%s "
            "AND hub_resource_id=%s AND hub_version=1 AND mode='REFERENCE'",
            ("0" * 64, recipient.platform_id, recipient.tenant_id, hub_skill.hub_resource_id),
        )
        connection.commit()
    finally:
        cursor.close()
        connection.close()
    with pytest.raises(DomainError) as digest_mismatch:
        recipient_catalog.publish_employee_version(
            recipient, digest_employee.employee_id, digest_version.version
        )
    assert any(
        item.get("reason") == "MANIFEST_DIGEST_MISMATCH"
        for item in digest_mismatch.value.details["missing"]
    )

    grant_employee = recipient_catalog.create_employee(
        recipient, "授权预检员工", "助手", "校验引用 Grant"
    )
    grant_version = recipient_catalog.create_employee_version(
        recipient, grant_employee.employee_id, "校验授权", [referenced_skill.skill_id], []
    )
    connection = connection_factory()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "UPDATE tenant_resource_imports SET manifest_sha256=%s WHERE platform_id=%s AND tenant_id=%s "
            "AND hub_resource_id=%s AND hub_version=1 AND mode='REFERENCE'",
            (
                hub_skill_version.manifest.sha256,
                recipient.platform_id,
                recipient.tenant_id,
                hub_skill.hub_resource_id,
            ),
        )
        connection.commit()
    finally:
        cursor.close()
        connection.close()
    assert hub_skill_grant is not None
    hub_repository.revoke_grant(publisher, hub_skill_grant.grant_id)
    with pytest.raises(DomainError) as revoked_grant:
        recipient_catalog.publish_employee_version(
            recipient, grant_employee.employee_id, grant_version.version
        )
    assert any(
        item.get("reason") == "GRANT_REVOKED_OR_EXPIRED"
        for item in revoked_grant.value.details["missing"]
    )


def test_mysql_local_object_store_resource_center_copy_reference_full_api_flow(
    mysql_resource_center_service_database, tmp_path
) -> None:
    """用两租户 HTTP API 验证 Skill ZIP/Tool/员工包到 Run Trace 的固定快照。"""

    import io
    import zipfile

    from fastapi.testclient import TestClient

    from agent_platform.api.app import create_app
    from agent_platform.infrastructure.mysql_catalog import MySqlCatalogRepository
    from agent_platform.infrastructure.mysql_conversations import MySqlConversationRepository
    from agent_platform.infrastructure.mysql_execution import MySqlExecutionRepository
    from agent_platform.infrastructure.mysql_resource_center import MySqlResourceCenterRepository
    from agent_platform.infrastructure.mysql_storage import MySqlMediaAssetRepository
    from agent_platform.ports.storage import LocalObjectStore

    database, config = mysql_resource_center_service_database
    connection_factory = lambda: mysql.connect(**config, database=database)
    media_repository = MySqlMediaAssetRepository(connection_factory)
    objects = LocalObjectStore(tmp_path / "resource-center-e2e-objects", metadata_repository=media_repository)
    hub_repository = MySqlResourceCenterRepository(connection_factory)

    def make_client() -> TestClient:
        return TestClient(
            create_app(
                mode="fake",
                catalog_repository=MySqlCatalogRepository(connection_factory),
                execution_repository=MySqlExecutionRepository(connection_factory),
                conversation_repository=MySqlConversationRepository(connection_factory),
                resource_center_repository=hub_repository,
                object_store=objects,
            )
        )

    publisher = make_client()
    recipient = make_client()
    publisher_headers = {"X-Fake-User": "admin-local"}
    recipient_headers = {"X-Fake-User": "admin-other"}

    # 发布 Tool 并先登记其 Hub 固定版本，使 Skill/员工包依赖能引用稳定来源。
    tool_response = publisher.post(
        "/api/v1/tools",
        headers=publisher_headers,
        json={"name": "查询工单", "description": "只读工单查询", "input_schema": {"type": "object"}},
    )
    assert tool_response.status_code == 201, tool_response.text
    tool_id = tool_response.json()["id"]
    tool_publish = publisher.post(f"/api/v1/tools/{tool_id}/publish", headers=publisher_headers)
    assert tool_publish.status_code == 200, tool_publish.text
    tool_version = tool_publish.json()["version"]
    tool_package = publisher.post(
        "/api/v1/hub/resources",
        headers=publisher_headers,
        json={
            "tenant_id": "tenant-a",
            "resource_type": "TOOL",
            "source_resource_id": tool_id,
            "source_resource_version": tool_version,
            "visibility": "PLATFORM",
        },
    )
    assert tool_package.status_code == 201, tool_package.text
    tool_hub_id = tool_package.json()["hub_resource_id"]
    assert publisher.post(
        f"/api/v1/hub/resources/{tool_hub_id}/versions/1/publish", headers=publisher_headers
    ).status_code == 200

    # ZIP 在预览后以原字节提交，根 SKILL.md 作为唯一入口并带一个只读辅助文件。
    skill_response = publisher.post(
        "/api/v1/skills",
        headers=publisher_headers,
        json={
            "name": "共享工单 Skill",
            "skill_type": "instruction",
            "instruction": "占位内容会被 ZIP 清单替换",
            "allowed_tool_ids": [tool_id],
        },
    )
    assert skill_response.status_code == 201, skill_response.text
    skill_id = skill_response.json()["id"]
    archive_buffer = io.BytesIO()
    with zipfile.ZipFile(archive_buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("SKILL.md", "根据固定流程查询工单。")
        archive.writestr("references/fields.md", "字段说明：工单编号、状态。")
    archive_bytes = archive_buffer.getvalue()
    zip_headers = {**publisher_headers, "Content-Type": "application/zip"}
    preview = publisher.post(
        f"/api/v1/skills/{skill_id}/versions/1/files/zip/preview?strategy=replace",
        headers=zip_headers,
        content=archive_bytes,
    )
    assert preview.status_code == 200, preview.text
    skill_file_digest = preview.json()["manifest_sha256"]
    committed = publisher.post(
        f"/api/v1/skills/{skill_id}/versions/1/files/zip/commit/{preview.json()['staging_id']}",
        headers=zip_headers,
        content=archive_bytes,
    )
    assert committed.status_code == 200, committed.text
    assert committed.json()["entry_path"] == "SKILL.md"
    skill_publish = publisher.post(f"/api/v1/skills/{skill_id}/publish", headers=publisher_headers)
    assert skill_publish.status_code == 200, skill_publish.text
    skill_version = skill_publish.json()["version"]
    skill_package = publisher.post(
        "/api/v1/hub/resources",
        headers=publisher_headers,
        json={
            "tenant_id": "tenant-a",
            "resource_type": "SKILL",
            "source_resource_id": skill_id,
            "source_resource_version": skill_version,
            "visibility": "PLATFORM",
        },
    )
    assert skill_package.status_code == 201, skill_package.text
    skill_hub_id = skill_package.json()["hub_resource_id"]
    assert publisher.post(
        f"/api/v1/hub/resources/{skill_hub_id}/versions/1/publish", headers=publisher_headers
    ).status_code == 200

    employee_response = publisher.post(
        "/api/v1/digital-employees",
        headers=publisher_headers,
        json={"name": "共享工单员工", "role_name": "客服", "description": "按共享 Skill 查询工单"},
    )
    assert employee_response.status_code == 201, employee_response.text
    source_employee_id = employee_response.json()["id"]
    source_version_response = publisher.post(
        f"/api/v1/digital-employees/{source_employee_id}/versions",
        headers=publisher_headers,
        json={
            "system_prompt": "你是工单客服，遵循 Skill 入口。",
            "skill_ids": [skill_id],
            "tool_ids": [tool_id],
        },
    )
    assert source_version_response.status_code == 201, source_version_response.text
    source_version = source_version_response.json()["version"]
    source_published = publisher.post(
        f"/api/v1/digital-employees/{source_employee_id}/versions/{source_version}/publish",
        headers=publisher_headers,
    )
    assert source_published.status_code == 200, source_published.text
    employee_package = publisher.post(
        "/api/v1/hub/resources",
        headers=publisher_headers,
        json={
            "tenant_id": "tenant-a",
            "resource_type": "DIGITAL_EMPLOYEE",
            "source_resource_id": source_employee_id,
            "source_resource_version": source_version,
            "visibility": "PLATFORM",
        },
    )
    assert employee_package.status_code == 201, employee_package.text
    employee_hub_id = employee_package.json()["hub_resource_id"]
    assert publisher.post(
        f"/api/v1/hub/resources/{employee_hub_id}/versions/1/publish", headers=publisher_headers
    ).status_code == 200

    # 目录公开可发现，但 Package 和两种导入方式都要先由发布租户逐版本授权。
    for hub_id in (tool_hub_id, skill_hub_id, employee_hub_id):
        grant = publisher.post(
            f"/api/v1/hub/resources/{hub_id}/versions/1/grants",
            headers=publisher_headers,
            json={
                "tenant_id": "tenant-a",
                "recipient_tenant_id": "tenant-b",
                "copy_allowed": True,
                "reference_allowed": True,
            },
        )
        assert grant.status_code == 201, grant.text

    discover = recipient.get("/api/v1/hub/resources?resource_type=DIGITAL_EMPLOYEE", headers=recipient_headers)
    assert discover.status_code == 200
    assert any(item["hub_resource_id"] == employee_hub_id for item in discover.json()["items"])
    manifest = recipient.get(
        f"/api/v1/hub/resources/{employee_hub_id}/versions/1/manifest?mode=COPY",
        headers=recipient_headers,
    )
    assert manifest.status_code == 200, manifest.text
    assert manifest.json()["manifest"]["dependencies"]

    imported: dict[str, dict[str, object]] = {}
    for mode in ("COPY", "REFERENCE"):
        response = recipient.post(
            "/api/v1/hub/imports",
            headers=recipient_headers,
            json={"tenant_id": "tenant-b", "hub_resource_id": employee_hub_id, "hub_version": 1, "mode": mode},
        )
        assert response.status_code == 201, response.text
        imported[mode] = response.json()
        assert imported[mode]["runtime_configuration_required"] is True
        assert imported[mode]["next_action"] == "CONFIGURE_DEPLOYMENT_RUNTIME"

    copy_employee = imported["COPY"]
    copy_employee_id = str(copy_employee["local_resource_id"])
    copy_employee_version = int(copy_employee["local_resource_version"])
    copy_version = recipient.get(
        f"/api/v1/digital-employees/{copy_employee_id}/versions/{copy_employee_version}",
        headers=recipient_headers,
    )
    assert copy_version.status_code == 200, copy_version.text
    copy_version_data = copy_version.json()
    copy_skill_ref = copy_version_data["skill_version_refs"][0]
    copy_tool_ref = copy_version_data["tool_version_refs"][0]
    # COPY 的依赖均是接收租户 DRAFT，按 Tool -> Skill 顺序发布依赖。
    copy_tool_publish = recipient.post(
        f"/api/v1/tools/{copy_tool_ref['tool_id']}/publish", headers=recipient_headers
    )
    assert copy_tool_publish.status_code == 200, copy_tool_publish.text
    copy_skill_publish = recipient.post(
        f"/api/v1/skills/{copy_skill_ref['skill_id']}/publish", headers=recipient_headers
    )
    assert copy_skill_publish.status_code == 200, copy_skill_publish.text
    # 再经 DRAFT PUT 固定本地副本 ID+版本，确保员工绑定不依赖 Hub 的远端标识。
    copy_saved = recipient.put(
        f"/api/v1/digital-employees/{copy_employee_id}/versions/{copy_employee_version}",
        headers=recipient_headers,
        json={
            "tenant_id": "tenant-b",
            "system_prompt": copy_version_data["system_prompt"],
            "skill_version_refs": [
                {"resource_id": copy_skill_ref["skill_id"], "version": copy_skill_ref["version"]}
            ],
            "tool_version_refs": [
                {"resource_id": copy_tool_ref["tool_id"], "version": copy_tool_ref["version"]}
            ],
        },
    )
    assert copy_saved.status_code == 200, copy_saved.text
    copy_published = recipient.post(
        f"/api/v1/digital-employees/{copy_employee_id}/versions/{copy_employee_version}/publish",
        headers=recipient_headers,
    )
    assert copy_published.status_code == 200, copy_published.text

    # REFERENCE 代理保持已发布只读，和 COPY 副本分开部署、运行并追踪。
    reference_employee_id = str(imported["REFERENCE"]["local_resource_id"])
    reference_employee_version = int(imported["REFERENCE"]["local_resource_version"])
    reference_version = recipient.get(
        f"/api/v1/digital-employees/{reference_employee_id}/versions/{reference_employee_version}",
        headers=recipient_headers,
    )
    assert reference_version.status_code == 200, reference_version.text
    assert reference_version.json()["status"] == "PUBLISHED"

    recipient_runtime = {
        "COPY": ("codex_cli", "openai-responses", "gpt-codex-recipient"),
        "REFERENCE": ("deepseek_harness", "openai-chat-completions", "deepseek-recipient"),
    }
    profiles_by_mode: dict[str, dict[str, object]] = {}
    routes_by_mode: dict[str, dict[str, object]] = {}
    for mode, (engine_name, protocol, model_id) in recipient_runtime.items():
        connection = recipient.post(
            "/api/v1/model-connections",
            headers=recipient_headers,
            json={
                "name": f"接收方 {engine_name} 连接",
                "provider_kind": "openai-compatible",
                "protocol": protocol,
                "endpoint": "http://recipient-model.local/v1",
                "catalog_mode": "MANUAL",
            },
        )
        assert connection.status_code == 201, connection.text
        model = recipient.post(
            f"/api/v1/model-connections/{connection.json()['connection_id']}/models",
            headers=recipient_headers,
            json={"model_id": model_id, "display_name": model_id},
        )
        assert model.status_code == 201, model.text
        route = recipient.post(
            "/api/v1/model-routes",
            headers=recipient_headers,
            json={
                "alias": f"本地 {engine_name} 路由",
                "connection_id": connection.json()["connection_id"],
                "catalog_model_id": model_id,
                "model_name": model_id,
            },
        )
        assert route.status_code == 201, route.text
        routes_by_mode[mode] = route.json()
        profile = recipient.post(
            "/api/v1/execution-profiles",
            headers=recipient_headers,
            json={"engine_name": engine_name},
        )
        assert profile.status_code == 201, profile.text
        profiles_by_mode[mode] = profile.json()

    for mode, employee_id, version in (
        ("COPY", copy_employee_id, copy_employee_version),
        ("REFERENCE", reference_employee_id, reference_employee_version),
    ):
        deployment = recipient.post(
            "/api/v1/deployments",
            headers=recipient_headers,
            json={"tenant_id": "tenant-b", "employee_id": employee_id, "version": version},
        )
        assert deployment.status_code == 201, deployment.text
        engine_profile = profiles_by_mode[mode]
        model_route = routes_by_mode[mode]
        saved_config = recipient.put(
            f"/api/v1/deployments/{deployment.json()['id']}/runtime-config",
            headers=recipient_headers,
            json={
                "tenant_id": "tenant-b",
                "expected_config_version": None,
                "mode": "OVERRIDE",
                "execution_profile_id": engine_profile["id"],
                "execution_profile_version": engine_profile["version"],
                "model_route_id": model_route["route_id"],
            },
        )
        assert saved_config.status_code == 200, saved_config.text
        session = recipient.post(
            "/api/v1/sessions",
            headers=recipient_headers,
            json={"tenant_id": "tenant-b", "deployment_id": deployment.json()["id"]},
        )
        assert session.status_code == 201, session.text
        engine_name = recipient_runtime[mode][0]
        assert session.json()["engine_name"] == engine_name
        assert session.json()["model_route_id"] == model_route["route_id"]
        run = recipient.post(
            f"/api/v1/sessions/{session.json()['id']}/runs",
            headers=recipient_headers,
            json={"tenant_id": "tenant-b", "prompt": f"{mode} 工单：查询状态", "idempotency_key": f"hub-e2e-{mode.lower()}"},
        )
        assert run.status_code == 201, run.text
        assert run.json()["status"] == "succeeded"
        assert run.json()["result"].startswith("fake:")
        assert run.json()["snapshot"]["engine_name"] == engine_name
        assert run.json()["snapshot"]["model_route_id"] == model_route["route_id"]
        assert run.json()["snapshot"]["model_route_snapshot"]["model_id"] == recipient_runtime[mode][2]
        refs = run.json()["snapshot"]["hub_resource_refs"]
        assert refs
        assert {item["mode"] for item in refs} == {mode}
        assert {item["hub_resource_id"] for item in refs} == {tool_hub_id, skill_hub_id, employee_hub_id}
        packages = run.json()["snapshot"]["skill_file_packages"]
        assert len(packages) == 1
        assert packages[0]["manifest_sha256"] == skill_file_digest
        assert {item["path"] for item in packages[0]["files"]} == {"SKILL.md", "references/fields.md"}
        trace = recipient.get(f"/api/v1/runs/{run.json()['id']}/trace", headers=recipient_headers)
        assert trace.status_code == 200, trace.text
        assert trace.json()["run"]["snapshot"]["hub_resource_refs"] == refs
        assert trace.json()["run"]["snapshot"]["skill_file_packages"] == packages
        assert any(span["span_type"] == "skill" for span in trace.json()["spans"])

    publisher.close()
    recipient.close()
