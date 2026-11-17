from __future__ import annotations

from fastapi.testclient import TestClient

from agent_platform.api.app import create_app
from agent_platform.application.catalog_service import InMemoryCatalogRepository


def test_employee_version_and_deployment_lifecycle_api_is_tenant_scoped_and_audited() -> None:
    repository = InMemoryCatalogRepository()
    client = TestClient(create_app(mode="fake", catalog_repository=repository))
    admin = {"X-Fake-User": "admin-local"}
    member = {"X-Fake-User": "member-local"}
    other_admin = {"X-Fake-User": "admin-other"}

    skill = client.post(
        "/api/v1/skills",
        headers=admin,
        json={"name": "生命周期 Skill", "skill_type": "instruction", "instruction": "按步骤执行"},
    )
    assert skill.status_code == 201
    assert client.post(f"/api/v1/skills/{skill.json()['id']}/publish", headers=admin).status_code == 200
    employee = client.post(
        "/api/v1/digital-employees",
        headers=admin,
        json={"name": "生命周期助手", "role_name": "客服", "description": "状态转换验收"},
    )
    assert employee.status_code == 201
    employee_id = employee.json()["id"]

    def create_version(system_prompt: str) -> int:
        response = client.post(
            f"/api/v1/digital-employees/{employee_id}/versions",
            headers=admin,
            json={"system_prompt": system_prompt, "skill_ids": [skill.json()["id"]]},
        )
        assert response.status_code == 201
        return response.json()["version"]

    version1 = create_version("版本一")
    assert client.post(
        f"/api/v1/digital-employees/{employee_id}/versions/{version1}/publish", headers=admin
    ).status_code == 200
    version2 = create_version("版本二")
    assert client.post(
        f"/api/v1/digital-employees/{employee_id}/versions/{version2}/publish", headers=admin
    ).status_code == 200
    deployment = client.post(
        "/api/v1/deployments", headers=admin, json={"employee_id": employee_id, "version": version2}
    )
    assert deployment.status_code == 201
    deployment_id = deployment.json()["id"]

    # 成员和其他租户管理员不能借生命周期操作改写本租户状态。
    assert client.post(
        f"/api/v1/digital-employees/{employee_id}/versions/{version1}/deprecate", headers=member
    ).status_code == 403
    assert client.post(
        f"/api/v1/digital-employees/{employee_id}/versions/{version1}/deprecate", headers=other_admin
    ).status_code == 404
    deprecated = client.post(
        f"/api/v1/digital-employees/{employee_id}/versions/{version1}/deprecate", headers=admin
    )
    assert deprecated.status_code == 200
    assert deprecated.json()["status"] == "DEPRECATED"
    repeated_deprecation = client.post(
        f"/api/v1/digital-employees/{employee_id}/versions/{version1}/deprecate", headers=admin
    )
    assert repeated_deprecation.status_code == 200
    assert repeated_deprecation.json()["status"] == "DEPRECATED"
    assert client.post(
        "/api/v1/deployments", headers=admin, json={"employee_id": employee_id, "version": version1}
    ).json()["code"] == "INVALID_STATE"
    assert client.post(
        f"/api/v1/deployments/{deployment_id}/rollback",
        headers=admin,
        json={"employee_id": employee_id, "version": version1},
    ).json()["code"] == "DEPENDENCY_VALIDATION_ERROR"

    session = client.post("/api/v1/sessions", headers=admin, json={"deployment_id": deployment_id})
    assert session.status_code == 201
    assert client.post(f"/api/v1/deployments/{deployment_id}/disable", headers=member).status_code == 403
    assert client.post(f"/api/v1/deployments/{deployment_id}/disable", headers=other_admin).status_code == 404
    disabled = client.post(f"/api/v1/deployments/{deployment_id}/disable", headers=admin)
    assert disabled.status_code == 200
    assert disabled.json()["status"] == "DISABLED"
    assert client.post(
        "/api/v1/sessions", headers=admin, json={"deployment_id": deployment_id}
    ).json()["code"] == "DEPLOYMENT_DISABLED"
    assert client.post(
        f"/api/v1/sessions/{session.json()['id']}/runs",
        headers=admin,
        json={"prompt": "新运行", "idempotency_key": "disabled-new-run"},
    ).json()["code"] == "DEPLOYMENT_DISABLED"

    enabled = client.post(f"/api/v1/deployments/{deployment_id}/enable", headers=admin)
    assert enabled.status_code == 200
    assert enabled.json()["status"] == "ACTIVE"
    assert enabled.json()["version"] == version2
    assert client.post(f"/api/v1/deployments/{deployment_id}/disable", headers=admin).status_code == 200
    assert client.post(
        f"/api/v1/digital-employees/{employee_id}/versions/{version2}/deprecate", headers=admin
    ).status_code == 200
    cannot_reenable = client.post(f"/api/v1/deployments/{deployment_id}/enable", headers=admin)
    assert cannot_reenable.status_code == 409
    assert cannot_reenable.json()["code"] == "INVALID_STATE"

    employee_audit = client.get(
        "/api/v1/audit-events",
        headers=admin,
        params={"resource_type": "digital_employee_version", "resource_id": employee_id, "limit": 20},
    )
    assert employee_audit.status_code == 200
    assert [item["event_type"] for item in employee_audit.json()["items"]].count(
        "EMPLOYEE_VERSION_DEPRECATED"
    ) == 2
    deployment_audit = client.get(
        "/api/v1/audit-events",
        headers=admin,
        params={"resource_type": "deployment", "resource_id": deployment_id, "limit": 20},
    )
    assert deployment_audit.status_code == 200
    assert {item["event_type"] for item in deployment_audit.json()["items"]} == {
        "DEPLOYMENT_CREATED",
        "DEPLOYMENT_DISABLED",
        "DEPLOYMENT_ENABLED",
    }
    assert client.get("/api/v1/audit-events", headers=member).status_code == 403
