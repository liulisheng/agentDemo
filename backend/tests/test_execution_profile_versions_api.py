from __future__ import annotations

from fastapi.testclient import TestClient

from agent_platform.api.app import create_app


def test_execution_profile_versions_are_editable_only_as_drafts() -> None:
    client = TestClient(create_app())
    admin = {"X-Fake-User": "admin-local"}
    member = {"X-Fake-User": "member-local"}
    other_admin = {"X-Fake-User": "admin-other"}

    created = client.post(
        "/api/v1/execution-profiles",
        headers=admin,
        json={"engine_name": "codex_cli", "approval_policy": "none"},
    )
    assert created.status_code == 201, created.text
    profile_id = created.json()["id"]

    unsupported_create = client.post(
        "/api/v1/execution-profiles",
        headers=admin,
        json={"engine_name": "deepseek_harness", "approval_policy": "required"},
    )
    assert unsupported_create.status_code == 409
    assert unsupported_create.json()["code"] == "ENGINE_CAPABILITY_UNSUPPORTED"
    unsupported_profile = client.post(
        "/api/v1/execution-profiles",
        headers=admin,
        json={"engine_name": "deepseek_harness", "approval_policy": "required"},
    )
    assert unsupported_profile.status_code == 409
    assert unsupported_profile.json()["code"] == "ENGINE_CAPABILITY_UNSUPPORTED"

    assert client.put(
        f"/api/v1/execution-profiles/{profile_id}/versions/1",
        headers=admin,
        json={"engine_name": "deepseek_harness", "approval_policy": "none"},
    ).json()["code"] == "IMMUTABLE_VERSION"

    denied = client.post(
        f"/api/v1/execution-profiles/{profile_id}/versions/1/fork", headers=member
    )
    assert denied.status_code == 403

    forked = client.post(
        f"/api/v1/execution-profiles/{profile_id}/versions/1/fork", headers=admin
    )
    assert forked.status_code == 201, forked.text
    assert forked.json()["id"] == profile_id
    assert forked.json()["version"] == 2
    assert forked.json()["status"] == "DRAFT"
    assert forked.json()["model_alias"] is None
    assert forked.json()["model_route_id"] is None

    wrong_tenant = client.post(
        f"/api/v1/execution-profiles/{profile_id}/versions/1/fork", headers=other_admin
    )
    assert wrong_tenant.status_code == 404

    unsupported_update = client.put(
        f"/api/v1/execution-profiles/{profile_id}/versions/2",
        headers=admin,
        json={"engine_name": "deepseek_harness", "approval_policy": "required"},
    )
    assert unsupported_update.status_code == 409
    assert unsupported_update.json()["code"] == "ENGINE_CAPABILITY_UNSUPPORTED"

    unsupported_draft_update = client.put(
        f"/api/v1/execution-profiles/{profile_id}/versions/2",
        headers=admin,
        json={"engine_name": "deepseek_harness", "approval_policy": "required"},
    )
    assert unsupported_draft_update.status_code == 409
    assert unsupported_draft_update.json()["code"] == "ENGINE_CAPABILITY_UNSUPPORTED"

    updated = client.put(
        f"/api/v1/execution-profiles/{profile_id}/versions/2",
        headers=admin,
        json={
            "engine_name": "deepseek_harness",
            "workspace_profile_id": None,
            "workspace_profile_version": None,
            "sandbox_profile_id": None,
            "sandbox_profile_version": None,
            "approval_policy": "none",
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["engine_name"] == "deepseek_harness"
    assert updated.json()["status"] == "DRAFT"

    default = client.put(
        "/api/v1/execution-profile-bindings/user",
        headers=admin,
        json={"profile_id": profile_id, "profile_version": 1},
    )
    assert default.status_code == 200

    published = client.post(
        f"/api/v1/execution-profiles/{profile_id}/versions/2/publish", headers=admin
    )
    assert published.status_code == 200, published.text
    assert published.json()["status"] == "PUBLISHED"
    assert published.json()["version"] == 2

    profiles = client.get("/api/v1/execution-profiles", headers=admin).json()["items"]
    assert len(profiles) == 1
    assert profiles[0]["id"] == profile_id
    assert profiles[0]["version"] == 2
    effective = client.get(
        "/api/v1/execution-profile-bindings/effective", headers=admin
    ).json()
    assert effective["binding"]["profile_version"] == 1

    immutable = client.put(
        f"/api/v1/execution-profiles/{profile_id}/versions/2",
        headers=admin,
        json={"engine_name": "codex_cli", "approval_policy": "none"},
    )
    assert immutable.status_code == 409
    assert immutable.json()["code"] == "IMMUTABLE_VERSION"

    from agent_platform.adapters.identity.fake import FakeIdentityProvider
    from agent_platform.domain.catalog import ExecutionProfile

    identity = FakeIdentityProvider().resolve_identity("admin-local")
    legacy_profile = ExecutionProfile(
        profile_id="legacy-workspace-profile",
        tenant_id=identity.tenant_id,
        engine_name="deepseek_harness",
        workspace_profile_id="workspace-old",
    ).publish()
    client.app.state.catalog_service.repository.save_profile(identity, legacy_profile)
    legacy_versions = client.get(
        f"/api/v1/execution-profiles/{legacy_profile.profile_id}/versions", headers=admin
    )
    assert legacy_versions.status_code == 200
    assert legacy_versions.json()["items"][0]["runtime_policy_supported"] is False
    unavailable_default = client.put(
        "/api/v1/execution-profile-bindings/user",
        headers=admin,
        json={"profile_id": legacy_profile.profile_id, "profile_version": 1},
    )
    assert unavailable_default.status_code == 409
    assert unavailable_default.json()["code"] == "ENGINE_CAPABILITY_UNSUPPORTED"
    client.close()
