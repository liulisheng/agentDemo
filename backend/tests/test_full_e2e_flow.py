from fastapi.testclient import TestClient

from agent_platform.api.app import create_app
from tests._runtime_config_helpers import configure_api_deployment


def test_full_fake_flow_includes_catalog_runtime_trace_and_channel_contracts() -> None:
    client = TestClient(create_app())
    headers = {"X-Fake-User": "admin-local"}
    tool = client.post("/api/v1/tools", headers=headers, json={"name": "echo", "description": "回显"}).json()
    client.post(f"/api/v1/tools/{tool['id']}/publish", headers=headers)
    skill = client.post(
        "/api/v1/skills", headers=headers,
        json={"name": "回声", "skill_type": "tool-bundle", "instruction": "使用 echo", "allowed_tool_ids": [tool["id"]]},
    ).json()
    client.post(f"/api/v1/skills/{skill['id']}/publish", headers=headers)
    employee = client.post(
        "/api/v1/digital-employees", headers=headers,
        json={"name": "助手", "role_name": "支持", "description": "支持"},
    ).json()
    version = client.post(
        f"/api/v1/digital-employees/{employee['id']}/versions", headers=headers,
        json={"system_prompt": "prompt", "skill_ids": [skill["id"]], "tool_ids": [tool["id"]]},
    ).json()
    client.post(f"/api/v1/digital-employees/{employee['id']}/versions/1/publish", headers=headers)
    deployment = client.post("/api/v1/deployments", headers=headers, json={"employee_id": employee["id"], "version": version["version"]}).json()
    configure_api_deployment(client, headers, deployment)
    session = client.post("/api/v1/sessions", headers=headers, json={"deployment_id": deployment["id"]}).json()
    run = client.post(f"/api/v1/sessions/{session['id']}/runs", headers=headers, json={"prompt": "echo: e2e", "idempotency_key": "e2e-1"}).json()
    trace = client.get(f"/api/v1/runs/{run['id']}/trace", headers=headers).json()
    assert run["status"] == "succeeded"
    assert trace["run"]["snapshot"]["employee_version"] == 1
    assert any(span["span_type"] == "context" for span in trace["spans"])
    assert trace["runtime_reference"]["engine_name"] == "fake"
    conversation = client.post("/api/v1/conversations", headers=headers, json={"title": "e2e", "is_group": False}).json()
    message = client.post(f"/api/v1/conversations/{conversation['id']}/messages", headers=headers, json={"sender_id": "member-local", "text": "hello"}).json()
    delivery = client.post(f"/api/v1/conversations/{conversation['id']}/deliveries", headers=headers, json={"body": "fake: e2e", "idempotency_key": "delivery-e2e"})
    assert message["conversation_id"] == conversation["id"]
    assert delivery.status_code == 201
