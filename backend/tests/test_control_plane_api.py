from fastapi.testclient import TestClient

from agent_platform.api.app import create_app

HEADERS = {"X-Fake-User": "admin-local"}


def test_api_runs_publish_deploy_fake_runtime_and_trace() -> None:
    client = TestClient(create_app())

    tool = client.post(
        "/api/v1/tools",
        headers=HEADERS,
        json={"name": "echo", "description": "回显文本", "input_schema": {"type": "object"}},
    )
    assert tool.status_code == 201
    tool_id = tool.json()["id"]
    assert client.post(f"/api/v1/tools/{tool_id}/publish", headers=HEADERS).status_code == 200

    skill = client.post(
        "/api/v1/skills",
        headers=HEADERS,
        json={
            "name": "回声技能",
            "skill_type": "tool-bundle",
            "instruction": "使用 echo",
            "allowed_tool_ids": [tool_id],
        },
    )
    assert skill.status_code == 201
    skill_id = skill.json()["id"]
    assert client.post(f"/api/v1/skills/{skill_id}/publish", headers=HEADERS).status_code == 200

    employee = client.post(
        "/api/v1/digital-employees",
        headers=HEADERS,
        json={"name": "行政助手", "role_name": "行政", "description": "处理事务"},
    )
    assert employee.status_code == 201
    employee_id = employee.json()["id"]
    version = client.post(
        f"/api/v1/digital-employees/{employee_id}/versions",
        headers=HEADERS,
        json={
            "system_prompt": "你是行政助手",
            "skill_ids": [skill_id],
            "tool_ids": [tool_id],
        },
    )
    assert version.status_code == 201
    version_number = version.json()["version"]
    published = client.post(
        f"/api/v1/digital-employees/{employee_id}/versions/{version_number}/publish",
        headers=HEADERS,
    )
    assert published.status_code == 200

    deployment = client.post(
        "/api/v1/deployments",
        headers=HEADERS,
        json={"employee_id": employee_id, "version": version_number},
    )
    assert deployment.status_code == 201
    deployment_id = deployment.json()["id"]
    session = client.post(
        "/api/v1/sessions", headers=HEADERS, json={"deployment_id": deployment_id}
    )
    assert session.status_code == 201
    session_id = session.json()["id"]

    run = client.post(
        f"/api/v1/sessions/{session_id}/runs",
        headers=HEADERS,
        json={"prompt": "echo: hello", "idempotency_key": "run-1"},
    )
    assert run.status_code == 201
    run_body = run.json()
    assert run_body["status"] == "succeeded"
    trace = client.get(f"/api/v1/runs/{run_body['id']}/trace", headers=HEADERS)
    assert trace.status_code == 200
    trace_body = trace.json()
    assert trace_body["run"]["result"] == "fake: echo: hello"
    assert {span["span_type"] for span in trace_body["spans"]} >= {
        "session",
        "run",
        "skill",
        "tool",
        "runtime",
        "model",
        "result",
    }


def test_api_requires_identity_and_hides_cross_tenant_resources() -> None:
    client = TestClient(create_app())
    assert client.post("/api/v1/tools", json={"name": "echo"}).status_code == 401

    response = client.get("/api/v1/runs/run-missing", headers={"X-Fake-User": "admin-other"})
    assert response.status_code == 404
    assert response.json()["code"] == "RESOURCE_NOT_FOUND"


def test_api_rejects_idempotency_payload_conflict() -> None:
    client = TestClient(create_app())
    tool = client.post(
        "/api/v1/tools", headers=HEADERS, json={"name": "echo", "description": "回显"}
    ).json()
    client.post(f"/api/v1/tools/{tool['id']}/publish", headers=HEADERS)
    skill = client.post(
        "/api/v1/skills",
        headers=HEADERS,
        json={"name": "技能", "skill_type": "instruction", "instruction": "回答"},
    ).json()
    client.post(f"/api/v1/skills/{skill['id']}/publish", headers=HEADERS)
    employee = client.post(
        "/api/v1/digital-employees", headers=HEADERS, json={"name": "助手", "role_name": "支持", "description": "支持"}
    ).json()
    version = client.post(
        f"/api/v1/digital-employees/{employee['id']}/versions",
        headers=HEADERS,
        json={"system_prompt": "prompt", "skill_ids": [skill["id"]], "tool_ids": [tool["id"]]},
    ).json()
    client.post(
        f"/api/v1/digital-employees/{employee['id']}/versions/{version['version']}/publish",
        headers=HEADERS,
    )
    deployment = client.post(
        "/api/v1/deployments", headers=HEADERS, json={"employee_id": employee["id"], "version": 1}
    ).json()
    session = client.post(
        "/api/v1/sessions", headers=HEADERS, json={"deployment_id": deployment["id"]}
    ).json()
    first = client.post(
        f"/api/v1/sessions/{session['id']}/runs",
        headers=HEADERS,
        json={"prompt": "hello", "idempotency_key": "same"},
    )
    assert first.status_code == 201
    conflict = client.post(
        f"/api/v1/sessions/{session['id']}/runs",
        headers=HEADERS,
        json={"prompt": "changed", "idempotency_key": "same"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"


def test_api_body_cannot_switch_tenant() -> None:
    client = TestClient(create_app())
    response = client.post(
        "/api/v1/tools",
        headers=HEADERS,
        json={"tenant_id": "tenant-b", "name": "echo", "description": "回显"},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "AUTHORIZATION_DENIED"


def test_api_lists_catalog_and_rolls_back_deployment() -> None:
    client = TestClient(create_app())
    headers = HEADERS
    tool = client.post("/api/v1/tools", headers=headers, json={"name": "echo", "description": "回显"}).json()
    client.post(f"/api/v1/tools/{tool['id']}/publish", headers=headers)
    skill = client.post("/api/v1/skills", headers=headers, json={"name": "技能", "skill_type": "instruction", "instruction": "回答"}).json()
    client.post(f"/api/v1/skills/{skill['id']}/publish", headers=headers)
    employee = client.post("/api/v1/digital-employees", headers=headers, json={"name": "助手", "role_name": "支持", "description": "支持"}).json()
    client.post(f"/api/v1/digital-employees/{employee['id']}/versions", headers=headers, json={"system_prompt": "p", "skill_ids": [skill['id']]}).json()
    client.post(f"/api/v1/digital-employees/{employee['id']}/versions/1/publish", headers=headers)
    deployment = client.post("/api/v1/deployments", headers=headers, json={"employee_id": employee['id'], "version": 1}).json()
    client.post(f"/api/v1/digital-employees/{employee['id']}/versions", headers=headers, json={"system_prompt": "p2", "skill_ids": [skill['id']]}).json()
    client.post(f"/api/v1/digital-employees/{employee['id']}/versions/2/publish", headers=headers)
    rollback = client.post(f"/api/v1/deployments/{deployment['id']}/rollback", headers=headers, json={"employee_id": employee['id'], "version": 1})
    assert rollback.status_code == 200
    assert rollback.json()["version"] == 1
    assert client.get("/api/v1/tools", headers=headers).status_code == 200
    assert client.get("/api/v1/skills", headers=headers).status_code == 200
