from fastapi.testclient import TestClient

from agent_platform.api.app import create_app


def test_runtime_strategy_defaults_are_tenant_scoped_and_role_checked() -> None:
    client = TestClient(create_app())
    admin = {"X-Fake-User": "admin-local"}
    member = {"X-Fake-User": "member-local"}

    saved = client.put(
        "/api/v1/runtime-strategy-bindings/tenant",
        headers=admin,
        json={
            "runtime_options": {
                "reasoning_mode": "DEEP",
                "reasoning_effort": "HIGH",
                "response_verbosity": "STANDARD",
            }
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["binding"]["runtime_options"]["reasoning_mode"] == "DEEP"

    effective = client.get(
        "/api/v1/runtime-strategy-bindings/effective", headers=member
    )
    assert effective.status_code == 200
    assert effective.json()["runtime_options"]["reasoning_effort"] == "HIGH"
    assert effective.json()["source"] == "TENANT_DEFAULT"

    denied = client.put(
        "/api/v1/runtime-strategy-bindings/tenant",
        headers=member,
        json={"runtime_options": {"reasoning_mode": "OFF"}},
    )
    assert denied.status_code == 403

    cleared = client.put(
        "/api/v1/runtime-strategy-bindings/tenant",
        headers=admin,
        json={"runtime_options": None},
    )
    assert cleared.status_code == 200
    assert cleared.json()["binding"] is None
