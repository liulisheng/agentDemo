"""backend-owned MySQL catalog repositories 的 disposable integration tests。"""

from __future__ import annotations

import os
import secrets
from dataclasses import replace
from pathlib import Path

import pytest

mysql = pytest.importorskip("mysql.connector")


class _RedactedMySqlConfig(dict[str, object]):
    """避免 pytest 失败回溯打印测试库密码。"""

    def __repr__(self) -> str:
        return "MySQL catalog test config(<redacted>)"


@pytest.fixture
def mysql_catalog_database():
    if os.environ.get("AGENT_PLATFORM_MYSQL_TEST_ALLOW_CREATE_DATABASE") != "1":
        pytest.skip("set AGENT_PLATFORM_MYSQL_TEST_ALLOW_CREATE_DATABASE=1 for disposable MySQL integration")
    database = f"agent_platform_catalog_test_{secrets.token_hex(5)}"
    config = _RedactedMySqlConfig({
        "host": os.environ.get("MYSQL_HOST", "127.0.0.1"),
        "port": int(os.environ.get("MYSQL_PORT", "3306")),
        "user": os.environ.get("MYSQL_USER", "root"),
        "password": os.environ.get("MYSQL_PASSWORD", ""),
    })
    root = mysql.connect(**config)
    cursor = root.cursor()
    try:
        cursor.execute(f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci")
        root.commit()
    finally:
        cursor.close()
        root.close()
    try:
        from agent_platform.infrastructure.migrations import MigrationRunner, PreflightAction

        runner = MigrationRunner(lambda: mysql.connect(**config, database=database), Path(__file__).parents[3] / "docs" / "migrations")
        result = runner.apply()
        if result.action is not PreflightAction.APPLY_MIGRATIONS:
            raise AssertionError(f"test database migration failed: {result}")
        connection = mysql.connect(**config, database=database)
        cursor = connection.cursor()
        try:
            cursor.execute(
                "INSERT INTO platforms (platform_id, code, name, status) VALUES (%s, %s, %s, %s)",
                ("plt-local", "platform-local", "本地平台", "ACTIVE"),
            )
            cursor.execute(
                "INSERT INTO tenants (platform_id, tenant_id, code, name, status) VALUES (%s, %s, %s, %s, %s)",
                ("plt-local", "tenant-a", "tenant-a", "租户 A", "ACTIVE"),
            )
            cursor.execute(
                "INSERT INTO users (platform_id, tenant_id, user_id, external_subject, username, display_name, status) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                ("plt-local", "tenant-a", "admin-local", "subject-admin-local", "admin-local", "本地管理员", "ACTIVE"),
            )
            connection.commit()
        finally:
            cursor.close()
            connection.close()
        yield database, config
    finally:
        cleanup = mysql.connect(**config)
        cursor = cleanup.cursor()
        try:
            cursor.execute(f"DROP DATABASE IF EXISTS `{database}`")
            cleanup.commit()
        finally:
            cursor.close()
            cleanup.close()


def test_mysql_catalog_repository_round_trips_versioned_resources_and_tenant_scope(mysql_catalog_database) -> None:
    from agent_platform.adapters.identity.fake import FakeIdentityProvider
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
    from agent_platform.domain.model_routes import (
        ModelCatalogItem,
        ModelCatalogSource,
        ModelConnection,
        ModelProfile,
        ModelRoute,
        SecretRef,
    )
    from agent_platform.infrastructure.mysql_catalog import MySqlCatalogRepository
    from agent_platform.ports.workspaces import SandboxProfile, WorkspaceMode, WorkspaceProfile

    database, config = mysql_catalog_database
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    repository = MySqlCatalogRepository(lambda: mysql.connect(**config, database=database))
    tool = ToolVersion("tool-000000000001", identity.tenant_id, "echo", "回显文本", {"type": "object"}).publish()
    repository.save_tool(identity, tool)
    assert repository.get_tool(identity, tool.tool_id) == tool

    skill = SkillVersion(
        "skill-000000000001",
        identity.tenant_id,
        "回声 Skill",
        SkillType.TOOL_BUNDLE,
        "调用 echo",
        allowed_tool_ids=(tool.tool_id,),
    ).publish()
    repository.save_skill(identity, skill)
    assert repository.get_skill(identity, skill.skill_id) == skill

    profile = ExecutionProfile("profile-000000001", identity.tenant_id, "fake", "fake-model").publish()
    repository.save_profile(identity, profile)
    assert repository.get_profile(identity, profile.profile_id) == profile

    employee = DigitalEmployee("employee-000001", identity.tenant_id, "助手", "支持", "帮助用户")
    repository.save_employee(identity, employee)
    version = DigitalEmployeeVersion(
        employee.employee_id,
        identity.tenant_id,
        1,
        "你是支持助手",
        (skill.skill_id,),
        (tool.tool_id,),
        profile.profile_id,
        LifecycleStatus.PUBLISHED,
    )
    repository.save_employee_version(identity, version)
    assert repository.get_employee(identity, employee.employee_id) == employee
    assert repository.get_employee_version(identity, employee.employee_id, 1) == version

    deployment = Deployment("deployment-000001", identity.tenant_id, employee.employee_id, version)
    repository.save_deployment(identity, deployment)
    loaded = repository.get_deployment(identity, deployment.deployment_id)
    assert loaded.employee_version == version
    assert repository.list_deployments(identity) == (deployment,)
    bindings = repository.list_capability_bindings(identity, employee.employee_id, 1)
    assert [(binding.slot, binding.module_id, binding.module_version) for binding in bindings] == [
        ("skill", skill.skill_id, 1), ("tool", tool.tool_id, 1)
    ]

    connection = ModelConnection(
        connection_id="connection-model-000001",
        tenant_id=identity.tenant_id,
        name="Fake Model Connection",
        provider_kind="fake",
        protocol="openai-compatible",
        endpoint="fake://model",
        secret_ref=SecretRef("secret-ref-1"),
        catalog_mode="MANUAL",
    )
    repository.save_model_connection(identity, connection)
    assert repository.get_model_connection(identity, connection.connection_id) == connection
    assert repository.list_model_connections(identity) == (connection,)

    model_a = ModelCatalogItem(
        connection_id=connection.connection_id,
        tenant_id=identity.tenant_id,
        model_id="fake-model",
        display_name="Fake Model",
        capabilities={"chat": True},
        catalog_source=ModelCatalogSource.MANUAL,
    )
    model_b = ModelCatalogItem(
        connection_id=connection.connection_id,
        tenant_id=identity.tenant_id,
        model_id="fake-reasoning-model",
        display_name="Fake Reasoning Model",
        capabilities={"chat": True, "reasoning": True},
        catalog_source=ModelCatalogSource.MANUAL,
    )
    repository.save_model_catalog_item(identity, model_a)
    repository.save_model_catalog_item(identity, model_b)
    assert repository.list_model_catalog_items(identity, connection.connection_id) == (model_a, model_b)

    route = ModelRoute(
        "route-000000000001",
        identity.tenant_id,
        "fake-route",
        ModelProfile(
            "model-profile-route-000000000001",
            identity.tenant_id,
            "fake-model",
            "fake",
            "fake://model",
            secret_ref=SecretRef("secret-ref-1"),
            connection_id=connection.connection_id,
            catalog_model_id="fake-model",
        ),
    )
    repository.save_model_route(identity, route)
    assert repository.get_model_route(identity, route.route_id) == route

    legacy_route = ModelRoute(
        "route-000000000002",
        identity.tenant_id,
        "legacy-direct-route",
        ModelProfile(
            "model-profile-route-000000000002",
            identity.tenant_id,
            "legacy-model",
            "legacy-openai-compatible",
            "http://legacy.example/v1",
        ),
    )
    repository.save_model_route(identity, legacy_route)
    migrated_route = repository.get_model_route(identity, legacy_route.route_id)
    assert migrated_route.profile.connection_id is not None
    assert migrated_route.profile.catalog_model_id == "legacy-model"
    assert repository.get_model_connection(
        identity, migrated_route.profile.connection_id
    ).endpoint == "http://legacy.example/v1"

    workspace = WorkspaceProfile(
        "workspace-000000001",
        mode=WorkspaceMode.EPHEMERAL,
        project_id="project-1",
        files=True,
        repository=True,
        quota={"memory_mb": 512},
    )
    sandbox = SandboxProfile("sandbox-00000001", network_policy="deny-all", cpu_limit=2, memory_mb=1024)
    repository.save_workspace_profile(identity, workspace)
    repository.save_sandbox_profile(identity, sandbox)
    assert repository.get_workspace_profile(identity, workspace.profile_id) == workspace
    assert repository.get_sandbox_profile(identity, sandbox.profile_id) == sandbox

    routed_profile = ExecutionProfile(
        "profile-000000002",
        identity.tenant_id,
        "fake",
        "fake-model",
        model_route_id=route.route_id,
        workspace_profile_id=workspace.profile_id,
        sandbox_profile_id=sandbox.profile_id,
    ).publish()
    repository.save_profile(identity, routed_profile)
    assert repository.get_profile_version(identity, routed_profile.profile_id, 1) == routed_profile

    tool_v2 = ToolVersion(tool.tool_id, identity.tenant_id, "echo-v2", "新版回显", {"type": "object"}, version=2).publish()
    repository.save_tool(identity, tool_v2)
    skill_v2 = SkillVersion(
        skill.skill_id,
        identity.tenant_id,
        "回声 Skill v2",
        SkillType.TOOL_BUNDLE,
        "调用 echo v2",
        allowed_tool_ids=(tool.tool_id,),
        version=2,
        status=LifecycleStatus.PUBLISHED,
        allowed_tool_versions=((tool.tool_id, 2),),
    )
    repository.save_skill(identity, skill_v2)
    assert repository.get_skill_version(identity, skill.skill_id, 1) == skill
    assert repository.get_skill(identity, skill.skill_id) == skill_v2
    assert repository.get_tool_version(identity, tool.tool_id, 1) == tool
    assert repository.get_tool(identity, tool.tool_id) == tool_v2
    with pytest.raises(DomainError) as immutable:
        repository.save_tool(identity, replace(tool, description="不能覆盖已发布版本"))
    assert immutable.value.code == "IMMUTABLE_VERSION"

    other = FakeIdentityProvider().resolve_identity("admin-other")
    with pytest.raises(DomainError) as error:
        repository.get_employee(other, employee.employee_id)
    assert error.value.code == "RESOURCE_NOT_FOUND"
    assert repository.list_model_connections(other) == ()
    with pytest.raises(DomainError) as hidden_catalog:
        repository.list_model_catalog_items(other, connection.connection_id)
    assert hidden_catalog.value.code == "RESOURCE_NOT_FOUND"
    with pytest.raises(DomainError) as hidden_model_connection:
        repository.get_model_connection(other, connection.connection_id)
    assert hidden_model_connection.value.code == "RESOURCE_NOT_FOUND"
    with pytest.raises(DomainError) as cross_tenant_connection:
        repository.save_model_connection(other, connection)
    assert cross_tenant_connection.value.code == "AUTHORIZATION_DENIED"
    with pytest.raises(DomainError) as cross_tenant_catalog:
        repository.save_model_catalog_item(
            other,
            ModelCatalogItem(
                connection_id=connection.connection_id,
                tenant_id=identity.tenant_id,
                model_id="model-cross-tenant",
                display_name="跨租户模型",
            ),
        )
    assert cross_tenant_catalog.value.code == "AUTHORIZATION_DENIED"
    other_platform = replace(identity, platform_id="plt-other")
    assert repository.list_model_connections(other_platform) == ()
    with pytest.raises(DomainError) as hidden_platform_connection:
        repository.get_model_connection(other_platform, connection.connection_id)
    assert hidden_platform_connection.value.code == "RESOURCE_NOT_FOUND"


def test_backend_database_ids_round_trip_through_v001_v002_char26_keys(mysql_catalog_database) -> None:
    from agent_platform.domain.common import generate_database_id

    database, config = mysql_catalog_database
    identifiers = {
        "session": generate_database_id(),
        "run": generate_database_id(),
        "message": generate_database_id(),
        "upload": generate_database_id(),
        "cleanup": generate_database_id(),
    }
    assert all(len(value) == 26 for value in identifiers.values())
    connection = mysql.connect(**config, database=database)
    cursor = connection.cursor()
    try:
        cursor.execute(
            "INSERT INTO sessions (platform_id, tenant_id, session_id, user_id, engine_name, engine_session_id, status) "
            "VALUES ('plt-local','tenant-a',%s,'admin-local','fake','runtime-session-1','ACTIVE')",
            (identifiers["session"],),
        )
        digest = "a" * 64
        cursor.execute(
            "INSERT INTO runs (platform_id, tenant_id, run_id, session_id, idempotency_key, request_digest, state) "
            "VALUES ('plt-local','tenant-a',%s,%s,'idempotency-1',%s,'QUEUED')",
            (identifiers["run"], identifiers["session"], digest),
        )
        cursor.execute(
            "INSERT INTO messages (platform_id, tenant_id, message_id, session_id, run_id, sequence_no, role, status, content_ref, content_sha256, byte_length, content_type) "
            "VALUES ('plt-local','tenant-a',%s,%s,%s,1,'USER','COMPLETE','object:plt-local:tenant-a:prompt',%s,5,'text/plain')",
            (identifiers["message"], identifiers["session"], identifiers["run"], digest),
        )
        cursor.execute(
            "INSERT INTO content_upload_intents (platform_id, tenant_id, upload_id, session_id, message_id, run_id, object_ref, object_type, lease_expires_at) "
            "VALUES ('plt-local','tenant-a',%s,%s,%s,%s,'object:plt-local:tenant-a:chunk','STREAM_CHUNK',CURRENT_TIMESTAMP(6))",
            (identifiers["upload"], identifiers["session"], identifiers["message"], identifiers["run"]),
        )
        cursor.execute(
            "INSERT INTO content_gc_queue (platform_id, tenant_id, cleanup_id, object_ref, object_type) "
            "VALUES ('plt-local','tenant-a',%s,'object:plt-local:tenant-a:orphan','MEDIA_ASSET')",
            (identifiers["cleanup"],),
        )
        connection.commit()
        for table, column, key in (
            ("sessions", "session_id", "session"),
            ("runs", "run_id", "run"),
            ("messages", "message_id", "message"),
            ("content_upload_intents", "upload_id", "upload"),
            ("content_gc_queue", "cleanup_id", "cleanup"),
        ):
            cursor.execute(
                f"SELECT {column} FROM {table} WHERE platform_id='plt-local' AND tenant_id='tenant-a' AND {column}=%s",
                (identifiers[key],),
            )
            assert cursor.fetchone()[0] == identifiers[key]
    finally:
        cursor.close()
        connection.close()


def test_mysql_model_test_queue_claims_and_fences_terminal_result(mysql_catalog_database) -> None:
    from agent_platform.adapters.identity.fake import FakeIdentityProvider
    from agent_platform.domain.common import generate_database_id
    from agent_platform.domain.identity import IdentityContext
    from agent_platform.domain.model_routes import (
        ModelConnection,
        ModelTestKind,
        ModelTestRecord,
        ModelTestResult,
        ModelTestState,
        UsageStatus,
    )
    from agent_platform.infrastructure.mysql_catalog import MySqlCatalogRepository

    database, config = mysql_catalog_database
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    repository = MySqlCatalogRepository(lambda: mysql.connect(**config, database=database))
    connection = ModelConnection(
        connection_id=generate_database_id(),
        tenant_id=identity.tenant_id,
        name="Worker queue connection",
        provider_kind="openai-compatible",
        protocol="openai-chat-completions",
        endpoint="http://localhost:63072/v1",
    )
    repository.save_model_connection(identity, connection)
    queued = ModelTestRecord(
        test_id=generate_database_id(),
        tenant_id=identity.tenant_id,
        connection_id=connection.connection_id,
        test_kind=ModelTestKind.CONNECTION,
        state=ModelTestState.QUEUED,
        snapshot={"operation": "SYNC_MODELS"},
        created_by_user_id=identity.user_id,
    )
    repository.save_model_test_record(identity, queued)

    claimed = repository.claim_next_model_test(
        identity.platform_id, "worker-model-test", lease_seconds=30
    )
    assert claimed is not None
    assert claimed.test_id == queued.test_id
    assert claimed.state is ModelTestState.RUNNING
    assert claimed.worker_id == "worker-model-test"
    assert claimed.fencing_token == 1
    worker_identity = IdentityContext(
        identity.platform_id,
        identity.tenant_id,
        "worker-model-test",
        frozenset(),
        auth_source="worker",
    )
    assert repository.heartbeat_model_test(
        worker_identity,
        claimed.test_id,
        "worker-model-test",
        claimed.fencing_token,
        lease_seconds=30,
    )
    result = ModelTestResult(
        ModelTestState.SUCCEEDED,
        latency_ms=31,
        provider_request_id="catalog-request-1",
        usage_status=UsageStatus.EXACT,
    )
    assert not repository.finish_model_test(
        worker_identity,
        replace(claimed, fencing_token=claimed.fencing_token + 1),
        result,
    )
    assert repository.finish_model_test(worker_identity, claimed, result)
    finished = repository.get_model_test_record(identity, queued.test_id)
    assert finished.state is ModelTestState.SUCCEEDED
    assert finished.provider_request_id == "catalog-request-1"
    assert finished.lease_expires_at is None
    assert not repository.finish_model_test(worker_identity, claimed, result)

    uncertain = replace(
        queued,
        test_id=generate_database_id(),
        state=ModelTestState.QUEUED,
        worker_id=None,
        fencing_token=0,
        lease_expires_at=None,
        started_at=None,
        finished_at=None,
    )
    repository.save_model_test_record(identity, uncertain)
    old_claim = repository.claim_next_model_test(
        identity.platform_id, "worker-expired", lease_seconds=30
    )
    assert old_claim is not None and old_claim.test_id == uncertain.test_id
    connection = mysql.connect(**config, database=database)
    cursor = connection.cursor()
    try:
        cursor.execute(
            "UPDATE model_test_records SET lease_expires_at=UTC_TIMESTAMP(6)-INTERVAL 1 SECOND "
            "WHERE platform_id=%s AND tenant_id=%s AND test_id=%s",
            (identity.platform_id, identity.tenant_id, uncertain.test_id),
        )
        connection.commit()
    finally:
        cursor.close()
        connection.close()
    assert repository.claim_next_model_test(
        identity.platform_id, "worker-replacement", lease_seconds=30
    ) is None
    expired = repository.get_model_test_record(identity, uncertain.test_id)
    assert expired.state is ModelTestState.UNKNOWN
    assert expired.error_category == "WORKER_LEASE_EXPIRED"
    assert expired.fencing_token == old_claim.fencing_token + 1
