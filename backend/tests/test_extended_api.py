from fastapi.testclient import TestClient

from agent_platform.api.app import create_app
from tests._runtime_config_helpers import configure_api_deployment


def test_model_route_and_conversation_api_contracts() -> None:
    client = TestClient(create_app())
    headers = {"X-Fake-User": "admin-local"}
    route = client.post("/api/v1/model-routes", headers=headers, json={"alias": "fake", "model_name": "fake-model"})
    assert route.status_code == 201
    assert route.json()["profile"]["secret_ref"] is None

    conversation = client.post("/api/v1/conversations", headers=headers, json={"title": "群聊", "is_group": True})
    assert conversation.status_code == 201
    conversation_id = conversation.json()["id"]
    client.post(
        f"/api/v1/conversations/{conversation_id}/bindings",
        headers=headers,
        json={"employee_id": "employee-a", "employee_name": "行政助手", "session_id": "session-a"},
    )
    ordinary = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        headers=headers,
        json={"sender_id": "member-local", "text": "普通消息"},
    ).json()
    routed = client.post(
        f"/api/v1/conversations/{conversation_id}/messages/{ordinary['id']}/route", headers=headers
    )
    assert routed.status_code == 200
    assert routed.json()["session_ids"] == []


def test_model_route_update_rejects_a_stale_expected_version() -> None:
    client = TestClient(create_app())
    admin = {"X-Fake-User": "admin-local"}
    created = client.post(
        "/api/v1/model-routes",
        headers=admin,
        json={"alias": "route-lifecycle", "model_name": "model-a", "provider": "fake"},
    )
    assert created.status_code == 201
    route_id = created.json()["route_id"]

    updated = client.put(
        f"/api/v1/model-routes/{route_id}",
        headers=admin,
        json={"expected_route_version": 1, "alias": "route-lifecycle-v2"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["route_version"] == 2

    stale = client.put(
        f"/api/v1/model-routes/{route_id}",
        headers=admin,
        json={"expected_route_version": 1, "alias": "stale-route-edit"},
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "MODEL_ROUTE_VERSION_CONFLICT"


def test_model_route_delete_revokes_unreferenced_route_and_is_idempotent() -> None:
    client = TestClient(create_app())
    admin = {"X-Fake-User": "admin-local"}
    member = {"X-Fake-User": "member-local"}
    created = client.post(
        "/api/v1/model-routes",
        headers=admin,
        json={"alias": "revoke-me", "model_name": "model-b", "provider": "fake"},
    )
    assert created.status_code == 201
    route_id = created.json()["route_id"]

    denied = client.delete(
        f"/api/v1/model-routes/{route_id}",
        headers=member,
        params={"expected_route_version": 1},
    )
    assert denied.status_code == 403

    revoked = client.delete(
        f"/api/v1/model-routes/{route_id}",
        headers=admin,
        params={"expected_route_version": 1},
    )
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["status"] == "REVOKED"
    assert revoked.json()["route_version"] == 2
    assert client.get(f"/api/v1/model-routes/{route_id}", headers=admin).json()["status"] == "REVOKED"
    assert all(
        route["route_id"] != route_id
        for route in client.get("/api/v1/model-routes/selectable", headers=admin).json()["items"]
    )

    repeated = client.delete(
        f"/api/v1/model-routes/{route_id}",
        headers=admin,
        params={"expected_route_version": 1},
    )
    assert repeated.status_code == 200
    assert repeated.json()["route_version"] == 2


def test_model_route_delete_rejects_route_used_by_a_tenant_default() -> None:
    client = TestClient(create_app())
    admin = {"X-Fake-User": "admin-local"}
    created = client.post(
        "/api/v1/model-routes",
        headers=admin,
        json={"alias": "bound-route", "model_name": "model-c", "provider": "fake"},
    )
    assert created.status_code == 201
    route_id = created.json()["route_id"]
    bound = client.put(
        "/api/v1/model-route-bindings/tenant",
        headers=admin,
        json={"route_id": route_id},
    )
    assert bound.status_code == 200, bound.text
    in_use = client.delete(
        f"/api/v1/model-routes/{route_id}",
        headers=admin,
        params={"expected_route_version": 1},
    )
    assert in_use.status_code == 409
    assert in_use.json()["code"] == "MODEL_ROUTE_IN_USE"


def test_disabled_deployment_cannot_reenable_with_a_revoked_model_route() -> None:
    client = TestClient(create_app())
    admin = {"X-Fake-User": "admin-local"}
    route = client.post(
        "/api/v1/model-routes",
        headers=admin,
        json={"alias": "deployment-route", "model_name": "model-deploy", "provider": "fake"},
    )
    assert route.status_code == 201
    skill = client.post(
        "/api/v1/skills",
        headers=admin,
        json={"name": "路由重新上线验收 Skill", "skill_type": "instruction", "instruction": "执行"},
    ).json()
    assert client.post(f"/api/v1/skills/{skill['id']}/publish", headers=admin).status_code == 200
    employee = client.post(
        "/api/v1/digital-employees",
        headers=admin,
        json={"name": "路由重新上线验收员工", "role_name": "助手", "description": "停用后撤销路由"},
    ).json()
    version = client.post(
        f"/api/v1/digital-employees/{employee['id']}/versions",
        headers=admin,
        json={"system_prompt": "运行", "skill_ids": [skill["id"]]},
    )
    assert version.status_code == 201
    assert client.post(
        f"/api/v1/digital-employees/{employee['id']}/versions/1/publish", headers=admin
    ).status_code == 200
    deployment = client.post(
        "/api/v1/deployments",
        headers=admin,
        json={"employee_id": employee["id"], "version": 1},
    ).json()
    active = configure_api_deployment(
        client, admin, deployment, route_id=route.json()["route_id"]
    )
    assert active["deployment_status"] == "ACTIVE"
    deployment_id = deployment["id"]

    assert client.post(f"/api/v1/deployments/{deployment_id}/disable", headers=admin).status_code == 200
    revoked = client.delete(
        f"/api/v1/model-routes/{route.json()['route_id']}",
        headers=admin,
        params={"expected_route_version": 1},
    )
    assert revoked.status_code == 200, revoked.text
    enabled = client.post(f"/api/v1/deployments/{deployment_id}/enable", headers=admin)
    assert enabled.status_code == 409
    assert enabled.json()["code"] == "DEPENDENCY_VALIDATION_ERROR"


def test_employee_conversation_list_returns_stable_pages_and_hides_other_tenants() -> None:
    client = TestClient(create_app())
    headers = {"X-Fake-User": "admin-local"}

    for index in range(2):
        conversation = client.post(
            "/api/v1/conversations",
            headers=headers,
            json={"title": f"工作流对话-{index}", "is_group": False},
        ).json()
        conversation_id = conversation["id"]
        binding = client.post(
            f"/api/v1/conversations/{conversation_id}/bindings",
            headers=headers,
            json={
                "employee_id": "employee-ds",
                "employee_name": "海豚调度数仓开发",
                "session_id": f"session-{index}",
            },
        )
        assert binding.status_code == 201
        message = client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            headers=headers,
            json={"sender_id": "admin-local", "text": f"ODS 到 DWD 任务-{index}"},
        ).json()
        assert message["conversation_id"] == conversation_id

    first = client.get(
        "/api/v1/conversations",
        headers=headers,
        params={"employee_id": "employee-ds", "limit": 1},
    )
    assert first.status_code == 200
    first_body = first.json()
    assert len(first_body["items"]) == 1
    assert first_body["items"][0]["employee"]["employee_id"] == "employee-ds"
    assert first_body["items"][0]["last_message"]["preview"].startswith("ODS 到 DWD")
    assert first_body["items"][0]["pending_approval_count"] == 0
    assert first_body["next_cursor"]

    second = client.get(
        "/api/v1/conversations",
        headers=headers,
        params={
            "employee_id": "employee-ds",
            "limit": 1,
            "cursor": first_body["next_cursor"],
        },
    )
    assert second.status_code == 200
    assert len(second.json()["items"]) == 1
    assert second.json()["items"][0]["conversation_id"] != first_body["items"][0]["conversation_id"]

    cross_tenant = client.get(
        "/api/v1/conversations",
        headers={"X-Fake-User": "admin-other"},
        params={"employee_id": "employee-ds"},
    )
    assert cross_tenant.status_code == 200
    assert cross_tenant.json()["items"] == []


def test_conversation_turn_is_queued_idempotent_and_keeps_sync_run_api_separate() -> None:
    client = TestClient(create_app())
    headers = {"X-Fake-User": "admin-local"}
    employee = client.post(
        "/api/v1/digital-employees",
        headers=headers,
        json={"name": "海豚调度员工", "role_name": "数仓开发", "description": "创建离线工作流"},
    ).json()
    skill = client.post(
        "/api/v1/skills",
        headers=headers,
        json={"name": "数仓任务技能", "skill_type": "instruction", "instruction": "先检查项目"},
    ).json()
    assert client.post(
        f"/api/v1/skills/{skill['id']}/publish", headers=headers
    ).status_code == 200
    version = client.post(
        f"/api/v1/digital-employees/{employee['id']}/versions",
        headers=headers,
        json={
            "system_prompt": "你是数仓开发员工",
            "skill_ids": [skill["id"]],
            "tool_ids": [],
        },
    ).json()
    assert client.post(
        f"/api/v1/digital-employees/{employee['id']}/versions/{version['version']}/publish",
        headers=headers,
    ).status_code == 200
    deployment = client.post(
        "/api/v1/deployments",
        headers=headers,
        json={"employee_id": employee["id"], "version": version["version"]},
    ).json()
    configure_api_deployment(client, headers, deployment)
    session = client.post(
        "/api/v1/sessions", headers=headers, json={"deployment_id": deployment["id"]}
    ).json()
    conversation = client.post(
        "/api/v1/conversations",
        headers=headers,
        json={"title": "工作流对话", "is_group": False},
    ).json()
    client.post(
        f"/api/v1/conversations/{conversation['id']}/bindings",
        headers=headers,
        json={
            "employee_id": employee["id"],
            "employee_name": employee["name"],
            "session_id": session["id"],
        },
    )
    body = {
        "employee_id": employee["id"],
        "text": "创建 ODS 到 DWD 离线工作流",
        "idempotency_key": "turn-1",
    }

    first = client.post(
        f"/api/v1/conversations/{conversation['id']}/turns",
        headers=headers,
        json=body,
    )
    assert first.status_code == 202
    first_body = first.json()
    assert first_body["status"] == "QUEUED"
    assert first_body["replayed"] is False
    assert first_body["message_id"]
    assert first_body["run_id"]

    replay = client.post(
        f"/api/v1/conversations/{conversation['id']}/turns",
        headers=headers,
        json=body,
    )
    assert replay.status_code == 202
    assert replay.json()["message_id"] == first_body["message_id"]
    assert replay.json()["run_id"] == first_body["run_id"]
    assert replay.json()["replayed"] is True

    conflict = client.post(
        f"/api/v1/conversations/{conversation['id']}/turns",
        headers=headers,
        json={**body, "text": "不同的工作流任务"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"

    messages = client.get(
        f"/api/v1/conversations/{conversation['id']}/messages", headers=headers
    )
    assert len(messages.json()["items"]) == 1
    listed = client.get(
        "/api/v1/conversations",
        headers=headers,
        params={"employee_id": employee["id"]},
    )
    assert listed.status_code == 200
    assert listed.json()["items"][0]["last_run"] == {
        "run_id": first_body["run_id"],
        "status": "QUEUED",
    }


def test_run_submitter_can_cancel_queued_run_but_another_user_cannot() -> None:
    client = TestClient(create_app())
    headers = {"X-Fake-User": "admin-local"}
    employee = client.post(
        "/api/v1/digital-employees",
        headers=headers,
        json={"name": "取消测试员工", "role_name": "数仓开发", "description": "测试取消"},
    ).json()
    skill = client.post(
        "/api/v1/skills",
        headers=headers,
        json={"name": "取消测试 Skill", "skill_type": "instruction", "instruction": "只读检查"},
    ).json()
    assert client.post(f"/api/v1/skills/{skill['id']}/publish", headers=headers).status_code == 200
    version = client.post(
        f"/api/v1/digital-employees/{employee['id']}/versions",
        headers=headers,
        json={"system_prompt": "数仓员工", "skill_ids": [skill["id"]], "tool_ids": []},
    ).json()
    assert client.post(
        f"/api/v1/digital-employees/{employee['id']}/versions/{version['version']}/publish",
        headers=headers,
    ).status_code == 200
    deployment = client.post(
        "/api/v1/deployments",
        headers=headers,
        json={"employee_id": employee["id"], "version": version["version"]},
    ).json()
    configure_api_deployment(client, headers, deployment)
    session = client.post(
        "/api/v1/sessions", headers=headers, json={"deployment_id": deployment["id"]}
    ).json()
    conversation = client.post(
        "/api/v1/conversations",
        headers=headers,
        json={"title": "可取消的工作", "is_group": False},
    ).json()
    client.post(
        f"/api/v1/conversations/{conversation['id']}/bindings",
        headers=headers,
        json={
            "employee_id": employee["id"],
            "employee_name": employee["name"],
            "session_id": session["id"],
        },
    )
    queued = client.post(
        f"/api/v1/conversations/{conversation['id']}/turns",
        headers=headers,
        json={
            "employee_id": employee["id"],
            "text": "取消这项任务",
            "idempotency_key": "cancel-me",
        },
    ).json()

    forbidden = client.post(
        f"/api/v1/runs/{queued['run_id']}/cancel",
        headers={"X-Fake-User": "member-local"},
    )
    assert forbidden.status_code == 403

    cancelled = client.post(
        f"/api/v1/runs/{queued['run_id']}/cancel", headers=headers
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert client.get(f"/api/v1/runs/{queued['run_id']}", headers=headers).json()["status"] == "cancelled"
