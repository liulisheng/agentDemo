import pytest

from agent_platform.application.skill_executor import SkillExecutor
from agent_platform.domain.catalog import SkillType, SkillVersion
from agent_platform.domain.common import DomainError


def test_instruction_skill_validates_input_and_returns_contract() -> None:
    skill = SkillVersion(
        "skill-1",
        "tenant-a",
        "回答",
        SkillType.INSTRUCTION,
        "回答用户",
        input_schema={"type": "object", "required": ["text"]},
        output_schema={"type": "object", "required": ["text"]},
    ).publish()

    execution = SkillExecutor().execute(skill, {"text": "你好"}, allowed_tools=())

    assert execution.status == "succeeded"
    assert execution.output["text"] == "你好"


def test_tool_bundle_rejects_undeclared_tool_without_calling_handler() -> None:
    skill = SkillVersion(
        "skill-1", "tenant-a", "工具", SkillType.TOOL_BUNDLE, "调用", ("tool-1",)
    ).publish()
    called = False

    def handler(_: dict[str, object]) -> object:
        nonlocal called
        called = True
        return {"ok": True}

    with pytest.raises(DomainError) as error:
        SkillExecutor().execute(
            skill,
            {"tool": "dangerous"},
            allowed_tools=("echo",),
            handlers={"dangerous": handler},
        )
    assert error.value.code == "POLICY_DENIED"
    assert not called


def test_workflow_records_step_states() -> None:
    skill = SkillVersion(
        "skill-1",
        "tenant-a",
        "流程",
        SkillType.WORKFLOW,
        "执行",
        workflow_steps=("prepare", "finish"),
    ).publish()

    execution = SkillExecutor().execute(skill, {}, allowed_tools=())

    assert execution.status == "succeeded"
    assert [step["status"] for step in execution.steps] == ["succeeded", "succeeded"]
