from __future__ import annotations

from fastapi.testclient import TestClient

from agent_platform.api.app import create_app


def _authorized_route(client: TestClient, headers: dict[str, str]) -> str:
    connection = client.post(
        "/api/v1/model-connections",
        headers=headers,
        json={
            "name": "本地模型连接",
            "provider_kind": "openai-compatible",
            "protocol": "openai-chat-completions",
            "endpoint": "http://model.local/v1",
            "catalog_mode": "MANUAL",
        },
    ).json()
    client.post(
        f"/api/v1/model-connections/{connection['connection_id']}/models",
        headers=headers,
        json={"model_id": "model-a", "display_name": "模型 A"},
    )
    route = client.post(
        "/api/v1/model-routes",
        headers=headers,
        json={
            "alias": "route-a",
            "connection_id": connection["connection_id"],
            "catalog_model_id": "model-a",
            "model_name": "model-a",
        },
    )
    assert route.status_code == 201
    return route.json()["route_id"]


def _published_employee(client: TestClient, headers: dict[str, str]) -> str:
    skill = client.post(
        "/api/v1/skills",
        headers=headers,
        json={"name": "配置测试 Skill", "skill_type": "instruction", "instruction": "执行任务"},
    )
    assert skill.status_code == 201
    assert client.post(f"/api/v1/skills/{skill.json()['id']}/publish", headers=headers).status_code == 200
    employee = client.post(
        "/api/v1/digital-employees",
        headers=headers,
        json={"name": "配置测试员工", "role_name": "分析员", "description": "运行配置验收"},
    )
    assert employee.status_code == 201
    employee_id = employee.json()["id"]
    version = client.post(
        f"/api/v1/digital-employees/{employee_id}/versions",
        headers=headers,
        json={"system_prompt": "处理请求", "skill_ids": [skill.json()["id"]]},
    )
    assert version.status_code == 201
    assert client.post(
        f"/api/v1/digital-employees/{employee_id}/versions/1/publish", headers=headers
    ).status_code == 200
    return employee_id


def test_new_deployment_requires_configuration_and_runtime_revisions_are_concurrent() -> None:
    client = TestClient(create_app())
    admin = {"X-Fake-User": "admin-local"}
    member = {"X-Fake-User": "member-local"}
    other_admin = {"X-Fake-User": "admin-other"}
    employee_id = _published_employee(client, admin)
    profile = client.post(
        "/api/v1/execution-profiles", headers=admin, json={"engine_name": "codex_cli"}
    )
    assert profile.status_code == 201
    route_id = _authorized_route(client, admin)
    deployment = client.post(
        "/api/v1/deployments",
        headers=admin,
        json={"employee_id": employee_id, "version": 1},
    )
    assert deployment.status_code == 201
    deployment_id = deployment.json()["id"]
    assert deployment.json()["status"] == "PENDING_CONFIGURATION"
    assert deployment.json()["runtime_config_version"] is None

    pending_session = client.post(
        "/api/v1/sessions", headers=admin, json={"deployment_id": deployment_id}
    )
    assert pending_session.status_code == 409
    assert pending_session.json()["code"] == "RUNTIME_CONFIGURATION_REQUIRED"

    first = client.put(
        f"/api/v1/deployments/{deployment_id}/runtime-config",
        headers=admin,
        json={
            "expected_config_version": None,
            "mode": "OVERRIDE",
            "execution_profile_id": profile.json()["id"],
            "execution_profile_version": profile.json()["version"],
            "model_route_id": None,
        },
    )
    assert first.status_code == 200
    assert first.json()["revision"]["config_version"] == 1
    assert first.json()["revision"]["execution_profile_id"] == profile.json()["id"]
    assert first.json()["revision"]["model_route_id"] is None
    assert first.json()["deployment_status"] == "ACTIVE"

    second = client.put(
        f"/api/v1/deployments/{deployment_id}/runtime-config",
        headers=admin,
        json={
            "expected_config_version": 1,
            "mode": "OVERRIDE",
            "execution_profile_id": None,
            "execution_profile_version": None,
            "model_route_id": route_id,
        },
    )
    assert second.status_code == 200
    assert second.json()["revision"]["config_version"] == 2
    assert second.json()["revision"]["execution_profile_id"] is None
    assert second.json()["revision"]["model_route_id"] == route_id

    inherited = client.put(
        f"/api/v1/deployments/{deployment_id}/runtime-config",
        headers=admin,
        json={"expected_config_version": 2, "mode": "INHERIT"},
    )
    assert inherited.status_code == 200
    assert inherited.json()["revision"]["config_version"] == 3
    assert inherited.json()["revision"]["execution_profile_id"] is None
    assert inherited.json()["revision"]["model_route_id"] is None

    stale = client.put(
        f"/api/v1/deployments/{deployment_id}/runtime-config",
        headers=admin,
        json={
            "expected_config_version": 2,
            "mode": "INHERIT",
        },
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "RUNTIME_CONFIG_REVISION_CONFLICT"

    assert client.put(
        f"/api/v1/deployments/{deployment_id}/runtime-config",
        headers=member,
        json={"expected_config_version": 3, "mode": "INHERIT"},
    ).status_code == 403
    assert client.get(
        f"/api/v1/deployments/{deployment_id}/runtime-config", headers=other_admin
    ).status_code == 404
    current = client.get(
        f"/api/v1/deployments/{deployment_id}/runtime-config", headers=member
    )
    assert current.status_code == 200
    assert current.json()["runtime_config_version"] == 3
    assert "secret_ref" not in current.text

    unsupported_environment_overrides = (
        {"workspace_profile_id": "workspace-1", "workspace_profile_version": 1},
        {"sandbox_profile_id": "sandbox-1", "sandbox_profile_version": 1},
        {"approval_policy": {"require_approval_for": ["all"]}},
    )
    for unsupported in unsupported_environment_overrides:
        response = client.put(
            f"/api/v1/deployments/{deployment_id}/runtime-config",
            headers=admin,
            json={
                "expected_config_version": 3,
                "mode": "OVERRIDE",
                "execution_profile_id": None,
                "execution_profile_version": None,
                "model_route_id": route_id,
                **unsupported,
            },
        )
        assert response.status_code == 409
        assert response.json()["code"] == "ENGINE_CAPABILITY_UNSUPPORTED"
    assert client.get(
        f"/api/v1/deployments/{deployment_id}/runtime-config", headers=admin
    ).json()["runtime_config_version"] == 3
    audit_types = {
        event.event_type for event in client.app.state.catalog_service.repository.audit_events
    }
    assert "DEPLOYMENT_RUNTIME_CONFIG_UPDATED" in audit_types
    client.close()
