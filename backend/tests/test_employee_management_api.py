from __future__ import annotations

from fastapi.testclient import TestClient

from agent_platform.adapters.identity.fake import FakeIdentityProvider
from agent_platform.api.app import create_app
from agent_platform.application.catalog_service import InMemoryCatalogRepository


def test_employee_list_cursor_search_detail_and_version_reads_are_tenant_scoped() -> None:
    repository = InMemoryCatalogRepository()
    client = TestClient(create_app(mode="fake", catalog_repository=repository))
    admin = {"X-Fake-User": "admin-local"}
    other = {"X-Fake-User": "admin-other"}
    created = []
    for name in ("二线助手", "一线助手", "一线助手"):
        response = client.post(
            "/api/v1/digital-employees",
            headers=admin,
            json={"name": name, "role_name": "客服", "description": "工单处理"},
        )
        assert response.status_code == 201
        created.append(response.json()["id"])

    first_page = client.get("/api/v1/digital-employees", headers=admin, params={"limit": 2})
    assert first_page.status_code == 200
    assert len(first_page.json()["items"]) == 2
    assert first_page.json()["next_cursor"]
    second_page = client.get(
        "/api/v1/digital-employees",
        headers=admin,
        params={"limit": 2, "cursor": first_page.json()["next_cursor"]},
    )
    assert second_page.status_code == 200
    all_items = first_page.json()["items"] + second_page.json()["items"]
    listed_ids = [item["id"] for item in all_items]
    listed_pairs = [(item["name"].casefold(), item["id"]) for item in all_items]
    assert listed_pairs == sorted(listed_pairs)
    assert set(listed_ids) == set(created)

    filtered = client.get(
        "/api/v1/digital-employees", headers=admin, params={"keyword": "二线", "status": "DRAFT"}
    )
    assert [item["name"] for item in filtered.json()["items"]] == ["二线助手"]
    assert client.get(f"/api/v1/digital-employees/{created[0]}", headers=admin).status_code == 200
    assert client.get(f"/api/v1/digital-employees/{created[0]}", headers=other).status_code == 404

    skill = client.post(
        "/api/v1/skills",
        headers=admin,
        json={"name": "分页 Skill", "skill_type": "instruction", "instruction": "按步骤回答"},
    )
    assert skill.status_code == 201
    target = created[0]
    for prompt in ("版本一", "版本二", "版本三"):
        response = client.post(
            f"/api/v1/digital-employees/{target}/versions",
            headers=admin,
            json={"system_prompt": prompt, "skill_ids": [skill.json()["id"]]},
        )
        assert response.status_code == 201
    version_page = client.get(
        f"/api/v1/digital-employees/{target}/versions", headers=admin, params={"limit": 2}
    )
    assert [item["version"] for item in version_page.json()["items"]] == [3, 2]
    next_versions = client.get(
        f"/api/v1/digital-employees/{target}/versions",
        headers=admin,
        params={"limit": 2, "cursor": version_page.json()["next_cursor"]},
    )
    assert [item["version"] for item in next_versions.json()["items"]] == [1]
    version_detail = client.get(
        f"/api/v1/digital-employees/{target}/versions/2", headers=admin
    )
    assert version_detail.status_code == 200
    assert version_detail.json()["system_prompt"] == "版本二"
    assert version_detail.json()["skill_version_refs"] == [
        {"skill_id": skill.json()["id"], "version": 1}
    ]

    wrong_filter_cursor = client.get(
        "/api/v1/digital-employees",
        headers=admin,
        params={"limit": 2, "keyword": "其他", "cursor": first_page.json()["next_cursor"]},
    )
    assert wrong_filter_cursor.status_code == 422


def test_employee_patch_draft_update_archive_and_admin_permissions() -> None:
    repository = InMemoryCatalogRepository()
    client = TestClient(create_app(mode="fake", catalog_repository=repository))
    admin = {"X-Fake-User": "admin-local"}
    member = {"X-Fake-User": "member-local"}
    other_admin = {"X-Fake-User": "admin-other"}
    denied_create = client.post(
        "/api/v1/digital-employees",
        headers=member,
        json={"name": "成员员工", "role_name": "客服", "description": "不应创建"},
    )
    assert denied_create.status_code == 403

    skill = client.post(
        "/api/v1/skills",
        headers=admin,
        json={"name": "发布依赖", "skill_type": "instruction", "instruction": "遵循约定"},
    )
    assert client.post(f"/api/v1/skills/{skill.json()['id']}/publish", headers=admin).status_code == 200
    employee = client.post(
        "/api/v1/digital-employees",
        headers=admin,
        json={"name": "初始名称", "role_name": "旧角色", "description": "旧描述"},
    ).json()
    employee_id = employee["id"]
    patched = client.patch(
        f"/api/v1/digital-employees/{employee_id}",
        headers=admin,
        json={"name": "新名称", "role_name": "新角色"},
    )
    assert patched.status_code == 200
    assert patched.json()["name"] == "新名称"
    assert patched.json()["description"] == "旧描述"
    assert client.patch(
        f"/api/v1/digital-employees/{employee_id}",
        headers=member,
        json={"description": "成员尝试修改"},
    ).status_code == 403
    assert client.patch(
        f"/api/v1/digital-employees/{employee_id}",
        headers=other_admin,
        json={"name": "越租户"},
    ).status_code == 404

    create_version = lambda prompt: client.post(
        f"/api/v1/digital-employees/{employee_id}/versions",
        headers=admin,
        json={"system_prompt": prompt, "skill_ids": [skill.json()["id"]]},
    )
    first = create_version("已发布指令")
    assert first.status_code == 201
    assert client.post(
        f"/api/v1/digital-employees/{employee_id}/versions/1/publish", headers=admin
    ).status_code == 200
    immutable = client.put(
        f"/api/v1/digital-employees/{employee_id}/versions/1",
        headers=admin,
        json={
            "system_prompt": "不能覆盖",
            "skill_version_refs": [{"resource_id": skill.json()["id"], "version": 1}],
        },
    )
    assert immutable.status_code == 409

    draft = create_version("旧草稿指令")
    assert draft.status_code == 201
    draft_update = client.put(
        f"/api/v1/digital-employees/{employee_id}/versions/2",
        headers=admin,
        json={
            "system_prompt": "更新后的草稿指令",
            "skill_version_refs": [{"resource_id": skill.json()["id"], "version": 1}],
            "tool_version_refs": [],
            "execution_profile_id": "profile-fake",
            "execution_profile_version": 1,
            "approval_policy": "none",
        },
    )
    assert draft_update.status_code == 200
    assert draft_update.json()["system_prompt"] == "更新后的草稿指令"
    assert client.put(
        f"/api/v1/digital-employees/{employee_id}/versions/2",
        headers=member,
        json={"system_prompt": "成员写入"},
    ).status_code == 403

    denied_archive = client.post(f"/api/v1/digital-employees/{employee_id}/archive", headers=member)
    assert denied_archive.status_code == 403
    archived = client.post(f"/api/v1/digital-employees/{employee_id}/archive", headers=admin)
    assert archived.status_code == 200
    assert archived.json()["status"] == "ARCHIVED"
    assert client.patch(
        f"/api/v1/digital-employees/{employee_id}",
        headers=admin,
        json={"name": "归档后不允许修改"},
    ).status_code == 409
    assert client.post(
        f"/api/v1/digital-employees/{employee_id}/versions",
        headers=admin,
        json={"system_prompt": "归档后不允许创建"},
    ).status_code == 409
    assert client.post(
        "/api/v1/deployments",
        headers=admin,
        json={"employee_id": employee_id, "version": 1},
    ).status_code == 409


def test_draft_version_test_uses_fake_runtime_without_creating_deployment_or_run() -> None:
    repository = InMemoryCatalogRepository()
    client = TestClient(create_app(mode="fake", catalog_repository=repository))
    admin = {"X-Fake-User": "admin-local"}
    skill = client.post(
        "/api/v1/skills",
        headers=admin,
        json={"name": "测试 Skill", "skill_type": "instruction", "instruction": "按入口回答"},
    )
    client.post(f"/api/v1/skills/{skill.json()['id']}/publish", headers=admin)
    employee = client.post(
        "/api/v1/digital-employees",
        headers=admin,
        json={"name": "测试员工", "role_name": "助手", "description": "预览 DRAFT"},
    )
    version = client.post(
        f"/api/v1/digital-employees/{employee.json()['id']}/versions",
        headers=admin,
        json={"system_prompt": "Draft system prompt", "skill_ids": [skill.json()["id"]]},
    )

    tested = client.post(
        f"/api/v1/digital-employees/{employee.json()['id']}/versions/{version.json()['version']}/test",
        headers=admin,
        json={"prompt": "请预览"},
    )

    assert tested.status_code == 200, tested.text
    assert tested.json()["status"] == "succeeded"
    assert tested.json()["result"] == "fake: 请预览"
    assert repository.list_deployments(
        FakeIdentityProvider().resolve_identity("admin-local")
    ) == ()
