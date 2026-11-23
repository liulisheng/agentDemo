from __future__ import annotations

from dataclasses import replace

from fastapi.testclient import TestClient

from agent_platform.adapters.identity.fake import FakeIdentityProvider
from agent_platform.api.app import create_app
from agent_platform.application.catalog_service import CatalogService, InMemoryCatalogRepository
from agent_platform.domain.catalog import (
    LifecycleStatus,
    SkillContentFormat,
    SkillFileEntry,
    SkillType,
    SkillVersion,
    compute_skill_file_manifest_sha256,
)


def _create_skill(client: TestClient, headers: dict[str, str], name: str) -> str:
    created = client.post(
        "/api/v1/skills",
        headers=headers,
        json={"name": name, "skill_type": "instruction", "instruction": f"{name} 的指令"},
    )
    assert created.status_code == 201
    skill_id = created.json()["id"]
    assert client.post(f"/api/v1/skills/{skill_id}/publish", headers=headers).status_code == 200
    return skill_id


def _create_tool(client: TestClient, headers: dict[str, str], name: str) -> str:
    created = client.post(
        "/api/v1/tools",
        headers=headers,
        json={"name": name, "description": f"{name} 的说明", "input_schema": {"type": "object"}},
    )
    assert created.status_code == 201
    tool_id = created.json()["id"]
    assert client.post(f"/api/v1/tools/{tool_id}/publish", headers=headers).status_code == 200
    return tool_id


def test_resource_binding_routes_change_only_the_selected_type_and_fork_published_version() -> None:
    repository = InMemoryCatalogRepository()
    client = TestClient(create_app(mode="fake", catalog_repository=repository))
    admin = {"X-Fake-User": "admin-local"}
    skill_a = _create_skill(client, admin, "已绑定 Skill")
    skill_b = _create_skill(client, admin, "新增 Skill")
    tool_a = _create_tool(client, admin, "已绑定 Tool")
    tool_b = _create_tool(client, admin, "新增 Tool")
    employee = client.post(
        "/api/v1/digital-employees",
        headers=admin,
        json={"name": "定向编辑员工", "role_name": "助理", "description": "验证局部资源编辑"},
    ).json()
    employee_id = employee["id"]
    version = client.post(
        f"/api/v1/digital-employees/{employee_id}/versions",
        headers=admin,
        json={"system_prompt": "必须原样保留", "skill_ids": [skill_a], "tool_ids": [tool_a]},
    )
    assert version.status_code == 201
    assert client.put(
        f"/api/v1/digital-employees/{employee_id}/versions/1",
        headers=admin,
        json={
            "system_prompt": "必须原样保留",
            "skill_version_refs": [{"resource_id": skill_a, "version": 1}],
            "tool_version_refs": [{"resource_id": tool_a, "version": 1}],
            "execution_profile_id": "profile-fake",
            "execution_profile_version": 1,
            "model_route_id": "route-fixed",
            "workspace_profile_id": "workspace-fixed",
            "workspace_profile_version": 3,
            "sandbox_profile_id": "sandbox-fixed",
            "sandbox_profile_version": 2,
            "approval_policy": "required",
        },
    ).status_code == 200
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    version_value = repository.get_employee_version(identity, employee_id, 1)
    repository.save_employee_version(
        identity,
        replace(version_value, status=LifecycleStatus.PUBLISHED),
    )

    forked = client.post(
        f"/api/v1/digital-employees/{employee_id}/versions/1/fork", headers=admin
    )
    assert forked.status_code == 201
    assert forked.json()["version"] == 2
    assert forked.json()["status"] == "DRAFT"
    assert forked.json()["system_prompt"] == "必须原样保留"
    assert forked.json()["skill_version_refs"] == [{"skill_id": skill_a, "version": 1}]
    assert forked.json()["tool_version_refs"] == [{"tool_id": tool_a, "version": 1}]
    assert not {
        "execution_profile_id",
        "model_route_id",
        "workspace_profile_id",
        "sandbox_profile_id",
        "approval_policy",
    } & forked.json().keys()
    forked_value = repository.get_employee_version(identity, employee_id, 2)
    assert forked_value.execution_profile_id is None
    assert forked_value.model_route_id is None
    assert forked_value.workspace_profile_id is None
    assert forked_value.sandbox_profile_id is None
    assert forked_value.approval_policy is None

    skill_update = client.put(
        f"/api/v1/digital-employees/{employee_id}/versions/2/skill-bindings",
        headers=admin,
        json={"skill_version_refs": [{"resource_id": skill_b, "version": 1}]},
    )
    assert skill_update.status_code == 200
    assert skill_update.json()["skill_version_refs"] == [{"skill_id": skill_b, "version": 1}]
    assert skill_update.json()["tool_version_refs"] == [{"tool_id": tool_a, "version": 1}]
    assert skill_update.json()["system_prompt"] == "必须原样保留"
    assert "model_route_id" not in skill_update.json()
    assert "approval_policy" not in skill_update.json()

    tool_update = client.put(
        f"/api/v1/digital-employees/{employee_id}/versions/2/tool-bindings",
        headers=admin,
        json={"tool_version_refs": [{"resource_id": tool_b, "version": 1}]},
    )
    assert tool_update.status_code == 200
    assert tool_update.json()["skill_version_refs"] == [{"skill_id": skill_b, "version": 1}]
    assert tool_update.json()["tool_version_refs"] == [{"tool_id": tool_b, "version": 1}]
    assert tool_update.json()["system_prompt"] == "必须原样保留"
    assert "model_route_id" not in tool_update.json()
    assert "approval_policy" not in tool_update.json()

    duplicate_skills = client.put(
        f"/api/v1/digital-employees/{employee_id}/versions/2/skill-bindings",
        headers=admin,
        json={"skill_version_refs": [
            {"resource_id": skill_b, "version": 1},
            {"resource_id": skill_b, "version": 1},
        ]},
    )
    assert duplicate_skills.status_code == 422
    duplicate_tools = client.put(
        f"/api/v1/digital-employees/{employee_id}/versions/2/tool-bindings",
        headers=admin,
        json={"tool_version_refs": [
            {"resource_id": tool_b, "version": 1},
            {"resource_id": tool_b, "version": 1},
        ]},
    )
    assert duplicate_tools.status_code == 422

    original = client.get(f"/api/v1/digital-employees/{employee_id}/versions/1", headers=admin).json()
    assert original["status"] == "PUBLISHED"
    assert original["skill_version_refs"] == [{"skill_id": skill_a, "version": 1}]
    assert original["tool_version_refs"] == [{"tool_id": tool_a, "version": 1}]
    assert client.put(
        f"/api/v1/digital-employees/{employee_id}/versions/1/skill-bindings",
        headers=admin,
        json={"skill_version_refs": []},
    ).status_code == 409


def test_resource_binding_routes_enforce_member_and_tenant_boundaries() -> None:
    repository = InMemoryCatalogRepository()
    client = TestClient(create_app(mode="fake", catalog_repository=repository))
    admin = {"X-Fake-User": "admin-local"}
    member = {"X-Fake-User": "member-local"}
    other_admin = {"X-Fake-User": "admin-other"}
    own_skill = _create_skill(client, admin, "本租户 Skill")
    foreign_skill = _create_skill(client, other_admin, "其他租户 Skill")
    draft_skill = client.post(
        "/api/v1/skills",
        headers=admin,
        json={"name": "草稿 Skill", "skill_type": "instruction", "instruction": "尚未发布"},
    ).json()["id"]
    employee = client.post(
        "/api/v1/digital-employees",
        headers=admin,
        json={"name": "权限员工", "role_name": "助理", "description": "测试租户隔离"},
    ).json()
    employee_id = employee["id"]
    assert client.post(
        f"/api/v1/digital-employees/{employee_id}/versions",
        headers=admin,
        json={"system_prompt": "只读配置", "skill_ids": [own_skill]},
    ).status_code == 201

    assert client.post(
        f"/api/v1/digital-employees/{employee_id}/versions/1/fork", headers=member
    ).status_code == 403
    assert client.post(
        f"/api/v1/digital-employees/{employee_id}/versions/1/fork", headers=other_admin
    ).status_code == 404
    assert client.post(
        f"/api/v1/digital-employees/{employee_id}/versions/1/fork", headers=admin
    ).status_code == 409

    assert client.put(
        f"/api/v1/digital-employees/{employee_id}/versions/1/skill-bindings",
        headers=member,
        json={"skill_version_refs": []},
    ).status_code == 403
    assert client.put(
        f"/api/v1/digital-employees/{employee_id}/versions/1/skill-bindings",
        headers=admin,
        json={"skill_version_refs": [{"resource_id": foreign_skill, "version": 1}]},
    ).status_code == 404
    assert client.put(
        f"/api/v1/digital-employees/{employee_id}/versions/1/skill-bindings",
        headers=admin,
        json={"skill_version_refs": [{"resource_id": draft_skill, "version": 1}]},
    ).status_code == 409
    assert client.get(f"/api/v1/digital-employees/{employee_id}/versions/1", headers=admin).json()[
        "skill_version_refs"
    ] == [{"skill_id": own_skill, "version": 1}]


def test_skill_version_can_be_forked_and_published_by_exact_version() -> None:
    client = TestClient(create_app(mode="fake", catalog_repository=InMemoryCatalogRepository()))
    admin = {"X-Fake-User": "admin-local"}
    skill_id = _create_skill(client, admin, "Skill 版本")

    forked = client.post(f"/api/v1/skills/{skill_id}/versions/1/fork", headers=admin)
    assert forked.status_code == 201
    assert forked.json()["version"] == 2
    assert forked.json()["status"] == "DRAFT"
    assert forked.json()["instruction"] == "Skill 版本 的指令"

    published = client.post(f"/api/v1/skills/{skill_id}/versions/2/publish", headers=admin)
    assert published.status_code == 200
    assert published.json()["version"] == 2
    assert published.json()["status"] == "PUBLISHED"


def test_skill_picker_can_list_published_history_when_latest_version_is_a_draft() -> None:
    client = TestClient(create_app(mode="fake", catalog_repository=InMemoryCatalogRepository()))
    admin = {"X-Fake-User": "admin-local"}
    skill_id = _create_skill(client, admin, "保留已发布版本")
    forked = client.post(f"/api/v1/skills/{skill_id}/versions/1/fork", headers=admin)
    assert forked.status_code == 201

    response = client.get("/api/v1/skills", headers=admin, params={"include_versions": "true"})

    assert response.status_code == 200
    assert [
        (item["id"], item["version"], item["status"])
        for item in response.json()["items"]
        if item["id"] == skill_id
    ] == [(skill_id, 1, "PUBLISHED"), (skill_id, 2, "DRAFT")]


def test_skill_fork_reuses_immutable_file_references_and_preserves_manifest() -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    repository = InMemoryCatalogRepository()
    entry = SkillFileEntry(
        file_path="SKILL.md",
        media_asset_id="asset-immutable-entry",
        mime_type="text/markdown",
        size_bytes=7,
        content_sha256="b" * 64,
    )
    manifest = compute_skill_file_manifest_sha256([entry])
    published = SkillVersion(
        skill_id="skill-package",
        tenant_id=identity.tenant_id,
        name="文件包 Skill",
        skill_type=SkillType.INSTRUCTION,
        instruction="",
        status=LifecycleStatus.PUBLISHED,
        content_format=SkillContentFormat.FILE_PACKAGE,
        entry_path="SKILL.md",
        file_manifest_sha256=manifest,
        file_entries=(entry,),
    )
    repository.save_skill(identity, published)
    catalog = CatalogService(repository)

    forked = catalog.fork_skill_version(identity, published.skill_id, 1)

    assert forked.version == 2
    assert forked.status is LifecycleStatus.DRAFT
    assert forked.file_manifest_sha256 == manifest
    assert forked.file_entries == (entry,)
    assert repository.get_skill_version(identity, published.skill_id, 1) == published


def test_hub_reference_skill_cannot_be_forked_before_copy() -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    repository = InMemoryCatalogRepository()
    reference = SkillVersion(
        skill_id="skill-hub-reference",
        tenant_id=identity.tenant_id,
        name="引用 Skill",
        skill_type=SkillType.INSTRUCTION,
        instruction="hub-reference://resource/version/1",
        status=LifecycleStatus.PUBLISHED,
        content_format=SkillContentFormat.FILE_PACKAGE,
        entry_path="SKILL.md",
        file_manifest_sha256="c" * 64,
    )
    repository.save_skill(identity, reference)
    client = TestClient(create_app(mode="fake", catalog_repository=repository))

    response = client.post(
        f"/api/v1/skills/{reference.skill_id}/versions/1/fork",
        headers={"X-Fake-User": "admin-local"},
    )

    assert response.status_code == 409
