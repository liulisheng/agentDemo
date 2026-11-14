from fastapi.testclient import TestClient

from agent_platform.api.app import create_app


def test_list_digital_employees_is_tenant_scoped() -> None:
    client = TestClient(create_app())

    response = client.get(
        "/api/v1/digital-employees",
        headers={"X-Fake-User": "admin-local"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["items"]
    assert all(item["tenant_id"] == "tenant-a" for item in body["items"])


def test_list_digital_employees_requires_identity() -> None:
    client = TestClient(create_app())

    response = client.get("/api/v1/digital-employees")

    assert response.status_code == 401
