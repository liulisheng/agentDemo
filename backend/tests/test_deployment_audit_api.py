from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agent_platform.adapters.identity.fake import FakeIdentityProvider
from agent_platform.adapters.runtime.fake import FakeRuntimeAdapter
from agent_platform.api.app import create_app
from agent_platform.application.catalog_service import CatalogService, InMemoryCatalogRepository
from agent_platform.application.control_plane import ControlPlaneService
from agent_platform.domain.catalog import SkillType
from agent_platform.domain.common import DomainError


def test_deployment_filter_version_summary_audit_and_rollback_preserve_run_snapshot() -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    repository = InMemoryCatalogRepository()
    catalog = CatalogService(repository)
    skill = catalog.create_skill(identity, "审计 Skill", SkillType.INSTRUCTION, "按步骤回答", ())
    catalog.publish_skill(identity, skill.skill_id)
    employee = catalog.create_employee(identity, "审计助手", "客服", "审计验收")
    version1 = catalog.create_employee_version(
        identity, employee.employee_id, "版本一系统提示", [skill.skill_id], []
    )
    catalog.publish_employee_version(identity, employee.employee_id, version1.version)
    version2 = catalog.create_employee_version(
        identity, employee.employee_id, "版本二系统提示", [skill.skill_id], []
    )
    catalog.publish_employee_version(identity, employee.employee_id, version2.version)
    deployment = catalog.deploy(identity, employee.employee_id, version2.version)
    control_plane = ControlPlaneService(catalog, FakeRuntimeAdapter())
    session = control_plane.create_session(identity, deployment.deployment_id)
    run = control_plane.create_run(identity, session.session_id, "固定版本二")

    with pytest.raises(DomainError) as unpublished_rollback:
        draft = catalog.create_employee_version(
            identity, employee.employee_id, "未发布版本", [skill.skill_id], []
        )
        catalog.rollback(identity, deployment.deployment_id, employee.employee_id, draft.version)
    assert unpublished_rollback.value.code == "DEPENDENCY_VALIDATION_ERROR"

    rolled_back = catalog.rollback(identity, deployment.deployment_id, employee.employee_id, version1.version)
    assert rolled_back.employee_version.version == version1.version
    assert control_plane.get_trace(identity, run.run_id).run.snapshot.employee_version == version2.version

    client = TestClient(create_app(mode="fake", catalog_repository=repository))
    headers = {"X-Fake-User": identity.user_id}
    deployments = client.get(
        "/api/v1/deployments",
        headers=headers,
        params={"employee_id": employee.employee_id, "status": "ACTIVE", "limit": 1},
    )
    assert deployments.status_code == 200
    assert deployments.json()["items"][0]["version"] == version1.version
    assert deployments.json()["items"][0]["version_summary"]["skill_count"] == 1

    first_events = client.get(
        "/api/v1/audit-events",
        headers=headers,
        params={"resource_type": "deployment", "resource_id": deployment.deployment_id, "limit": 1},
    )
    assert first_events.status_code == 200
    assert len(first_events.json()["items"]) == 1
    assert first_events.json()["next_cursor"]
    second_events = client.get(
        "/api/v1/audit-events",
        headers=headers,
        params={
            "resource_type": "deployment",
            "resource_id": deployment.deployment_id,
            "limit": 1,
            "cursor": first_events.json()["next_cursor"],
        },
    )
    assert second_events.status_code == 200
    assert len(second_events.json()["items"]) == 1
    assert {first_events.json()["items"][0]["event_type"], second_events.json()["items"][0]["event_type"]} == {
        "DEPLOYMENT_CREATED",
        "DEPLOYMENT_ROLLED_BACK",
    }
    assert client.get("/api/v1/audit-events", headers={"X-Fake-User": "member-local"}).status_code == 403
