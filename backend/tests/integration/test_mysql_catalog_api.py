"""MySQL-backed FastAPI catalog API 与进程重启集成测试。"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

mysql = pytest.importorskip("mysql.connector")


@pytest.fixture
def mysql_catalog_api_database():
    if os.environ.get("AGENT_PLATFORM_MYSQL_TEST_ALLOW_CREATE_DATABASE") != "1":
        pytest.skip("set AGENT_PLATFORM_MYSQL_TEST_ALLOW_CREATE_DATABASE=1 for disposable MySQL integration")
    database = f"agent_platform_api_test_{secrets.token_hex(5)}"
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
        from agent_platform.infrastructure.seed_fake_identity import seed_fake_identity

        connection = mysql.connect(**config, database=database)
        try:
            seed_fake_identity(connection)
            seed_fake_identity(connection)
        finally:
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


def test_catalog_api_uses_mysql_and_survives_app_restart(tmp_path: Path, mysql_catalog_api_database) -> None:
    from agent_platform.adapters.identity.fake import FakeIdentityProvider
    from agent_platform.api.app import create_app
    from agent_platform.infrastructure.mysql_catalog import MySqlCatalogRepository
    from agent_platform.infrastructure.mysql_conversations import MySqlConversationRepository
    from agent_platform.infrastructure.mysql_execution import MySqlExecutionRepository
    from agent_platform.infrastructure.mysql_resource_center import MySqlResourceCenterRepository
    from agent_platform.infrastructure.mysql_storage import MySqlMediaAssetRepository
    from agent_platform.ports.storage import LocalObjectStore

    database, config = mysql_catalog_api_database
    headers = {"X-Fake-User": "admin-local"}

    def create_mysql_app():
        connection_factory = lambda: mysql.connect(**config, database=database)
        return create_app(
            catalog_repository=MySqlCatalogRepository(connection_factory),
            execution_repository=MySqlExecutionRepository(connection_factory),
            conversation_repository=MySqlConversationRepository(connection_factory),
            resource_center_repository=MySqlResourceCenterRepository(connection_factory),
            object_store=LocalObjectStore(
                tmp_path / "api-objects",
                metadata_repository=MySqlMediaAssetRepository(connection_factory),
            ),
        )

    client = TestClient(create_mysql_app())
    route = client.post(
        "/api/v1/model-routes",
        headers=headers,
        json={"alias": "local-fake", "model_name": "fake-model", "provider": "fake"},
    )
    assert route.status_code == 201
    route_id = route.json()["route_id"]
    profile = client.post(
        "/api/v1/execution-profiles",
        headers=headers,
        json={"engine_name": "fake", "model_alias": "local-fake", "model_route_id": route_id},
    )
    assert profile.status_code == 201
    profile_id = profile.json()["id"]
    tool = client.post("/api/v1/tools", headers=headers, json={"name": "echo", "description": "回显", "input_schema": {"type": "object"}})
    assert tool.status_code == 201
    tool_id = tool.json()["id"]
    assert client.post(f"/api/v1/tools/{tool_id}/publish", headers=headers).status_code == 200

    skill = client.post(
        "/api/v1/skills",
        headers=headers,
        json={"name": "回声", "skill_type": "tool-bundle", "instruction": "调用 echo", "allowed_tool_ids": [tool_id]},
    )
    assert skill.status_code == 201
    skill_id = skill.json()["id"]
    assert client.post(f"/api/v1/skills/{skill_id}/publish", headers=headers).status_code == 200

    employee = client.post(
        "/api/v1/digital-employees",
        headers=headers,
        json={"name": "助手", "role_name": "支持", "description": "本地助手"},
    )
    assert employee.status_code == 201
    employee_id = employee.json()["id"]
    version = client.post(
        f"/api/v1/digital-employees/{employee_id}/versions",
        headers=headers,
        json={
            "system_prompt": "你是助手",
            "skill_ids": [skill_id],
            "tool_ids": [tool_id],
            "execution_profile_id": profile_id,
        },
    )
    assert version.status_code == 201
    assert client.post(f"/api/v1/digital-employees/{employee_id}/versions/1/publish", headers=headers).status_code == 200
    deployment = client.post(
        "/api/v1/deployments", headers=headers, json={"employee_id": employee_id, "version": 1}
    )
    assert deployment.status_code == 201
    deployment_id = deployment.json()["id"]

    session = client.post("/api/v1/sessions", headers=headers, json={"deployment_id": deployment_id})
    assert session.status_code == 201
    session_id = session.json()["id"]
    run = client.post(
        f"/api/v1/sessions/{session_id}/runs",
        headers=headers,
        json={"prompt": "你好", "idempotency_key": "full-loop-run-1"},
    )
    assert run.status_code == 201
    run_id = run.json()["id"]
    assert run.json()["result"].startswith("fake:")
    assert run.json()["result_ref"].startswith("object:plt-local:tenant-a:")

    conversation = client.post(
        "/api/v1/conversations", headers=headers, json={"title": "支持会话"}
    ).json()
    conversation_id = conversation["id"]
    binding = client.post(
        f"/api/v1/conversations/{conversation_id}/bindings",
        headers=headers,
        json={"employee_id": employee_id, "employee_name": "助手", "session_id": session_id},
    )
    assert binding.status_code == 201
    message = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        headers=headers,
        json={"sender_id": "admin-local", "text": "消息正文存入本地对象"},
    )
    assert message.status_code == 201
    message_id = message.json()["id"]
    client.close()

    restarted = TestClient(create_mysql_app())
    assert restarted.get("/api/v1/tools", headers=headers).json()["items"][0]["id"] == tool_id
    assert restarted.get("/api/v1/skills", headers=headers).json()["items"][0]["id"] == skill_id
    assert any(item["id"] == employee_id for item in restarted.get("/api/v1/digital-employees", headers=headers).json()["items"])
    assert any(item["id"] == deployment_id for item in restarted.get("/api/v1/deployments", headers=headers).json()["items"])
    assert restarted.get("/api/v1/model-routes", headers=headers).json()["items"][0]["route_id"] == route_id
    assert any(
        item["id"] == profile_id
        for item in restarted.get("/api/v1/execution-profiles", headers=headers).json()["items"]
    )
    recovered_run = restarted.get(f"/api/v1/runs/{run_id}", headers=headers)
    assert recovered_run.status_code == 200
    assert recovered_run.json()["result"].startswith("fake:")
    assert recovered_run.json()["result_ref"] == run.json()["result_ref"]
    trace = restarted.get(f"/api/v1/runs/{run_id}/trace", headers=headers)
    assert trace.status_code == 200
    assert trace.json()["usage"]["total_tokens"] > 0
    assert any(span["span_type"] == "result" for span in trace.json()["spans"])
    repeated = restarted.post(
        f"/api/v1/sessions/{session_id}/runs",
        headers=headers,
        json={"prompt": "你好", "idempotency_key": "full-loop-run-1"},
    )
    assert repeated.json()["id"] == run_id
    assert repeated.json()["result"].startswith("fake:")
    messages = restarted.get(
        f"/api/v1/conversations/{conversation_id}/messages", headers=headers
    )
    assert messages.status_code == 200
    assert messages.json()["items"][0]["id"] == message_id
    assert messages.json()["items"][0]["text"] == "消息正文存入本地对象"

    other = FakeIdentityProvider().resolve_identity("admin-other")
    other_headers = {"X-Fake-User": other.user_id}
    assert restarted.get("/api/v1/deployments", headers=other_headers).json()["items"] == []
    assert restarted.get("/api/v1/model-routes", headers=other_headers).json()["items"] == []
    assert restarted.get("/api/v1/execution-profiles", headers=other_headers).json()["items"] == []
    assert restarted.post(f"/api/v1/tools/{tool_id}/publish", headers=other_headers).status_code == 404
    assert restarted.get(f"/api/v1/runs/{run_id}", headers=other_headers).status_code == 404
    assert restarted.get(
        f"/api/v1/conversations/{conversation_id}/messages", headers=other_headers
    ).status_code == 404
    forged = restarted.post(
        "/api/v1/tools",
        headers=headers,
        json={"tenant_id": "tenant-b", "name": "forged", "description": "越权"},
    )
    assert forged.status_code == 403


def test_mysql_employee_search_cursor_and_version_detail_are_tenant_scoped(
    mysql_catalog_api_database,
) -> None:
    from agent_platform.api.app import create_app
    from agent_platform.infrastructure.mysql_catalog import MySqlCatalogRepository

    database, config = mysql_catalog_api_database
    connection_factory = lambda: mysql.connect(**config, database=database)
    client = TestClient(
        create_app(mode="fake", catalog_repository=MySqlCatalogRepository(connection_factory))
    )
    headers = {"X-Fake-User": "admin-local"}
    created = []
    for name in ("合同助手", "工单助手", "工单助手"):
        response = client.post(
            "/api/v1/digital-employees",
            headers=headers,
            json={"name": name, "role_name": "客服", "description": "服务业务"},
        )
        assert response.status_code == 201
        created.append(response.json()["id"])

    first = client.get("/api/v1/digital-employees", headers=headers, params={"limit": 2})
    assert first.status_code == 200
    assert first.json()["next_cursor"]
    second = client.get(
        "/api/v1/digital-employees",
        headers=headers,
        params={"limit": 2, "cursor": first.json()["next_cursor"]},
    )
    assert second.status_code == 200
    rows = first.json()["items"] + second.json()["items"]
    assert [(row["name"].casefold(), row["id"]) for row in rows] == sorted(
        (row["name"].casefold(), row["id"]) for row in rows
    )
    assert set(created) <= {row["id"] for row in rows}
    filtered = client.get(
        "/api/v1/digital-employees",
        headers=headers,
        params={"keyword": "工单", "status": "DRAFT"},
    )
    assert len(filtered.json()["items"]) == 2

    skill = client.post(
        "/api/v1/skills",
        headers=headers,
        json={"name": "员工详情 Skill", "skill_type": "instruction", "instruction": "按流程处理"},
    )
    target = created[0]
    version = client.post(
        f"/api/v1/digital-employees/{target}/versions",
        headers=headers,
        json={"system_prompt": "详情版本指令", "skill_ids": [skill.json()["id"]]},
    )
    assert version.status_code == 201
    assert client.get(
        f"/api/v1/digital-employees/{target}/versions/1", headers=headers
    ).json()["skill_version_refs"] == [{"skill_id": skill.json()["id"], "version": 1}]
    assert client.get(
        f"/api/v1/digital-employees/{target}", headers={"X-Fake-User": "admin-other"}
    ).status_code == 404


def test_mysql_employee_patch_draft_put_archive_and_admin_write_scope(
    mysql_catalog_api_database,
) -> None:
    from agent_platform.api.app import create_app
    from agent_platform.infrastructure.mysql_catalog import MySqlCatalogRepository

    database, config = mysql_catalog_api_database
    connection_factory = lambda: mysql.connect(**config, database=database)
    client = TestClient(
        create_app(mode="fake", catalog_repository=MySqlCatalogRepository(connection_factory))
    )
    admin = {"X-Fake-User": "admin-local"}
    member = {"X-Fake-User": "member-local"}
    skill = client.post(
        "/api/v1/skills",
        headers=admin,
        json={"name": "员工管理 Skill", "skill_type": "instruction", "instruction": "遵循发布规则"},
    )
    assert skill.status_code == 201
    assert client.post(f"/api/v1/skills/{skill.json()['id']}/publish", headers=admin).status_code == 200
    employee = client.post(
        "/api/v1/digital-employees",
        headers=admin,
        json={"name": "管理测试员工", "role_name": "助手", "description": "员工基础资料"},
    )
    employee_id = employee.json()["id"]
    version = client.post(
        f"/api/v1/digital-employees/{employee_id}/versions",
        headers=admin,
        json={"system_prompt": "初始草稿", "skill_ids": [skill.json()["id"]]},
    )
    assert version.status_code == 201
    draft = client.put(
        f"/api/v1/digital-employees/{employee_id}/versions/1",
        headers=admin,
        json={
            "system_prompt": "保存后的草稿",
            "skill_version_refs": [{"resource_id": skill.json()["id"], "version": 1}],
            "execution_profile_id": "profile-fake",
            "execution_profile_version": 1,
        },
    )
    assert draft.status_code == 200
    assert draft.json()["system_prompt"] == "保存后的草稿"
    assert client.post(
        f"/api/v1/digital-employees/{employee_id}/versions/1/publish", headers=admin
    ).status_code == 200
    immutable = client.put(
        f"/api/v1/digital-employees/{employee_id}/versions/1",
        headers=admin,
        json={
            "system_prompt": "不允许覆盖已发布版本",
            "skill_version_refs": [{"resource_id": skill.json()["id"], "version": 1}],
        },
    )
    assert immutable.status_code == 409

    patched = client.patch(
        f"/api/v1/digital-employees/{employee_id}",
        headers=admin,
        json={"name": "变更员工名称"},
    )
    assert patched.status_code == 200
    assert patched.json()["name"] == "变更员工名称"
    assert client.patch(
        f"/api/v1/digital-employees/{employee_id}",
        headers=member,
        json={"name": "成员越权"},
    ).status_code == 403
    assert client.post(
        f"/api/v1/digital-employees/{employee_id}/archive", headers=member
    ).status_code == 403
    archived = client.post(f"/api/v1/digital-employees/{employee_id}/archive", headers=admin)
    assert archived.status_code == 200
    assert archived.json()["status"] == "ARCHIVED"
    assert client.post(
        "/api/v1/digital-employees",
        headers=member,
        json={"name": "成员员工", "role_name": "助手", "description": "不应创建"},
    ).status_code == 403


def test_environment_mysql_app_wires_pool_without_implicit_seed(
    tmp_path: Path, mysql_catalog_api_database, monkeypatch
) -> None:
    from agent_platform.api.app import create_app_from_environment

    database, config = mysql_catalog_api_database
    connection = mysql.connect(**config, database=database)
    cursor = connection.cursor()
    try:
        cursor.execute("SELECT COUNT(*) FROM users")
        users_before_startup = cursor.fetchone()[0]
    finally:
        cursor.close()
        connection.close()
    monkeypatch.setenv("AGENT_PLATFORM_MODE", "mysql")
    monkeypatch.setenv("AGENT_PLATFORM_IDENTITY_PROVIDER", "fake")
    monkeypatch.setenv("MYSQL_HOST", config["host"])
    monkeypatch.setenv("MYSQL_PORT", str(config["port"]))
    monkeypatch.setenv("MYSQL_DATABASE", database)
    monkeypatch.setenv("MYSQL_USER", config["user"])
    monkeypatch.setenv("MYSQL_PASSWORD", config["password"])
    monkeypatch.setenv("AGENT_PLATFORM_DATA_ROOT", str(tmp_path / "external-data"))

    with TestClient(create_app_from_environment()) as client:
        assert client.get("/api/health").json() == {"status": "ok"}
        assert client.post(
            "/api/v1/tools",
            headers={"X-Fake-User": "admin-local"},
            json={"name": "pool-check", "description": "池接线检查"},
        ).status_code == 201

    with TestClient(create_app_from_environment()) as restarted:
        tools = restarted.get("/api/v1/tools", headers={"X-Fake-User": "admin-local"}).json()["items"]
        assert any(item["name"] == "pool-check" for item in tools)
    connection = mysql.connect(**config, database=database)
    cursor = connection.cursor()
    try:
        cursor.execute("SELECT COUNT(*) FROM users")
        assert cursor.fetchone()[0] == users_before_startup
    finally:
        cursor.close()
        connection.close()
