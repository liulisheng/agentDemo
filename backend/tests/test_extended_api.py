from fastapi.testclient import TestClient

from agent_platform.api.app import create_app


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
