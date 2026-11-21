from __future__ import annotations

from fastapi.testclient import TestClient

from agent_platform.api.app import create_app


def _client() -> TestClient:
    return TestClient(create_app())


def test_model_connection_and_catalog_api_are_tenant_scoped_and_secret_safe() -> None:
    client = _client()
    admin = {"X-Fake-User": "admin-local"}
    other_admin = {"X-Fake-User": "admin-other"}
    member = {"X-Fake-User": "member-local"}

    created = client.post(
        "/api/v1/model-connections",
        headers=admin,
        json={
            "name": "本地模型",
            "provider_kind": "openai-compatible",
            "protocol": "openai-chat-completions",
            "endpoint": "http://host.docker.internal:63072/v1",
            "secret_id": "local-model-key",
            "catalog_mode": "MANUAL",
        },
    )
    assert created.status_code == 201
    connection = created.json()
    connection_id = connection["connection_id"]
    assert connection["tenant_id"] == "tenant-a"
    assert connection["secret_ref"] == {
        "secret_id": "local-model-key",
        "scope": "tenant-a",
        "provider": "environment",
    }
    assert "local-model-api-key-value" not in created.text

    denied_create = client.post(
        "/api/v1/model-connections",
        headers=member,
        json={
            "name": "成员不能创建",
            "provider_kind": "openai-compatible",
            "protocol": "openai-chat-completions",
            "endpoint": "http://host.docker.internal:63072/v1",
        },
    )
    assert denied_create.status_code == 403

    listed = client.get("/api/v1/model-connections", headers=admin)
    assert [item["connection_id"] for item in listed.json()["items"]] == [connection_id]
    assert client.get("/api/v1/model-connections", headers=other_admin).json()["items"] == []
    assert client.get(
        f"/api/v1/model-connections/{connection_id}", headers=other_admin
    ).status_code == 404

    model = client.post(
        f"/api/v1/model-connections/{connection_id}/models",
        headers=admin,
        json={
            "model_id": "local-chat-model",
            "display_name": "本地聊天模型",
            "capabilities": {"chat": True},
            "context_window": 8192,
        },
    )
    assert model.status_code == 201
    assert model.json()["connection_id"] == connection_id
    assert model.json()["catalog_source"] == "MANUAL"
    assert client.get(
        f"/api/v1/model-connections/{connection_id}/models", headers=admin
    ).json()["items"][0]["model_id"] == "local-chat-model"
    assert client.get(
        f"/api/v1/model-connections/{connection_id}/models", headers=other_admin
    ).status_code == 404


def test_connection_update_creates_a_new_version_and_model_sync_is_queued() -> None:
    client = _client()
    headers = {"X-Fake-User": "admin-local"}
    created = client.post(
        "/api/v1/model-connections",
        headers=headers,
        json={
            "name": "本地模型",
            "provider_kind": "openai-compatible",
            "protocol": "openai-chat-completions",
            "endpoint": "http://host.docker.internal:63072/v1",
            "catalog_mode": "REMOTE",
        },
    )
    assert created.status_code == 201
    connection_id = created.json()["connection_id"]

    updated = client.put(
        f"/api/v1/model-connections/{connection_id}",
        headers=headers,
        json={"endpoint": "http://host.docker.internal:63072/v1/"},
    )
    assert updated.status_code == 200
    assert updated.json()["connection_id"] == connection_id
    assert updated.json()["version"] == 2

    sync = client.post(
        f"/api/v1/model-connections/{connection_id}/sync-models", headers=headers
    )
    assert sync.status_code == 202
    test_id = sync.json()["test_id"]
    assert sync.json()["state"] == "QUEUED"
    assert client.get(f"/api/v1/model-tests/{test_id}", headers=headers).json()["state"] == "QUEUED"


def test_selectable_model_routes_are_filtered_by_tenant_and_status() -> None:
    client = _client()
    admin = {"X-Fake-User": "admin-local"}
    other_admin = {"X-Fake-User": "admin-other"}
    route = client.post(
        "/api/v1/model-routes",
        headers=admin,
        json={"alias": "local-route", "model_name": "local-model", "provider": "fake"},
    )
    assert route.status_code == 201

    selected = client.get("/api/v1/model-routes/selectable", headers=admin)
    assert [item["route_id"] for item in selected.json()["items"]] == [route.json()["route_id"]]
    assert selected.json()["items"][0]["authorization_source"] == "platform"
    assert client.get("/api/v1/model-routes/selectable", headers=other_admin).json()["items"] == []


def test_catalog_backed_route_create_update_and_disable_flow() -> None:
    client = _client()
    admin = {"X-Fake-User": "admin-local"}
    connection = client.post(
        "/api/v1/model-connections",
        headers=admin,
        json={
            "name": "本地模型",
            "provider_kind": "openai-compatible",
            "protocol": "openai-chat-completions",
            "endpoint": "http://host.docker.internal:63072/v1",
            "catalog_mode": "MANUAL",
        },
    ).json()
    for model_id in ("chat-a", "chat-b"):
        assert client.post(
            f"/api/v1/model-connections/{connection['connection_id']}/models",
            headers=admin,
            json={"model_id": model_id, "display_name": model_id},
        ).status_code == 201

    created = client.post(
        "/api/v1/model-routes",
        headers=admin,
        json={
            "alias": "assistant-model",
            "connection_id": connection["connection_id"],
            "catalog_model_id": "chat-a",
            "model_name": "chat-a",
        },
    )
    assert created.status_code == 201
    route = created.json()
    assert route["profile"]["connection_id"] == connection["connection_id"]
    assert route["profile"]["catalog_model_id"] == "chat-a"
    assert route["route_version"] == 1

    updated = client.put(
        f"/api/v1/model-routes/{route['route_id']}",
        headers=admin,
        json={"catalog_model_id": "chat-b", "model_name": "chat-b"},
    )
    assert updated.status_code == 200
    assert updated.json()["profile"]["catalog_model_id"] == "chat-b"
    assert updated.json()["route_version"] == 2

    disabled = client.post(
        f"/api/v1/model-routes/{route['route_id']}/disable", headers=admin
    )
    assert disabled.status_code == 200
    assert disabled.json()["status"] == "DISABLED"
    assert disabled.json()["route_version"] == 3
    assert client.get("/api/v1/model-routes/selectable", headers=admin).json()["items"] == []

    enabled = client.post(f"/api/v1/model-routes/{route['route_id']}/enable", headers=admin)
    assert enabled.status_code == 200
    assert enabled.json()["status"] == "ACTIVE"
    assert enabled.json()["route_version"] == 4


def test_connection_and_confirmed_model_tests_are_queued_and_tenant_scoped() -> None:
    client = _client()
    admin = {"X-Fake-User": "admin-local"}
    other_admin = {"X-Fake-User": "admin-other"}
    connection = client.post(
        "/api/v1/model-connections",
        headers=admin,
        json={
            "name": "人工目录连接",
            "provider_kind": "openai-compatible",
            "protocol": "openai-chat-completions",
            "endpoint": "http://host.docker.internal:63072/v1",
            "secret_id": "tenant-model-key",
            "catalog_mode": "MANUAL",
        },
    ).json()
    connection_id = connection["connection_id"]
    connection_test = client.post(
        f"/api/v1/model-connections/{connection_id}/tests", headers=admin
    )
    assert connection_test.status_code == 202
    assert connection_test.json()["state"] == "QUEUED"

    assert client.post(
        f"/api/v1/model-connections/{connection_id}/models",
        headers=admin,
        json={"model_id": "local-chat-a", "display_name": "Local A"},
    ).status_code == 201
    route = client.post(
        "/api/v1/model-routes",
        headers=admin,
        json={
            "alias": "test-route",
            "connection_id": connection_id,
            "catalog_model_id": "local-chat-a",
            "model_name": "local-chat-a",
        },
    ).json()
    unconfirmed = client.post(
        f"/api/v1/model-routes/{route['route_id']}/tests",
        headers=admin,
        json={"confirmed_cost": False},
    )
    assert unconfirmed.status_code == 422
    assert unconfirmed.json()["code"] == "INVALID_ARGUMENT"

    confirmed = client.post(
        f"/api/v1/model-routes/{route['route_id']}/tests",
        headers=admin,
        json={"confirmed_cost": True},
    )
    assert confirmed.status_code == 202
    record = client.get(
        f"/api/v1/model-tests/{confirmed.json()['test_id']}", headers=admin
    )
    assert record.status_code == 200
    assert record.json()["state"] == "QUEUED"
    assert record.json()["model_id"] == "local-chat-a"
    assert client.get(
        f"/api/v1/model-tests/{confirmed.json()['test_id']}", headers=other_admin
    ).status_code == 404
    assert "tenant-model-key-value" not in record.text
