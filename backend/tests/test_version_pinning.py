from agent_platform.domain.catalog import (
    DigitalEmployeeVersion,
    LifecycleStatus,
    SkillType,
    SkillVersion,
)


def test_skill_and_employee_versions_pin_exact_dependency_versions() -> None:
    skill = SkillVersion(
        "skill-1",
        "tenant-a",
        "查询",
        SkillType.TOOL_BUNDLE,
        "调用 tool v2",
        allowed_tool_ids=("tool-1",),
        allowed_tool_versions=(("tool-1", 2),),
        version=3,
    )
    employee = DigitalEmployeeVersion(
        employee_id="employee-1",
        tenant_id="tenant-a",
        version=4,
        system_prompt="prompt",
        skill_version_ids=(skill.skill_id,),
        tool_version_ids=("tool-1",),
        execution_profile_id="profile-1",
        status=LifecycleStatus.PUBLISHED,
        skill_version_refs=((skill.skill_id, skill.version),),
        tool_version_refs=(("tool-1", 2),),
        execution_profile_version=5,
    )

    assert skill.allowed_tool_versions == (("tool-1", 2),)
    assert employee.skill_version_refs == (("skill-1", 3),)
    assert employee.tool_version_refs == (("tool-1", 2),)
    assert employee.execution_profile_version == 5
