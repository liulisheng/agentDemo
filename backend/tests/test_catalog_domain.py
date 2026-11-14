from dataclasses import FrozenInstanceError

import pytest

from agent_platform.domain.catalog import (
    Deployment,
    DigitalEmployee,
    DigitalEmployeeVersion,
    ExecutionProfile,
    LifecycleStatus,
    SkillType,
    SkillVersion,
    ToolVersion,
)


def _skill() -> SkillVersion:
    return SkillVersion(
        skill_id="skill-1",
        tenant_id="tenant-a",
        name="回声技能",
        skill_type=SkillType.TOOL_BUNDLE,
        instruction="将输入交给回声工具。",
        allowed_tool_ids=("tool-1",),
    )


def test_skill_and_tool_publish_create_immutable_snapshots() -> None:
    tool = ToolVersion(
        tool_id="tool-1",
        tenant_id="tenant-a",
        name="echo",
        description="回显文本",
        input_schema={"type": "object"},
    )
    skill = _skill()

    published_tool = tool.publish()
    published_skill = skill.publish()

    assert tool.status is LifecycleStatus.DRAFT
    assert published_tool.status is LifecycleStatus.PUBLISHED
    assert published_skill.status is LifecycleStatus.PUBLISHED
    with pytest.raises(FrozenInstanceError):
        published_skill.name = "changed"  # type: ignore[misc]


def test_employee_version_snapshot_requires_published_dependencies() -> None:
    employee = DigitalEmployee(
        employee_id="employee-1",
        tenant_id="tenant-a",
        name="行政助手",
        role_name="行政支持",
        description="处理事务",
    )
    profile = ExecutionProfile(
        profile_id="profile-1",
        tenant_id="tenant-a",
        engine_name="fake",
        model_alias="fake-model",
    ).publish()
    version = DigitalEmployeeVersion(
        employee_id=employee.employee_id,
        tenant_id=employee.tenant_id,
        version=1,
        system_prompt="你是行政助手。",
        skill_version_ids=("skill-1",),
        tool_version_ids=("tool-1",),
        execution_profile_id=profile.profile_id,
    )

    assert version.status is LifecycleStatus.DRAFT
    assert version.selected_tool_names({"tool-1": "echo"}) == ("echo",)
    published = version.publish()
    assert published.status is LifecycleStatus.PUBLISHED
    assert published.skill_version_ids == ("skill-1",)


def test_deployment_only_accepts_published_employee_version() -> None:
    with pytest.raises(ValueError, match="published"):
        Deployment(
            deployment_id="deployment-1",
            tenant_id="tenant-a",
            employee_id="employee-1",
            employee_version=DigitalEmployeeVersion(
                employee_id="employee-1",
                tenant_id="tenant-a",
                version=1,
                system_prompt="prompt",
                skill_version_ids=(),
                tool_version_ids=(),
                execution_profile_id="profile-1",
            ),
        )


def test_each_skill_type_has_a_valid_published_representation() -> None:
    instruction = SkillVersion("skill-i", "tenant-a", "指令", SkillType.INSTRUCTION, "回答")
    bundle = SkillVersion("skill-b", "tenant-a", "工具包", SkillType.TOOL_BUNDLE, "调用")
    workflow = SkillVersion(
        "skill-w", "tenant-a", "流程", SkillType.WORKFLOW, "执行", workflow_steps=("step-1",)
    )

    assert instruction.publish().status is LifecycleStatus.PUBLISHED
    assert bundle.publish().status is LifecycleStatus.PUBLISHED
    assert workflow.publish().status is LifecycleStatus.PUBLISHED
