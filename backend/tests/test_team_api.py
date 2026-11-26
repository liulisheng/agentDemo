from fastapi.testclient import TestClient

from agent_platform.adapters.identity.fake import FakeIdentityProvider
from agent_platform.api.app import create_app
from agent_platform.domain.catalog import SkillType
from tests._runtime_config_helpers import configure_in_memory_deployment


def _team(client: TestClient, headers: dict[str, str]) -> tuple[str, int]:
    created = client.post("/api/v1/teams", headers=headers, json={"name": "分析团队"})
    assert created.status_code == 201, created.text
    team = created.json()
    for member_id, role, engine in (
        ("router", "ROUTER", "deepseek_harness"),
        ("finalizer", "FINALIZER", "codex_cli"),
    ):
        added = client.post(
            f"/api/v1/teams/{team['team_id']}/versions/1/members",
            headers=headers,
            json={
                "member_id": member_id,
                "employee_id": f"employee-{member_id}",
                "employee_version": 1,
                "deployment_id": f"deployment-{member_id}",
                "role": role,
                "engine_name": engine,
                "capabilities": ["reasoning", "workspace"],
            },
        )
        assert added.status_code == 200, added.text
    published = client.post(
        f"/api/v1/teams/{team['team_id']}/versions/1/publish", headers=headers
    )
    assert published.status_code == 200, published.text
    return team["team_id"], 1


def test_team_api_creates_publishes_and_reads_team() -> None:
    client = TestClient(create_app())
    admin = {"X-Fake-User": "admin-local"}
    team_id, version = _team(client, admin)

    result = client.get(f"/api/v1/teams/{team_id}/versions/{version}", headers=admin)

    assert result.status_code == 200
    assert result.json()["status"] == "PUBLISHED"
    assert {item["role"] for item in result.json()["members"]} == {"ROUTER", "FINALIZER"}
    audit = client.get(f"/api/v1/teams/{team_id}/audit-events", headers=admin)
    assert audit.status_code == 200
    assert any(item["event_type"] == "TEAM_VERSION_PUBLISHED" for item in audit.json()["items"])
    pools = client.get(f"/api/v1/teams/{team_id}/worker-pools", headers=admin)
    assert pools.status_code == 200
    assert {item["engine_name"] for item in pools.json()["items"]} == {"codex_cli", "deepseek_harness"}
    openapi = client.get("/openapi.json")
    assert openapi.status_code == 200
    paths = openapi.json()["paths"]
    assert "/api/v1/teams" in paths
    assert "/api/v1/orchestrations/{orchestration_id}/events" in paths
    assert "/api/v1/artifacts/{artifact_id}/download-url" in paths


def test_admin_can_create_members_publish_and_open_a_bound_team_conversation() -> None:
    app = create_app()
    client = TestClient(app)
    admin = {"X-Fake-User": "admin-local"}
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    catalog = app.state.catalog_service
    profile = catalog.create_profile(identity, "fake", "fake-model")
    skill = catalog.create_skill(identity, "团队基础技能", SkillType.INSTRUCTION, "按用户要求完成分析。", [])
    catalog.publish_skill(identity, skill.skill_id)
    employee = catalog.create_employee(identity, "团队数字员工", "分析", "用于团队协作")
    employee_version = catalog.create_employee_version(identity, employee.employee_id, "团队成员系统指令。", [skill.skill_id], [], execution_profile_id=profile.profile_id)
    catalog.publish_employee_version(identity, employee.employee_id, employee_version.version)
    deployment = configure_in_memory_deployment(catalog, identity, catalog.deploy(identity, employee.employee_id, employee_version.version))

    team = client.post("/api/v1/teams", headers=admin, json={"name": "可执行团队"}).json()
    member_ids = []
    for role in ("ROUTER", "FINALIZER"):
        added = client.post(
            f"/api/v1/teams/{team['team_id']}/versions/1/members",
            headers=admin,
            json={
                "employee_id": employee.employee_id,
                "employee_version": employee_version.version,
                "deployment_id": deployment.deployment_id,
                "role": role,
                "engine_name": "fake",
                "capabilities": ["reasoning"],
            },
        )
        assert added.status_code == 200, added.text
        member_ids.append(added.json()["members"][-1]["member_id"])
    published = client.post(f"/api/v1/teams/{team['team_id']}/versions/1/publish", headers=admin)
    assert published.status_code == 200, published.text

    created = client.post(
        "/api/v1/conversations/team",
        headers=admin,
        json={"title": "团队对话联调", "team_id": team["team_id"], "team_version": 1},
    )

    assert created.status_code == 201, created.text
    assert created.json()["conversation_mode"] == "TEAM"
    assert {item["member_id"] for item in created.json()["bindings"]} == set(member_ids)
    assert all(len(item["session_id"]) == 26 for item in created.json()["bindings"])

    turn = client.post(
        f"/api/v1/conversations/{created.json()['id']}/team-turns",
        headers=admin,
        json={"text": "分析本季度销售变化", "idempotency_key": "team-turn-1"},
    )
    assert turn.status_code == 202, turn.text
    assert turn.json()["status"] == "AWAITING_PLAN_APPROVAL"
    assert turn.json()["task_count"] == 1
    messages = client.get(f"/api/v1/conversations/{created.json()['id']}/messages", headers=admin)
    assert messages.status_code == 200
    assert [item["source"] for item in messages.json()["items"]][-2:] == ["user", "agent"]
    replay = client.post(
        f"/api/v1/conversations/{created.json()['id']}/team-turns",
        headers=admin,
        json={"text": "分析本季度销售变化", "idempotency_key": "team-turn-1"},
    )
    assert replay.status_code == 202
    assert replay.json()["orchestration_id"] == turn.json()["orchestration_id"]
    orchestration_id = turn.json()["orchestration_id"]
    approved = client.post(f"/api/v1/orchestrations/{orchestration_id}/plan/approve", headers=admin)
    assert approved.status_code == 200, approved.text
    assert client.app.state.team_task_execution_service.wait_for_idle(timeout=5)
    tasks = client.get(f"/api/v1/orchestrations/{orchestration_id}/tasks", headers=admin)
    assert tasks.status_code == 200
    assert tasks.json()["items"][0]["status"] == "SUCCEEDED"
    messages_after_execution = client.get(f"/api/v1/conversations/{created.json()['id']}/messages", headers=admin).json()["items"]
    assert any(item["source"] == "agent" and "fake:" in item["text"] for item in messages_after_execution)
    outputs = client.get(f"/api/v1/orchestrations/{orchestration_id}/artifacts", headers=admin).json()["items"]
    assert len(outputs) == 1 and outputs[0]["status"] == "READY"
    preview = client.get(f"/api/v1/artifacts/{outputs[0]['artifact_id']}/preview", headers=admin)
    assert preview.status_code == 200 and "fake:" in preview.json()["content"]
    delivery = client.post(
        f"/api/v1/orchestrations/{orchestration_id}/deliver",
        headers=admin,
        json={"artifact_ids": [outputs[0]["artifact_id"]], "channel_name": "web", "idempotency_key": "team-output-delivery"},
    )
    assert delivery.status_code == 200 and delivery.json()["items"][0]["status"] == "DELIVERED"
    downloaded = client.get(f"/api/v1/artifacts/{outputs[0]['artifact_id']}/download", headers=admin)
    assert downloaded.status_code == 200 and b"fake:" in downloaded.content


def test_member_cannot_publish_team() -> None:
    client = TestClient(create_app())
    admin = {"X-Fake-User": "admin-local"}
    member = {"X-Fake-User": "member-local"}
    created = client.post("/api/v1/teams", headers=admin, json={"name": "分析团队"}).json()

    denied = client.post(
        f"/api/v1/teams/{created['team_id']}/versions/1/publish", headers=member
    )

    assert denied.status_code == 403
    assert denied.json()["code"] == "AUTHORIZATION_DENIED"


def test_publish_rejects_stale_team_version() -> None:
    client = TestClient(create_app())
    admin = {"X-Fake-User": "admin-local"}
    created = client.post("/api/v1/teams", headers=admin, json={"name": "分析团队"}).json()

    denied = client.post(
        f"/api/v1/teams/{created['team_id']}/versions/1/publish?expected_version=2",
        headers=admin,
    )

    assert denied.status_code == 409
    assert denied.json()["code"] == "TEAM_VERSION_CONFLICT"


def test_team_configuration_forks_and_saves_policy_and_roles() -> None:
    client = TestClient(create_app())
    admin = {"X-Fake-User": "admin-local"}
    team_id, version = _team(client, admin)

    forked = client.post(f"/api/v1/teams/{team_id}/versions/{version}/fork", headers=admin)
    assert forked.status_code == 201, forked.text
    assert forked.json()["version"] == 2
    assert forked.json()["status"] == "DRAFT"
    members = forked.json()["members"]
    policy = client.put(
        f"/api/v1/teams/{team_id}/versions/2/policy",
        headers=admin,
        json={"orchestration_mode": "ROUTER", "approval_mode": "AUTO", "max_concurrency": 5, "delivery_policy": "ATTACHMENTS"},
    )
    assert policy.status_code == 200, policy.text
    roles = client.put(
        f"/api/v1/teams/{team_id}/versions/2/roles",
        headers=admin,
        json={"router_member_id": members[1]["member_id"], "finalizer_member_id": members[0]["member_id"]},
    )
    assert roles.status_code == 200, roles.text
    latest = client.get(f"/api/v1/teams/{team_id}/versions/2", headers=admin).json()
    original = client.get(f"/api/v1/teams/{team_id}/versions/1", headers=admin).json()
    assert latest["max_concurrency"] == 5
    assert latest["delivery_policy"] == "ATTACHMENTS"
    assert latest["router_member_id"] == members[1]["member_id"]
    assert original["status"] == "PUBLISHED" and original["orchestration_mode"] == "HYBRID"


def test_cross_tenant_team_read_is_not_found() -> None:
    client = TestClient(create_app())
    admin = {"X-Fake-User": "admin-local"}
    other = {"X-Fake-User": "admin-other"}
    team_id, version = _team(client, admin)

    response = client.get(f"/api/v1/teams/{team_id}/versions/{version}", headers=other)

    assert response.status_code == 404


def test_team_mode_rejects_single_agent_turn_endpoint() -> None:
    client = TestClient(create_app())
    admin = {"X-Fake-User": "admin-local"}
    conversation = client.post(
        "/api/v1/conversations",
        headers=admin,
        json={"title": "团队任务", "is_group": True, "conversation_mode": "TEAM"},
    )
    assert conversation.status_code == 201, conversation.text

    response = client.post(
        f"/api/v1/conversations/{conversation.json()['id']}/turns",
        headers=admin,
        json={"text": "普通 turn", "idempotency_key": "turn-1"},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "CONVERSATION_MODE_MISMATCH"


def test_single_agent_conversation_rejects_team_orchestration_endpoint() -> None:
    client = TestClient(create_app())
    admin = {"X-Fake-User": "admin-local"}
    conversation = client.post(
        "/api/v1/conversations",
        headers=admin,
        json={"title": "单员工任务", "is_group": False},
    ).json()

    response = client.post(
        f"/api/v1/conversations/{conversation['id']}/orchestrations",
        headers=admin,
        json={
            "team_id": "team-1",
            "team_version": 1,
            "idempotency_key": "orch-1",
            "plan": [{"client_key": "a", "title": "A"}],
        },
    )

    assert response.status_code == 409
    assert response.json()["code"] == "CONVERSATION_MODE_MISMATCH"


def test_team_orchestration_plan_controls_are_idempotent_and_cancellable() -> None:
    client = TestClient(create_app())
    admin = {"X-Fake-User": "admin-local"}
    team_id, version = _team(client, admin)
    conversation = client.post(
        "/api/v1/conversations",
        headers=admin,
        json={"title": "团队任务", "is_group": True, "conversation_mode": "TEAM"},
    ).json()
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    client.app.state.team_service.bind_conversation(identity, conversation["id"], team_id, version)
    created = client.post(
        f"/api/v1/conversations/{conversation['id']}/orchestrations",
        headers=admin,
        json={
            "team_id": team_id,
            "team_version": version,
            "idempotency_key": "orch-1",
            "plan": [{"client_key": "a", "title": "A"}],
        },
    )
    assert created.status_code == 202, created.text
    orchestration_id = created.json()["orchestration_id"]
    activity = client.get(f"/api/v1/conversations/{conversation['id']}/team-activity", headers=admin)
    assert activity.status_code == 200
    assert activity.json()["items"][0]["orchestration_id"] == orchestration_id
    events = client.get(f"/api/v1/orchestrations/{orchestration_id}/events", headers=admin)
    assert events.status_code == 200
    assert events.json()["items"][0]["event_type"] == "plan_created"
    replay = client.post(
        f"/api/v1/conversations/{conversation['id']}/orchestrations",
        headers=admin,
        json={
            "team_id": team_id,
            "team_version": version,
            "idempotency_key": "orch-1",
            "plan": [{"client_key": "a", "title": "A"}],
        },
    )
    assert replay.status_code == 202
    assert replay.json()["orchestration_id"] == orchestration_id
    cancelled = client.post(f"/api/v1/orchestrations/{orchestration_id}/cancel", headers=admin)
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "CANCELLED"
