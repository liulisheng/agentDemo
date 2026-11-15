"""租户管理台 Tool/Skill 新建、草稿维护和发布 API 验收。"""

from fastapi.testclient import TestClient

from agent_platform.api.app import create_app


def test_admin_can_create_update_and_publish_tool_and_skill() -> None:
    client = TestClient(create_app(mode="fake"))
    admin = {"X-Fake-User": "admin-local"}

    tool = client.post(
        "/api/v1/tools",
        headers=admin,
        json={"name": "工单查询", "description": "读取工单", "input_schema": {"type": "object"}},
    )
    assert tool.status_code == 201
    tool_id = tool.json()["id"]
    updated = client.patch(
        f"/api/v1/tools/{tool_id}",
        headers=admin,
        json={"name": "读取工单状态", "description": "读取工单状态", "input_schema": {"type": "object", "properties": {"id": {"type": "string"}}}},
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "读取工单状态"
    assert updated.json()["status"] == "DRAFT"
    published_tool = client.post(f"/api/v1/tools/{tool_id}/publish", headers=admin)
    assert published_tool.status_code == 200
    assert published_tool.json()["status"] == "PUBLISHED"

    skill = client.post(
        "/api/v1/skills",
        headers=admin,
        json={
            "name": "工单处理",
            "skill_type": "instruction",
            "instruction": "按工单流程处理",
            "allowed_tool_ids": [tool_id],
        },
    )
    assert skill.status_code == 201
    published_skill = client.post(f"/api/v1/skills/{skill.json()['id']}/publish", headers=admin)
    assert published_skill.status_code == 200
    assert published_skill.json()["status"] == "PUBLISHED"


def test_member_cannot_create_or_publish_tool_or_skill() -> None:
    client = TestClient(create_app(mode="fake"))
    member = {"X-Fake-User": "member-local"}

    tool = client.post(
        "/api/v1/tools",
        headers=member,
        json={"name": "越权工具", "description": "不应创建"},
    )
    skill = client.post(
        "/api/v1/skills",
        headers=member,
        json={"name": "越权技能", "skill_type": "instruction", "instruction": "不应创建"},
    )
    assert tool.status_code == 403
    assert skill.status_code == 403
