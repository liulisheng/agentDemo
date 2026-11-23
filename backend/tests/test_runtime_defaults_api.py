from __future__ import annotations

from fastapi.testclient import TestClient

from agent_platform.adapters.identity.fake import FakeIdentityProvider
from agent_platform.api.app import create_app


def _model_route(client: TestClient, headers: dict[str, str]) -> dict[str, object]:
    connection = client.post(
        "/api/v1/model-connections",
        headers=headers,
        json={
            "name": "默认模型连接",
            "provider_kind": "openai-compatible",
            "protocol": "openai-chat-completions",
            "endpoint": "http://model.local/v1",
            "catalog_mode": "MANUAL",
        },
    )
    assert connection.status_code == 201
    connection_id = connection.json()["connection_id"]
    model = client.post(
        f"/api/v1/model-connections/{connection_id}/models",
        headers=headers,
        json={"model_id": "gpt-local", "display_name": "本地模型"},
    )
    assert model.status_code == 201
    route = client.post(
        "/api/v1/model-routes",
        headers=headers,
        json={
            "alias": "本地默认路由",
            "connection_id": connection_id,
            "catalog_model_id": "gpt-local",
            "model_name": "gpt-local",
        },
    )
    assert route.status_code == 201
    return route.json()


def test_engine_and_model_defaults_are_independent_and_audited() -> None:
    client = TestClient(create_app())
    admin = {"X-Fake-User": "admin-local"}
    member = {"X-Fake-User": "member-local"}
    other_admin = {"X-Fake-User": "admin-other"}
    assert client.post(
        "/api/v1/execution-profiles",
        headers=member,
        json={"engine_name": "codex_cli"},
    ).status_code == 403

    profile = client.post(
        "/api/v1/execution-profiles",
        headers=admin,
        json={"engine_name": "codex_cli"},
    )
    assert profile.status_code == 201
    assert profile.json()["model_alias"] is None
    route = _model_route(client, admin)

    saved_engine = client.put(
        "/api/v1/execution-profile-bindings/user",
        headers=admin,
        json={"profile_id": profile.json()["id"], "profile_version": profile.json()["version"]},
    )
    assert saved_engine.status_code == 200
    assert saved_engine.json()["binding"]["scope_type"] == "user"
    assert saved_engine.json()["binding"]["profile_id"] == profile.json()["id"]

    saved_model = client.put(
        "/api/v1/model-route-bindings/user",
        headers=admin,
        json={"route_id": route["route_id"]},
    )
    assert saved_model.status_code == 200
    assert saved_model.json()["binding"]["route_id"] == route["route_id"]

    effective_engine = client.get("/api/v1/execution-profile-bindings/effective", headers=admin)
    effective_model = client.get("/api/v1/model-route-bindings/effective", headers=admin)
    assert effective_engine.status_code == 200
    assert effective_engine.json()["source"] == "USER_DEFAULT"
    assert effective_engine.json()["profile"]["engine_name"] == "codex_cli"
    assert "model_alias" not in effective_engine.json()["profile"]
    assert "model_route_id" not in effective_engine.json()["profile"]
    assert "secret_ref" not in effective_model.text
    assert effective_model.json()["source"] == "USER_DEFAULT"
    assert effective_model.json()["route"]["model_name"] == "gpt-local"

    tenant_engine = client.put(
        "/api/v1/execution-profile-bindings/tenant",
        headers=admin,
        json={"profile_id": profile.json()["id"], "profile_version": 1},
    )
    tenant_model = client.put(
        "/api/v1/model-route-bindings/tenant",
        headers=admin,
        json={"route_id": route["route_id"]},
    )
    assert tenant_engine.status_code == tenant_model.status_code == 200
    assert client.get("/api/v1/execution-profile-bindings/tenant", headers=member).json()["binding"]["scope_id"] == "tenant-a"
    assert client.get("/api/v1/model-route-bindings/tenant", headers=member).json()["binding"]["scope_id"] == "tenant-a"
    assert client.put(
        "/api/v1/execution-profile-bindings/tenant",
        headers=member,
        json={"profile_id": profile.json()["id"], "profile_version": 1},
    ).status_code == 403
    assert client.put(
        "/api/v1/model-route-bindings/tenant",
        headers=member,
        json={"route_id": route["route_id"]},
    ).status_code == 403

    # 只改用户引擎默认，不得改写用户/租户模型默认。
    second_profile = client.post(
        "/api/v1/execution-profiles",
        headers=admin,
        json={"engine_name": "deepseek_harness"},
    ).json()
    client.put(
        "/api/v1/execution-profile-bindings/user",
        headers=admin,
        json={"profile_id": second_profile["id"], "profile_version": 1},
    )
    assert client.get("/api/v1/model-route-bindings/effective", headers=admin).json()["route"]["route_id"] == route["route_id"]

    assert client.get("/api/v1/execution-profile-bindings/effective", headers=other_admin).json()["profile"] is None
    assert client.get("/api/v1/model-route-bindings/effective", headers=other_admin).json()["route"] is None
    assert client.put(
        "/api/v1/execution-profile-bindings/user",
        headers=other_admin,
        json={"profile_id": profile.json()["id"], "profile_version": 1},
    ).status_code == 404
    assert client.put(
        "/api/v1/model-route-bindings/user",
        headers=other_admin,
        json={"route_id": route["route_id"]},
    ).status_code == 404

    audit_types = {event.event_type for event in client.app.state.catalog_service.repository.audit_events}
    assert "ENGINE_PROFILE_DEFAULT_UPDATED" in audit_types
    assert "MODEL_ROUTE_DEFAULT_UPDATED" in audit_types
    client.close()


def test_platform_defaults_are_final_persisted_fallbacks() -> None:
    client = TestClient(create_app())
    admin = {"X-Fake-User": "admin-other"}
    identity = FakeIdentityProvider().resolve_identity("admin-other")
    profile = client.post(
        "/api/v1/execution-profiles", headers=admin, json={"engine_name": "codex_cli"}
    )
    assert profile.status_code == 201
    route = _model_route(client, admin)
    repository = client.app.state.catalog_service.repository
    repository.set_engine_profile_default(
        identity,
        "platform",
        identity.platform_id,
        profile.json()["id"],
        profile.json()["version"],
    )
    repository.set_model_route_default(
        identity, "platform", identity.platform_id, route["route_id"]
    )

    engine = client.get("/api/v1/execution-profile-bindings/effective", headers=admin).json()
    model = client.get("/api/v1/model-route-bindings/effective", headers=admin).json()
    assert engine["source"] == "PLATFORM_DEFAULT"
    assert engine["profile"]["engine_name"] == "codex_cli"
    assert model["source"] == "PLATFORM_DEFAULT"
    assert model["route"]["route_id"] == route["route_id"]
    client.close()
