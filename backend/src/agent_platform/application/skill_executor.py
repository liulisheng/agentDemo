"""Skill 执行器：在 Runtime 之前校验契约并执行受策略允许的工具。"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from uuid import uuid4

from agent_platform.domain.catalog import SkillType, SkillVersion
from agent_platform.domain.common import DomainError


@dataclass(frozen=True, slots=True)
class SkillExecution:
    """一次 SkillExecution 的可审计结果，不保存隐藏推理正文。"""

    execution_id: str
    skill_id: str
    status: str
    input: Mapping[str, object]
    output: Mapping[str, object]
    selected_tools: tuple[str, ...]
    steps: tuple[Mapping[str, str], ...] = ()
    error_category: str | None = None


class SkillExecutor:
    """执行 instruction、tool-bundle、workflow 三种 Skill 类型。"""

    def execute(
        self,
        skill: SkillVersion,
        input_data: Mapping[str, object],
        *,
        allowed_tools: Sequence[str],
        handlers: Mapping[str, Callable[[dict[str, object]], object]] | None = None,
    ) -> SkillExecution:
        self._validate_schema(skill.input_schema, input_data)
        handlers = handlers or {}
        selected_tools = tuple(
            tool_id for tool_id in skill.allowed_tool_ids if tool_id in allowed_tools
        )
        execution_id = f"skill-execution-{uuid4().hex}"
        if skill.skill_type is SkillType.INSTRUCTION:
            output = {"text": str(input_data.get("text", ""))}
            self._validate_schema(skill.output_schema, output)
            return SkillExecution(execution_id, skill.skill_id, "succeeded", input_data, output, selected_tools)
        if skill.skill_type is SkillType.TOOL_BUNDLE:
            tool_name = str(input_data.get("tool", ""))
            if not tool_name or tool_name not in allowed_tools:
                raise DomainError("POLICY_DENIED", "Skill 请求的工具不在允许交集内", {"tool": tool_name})
            handler = handlers.get(tool_name)
            if handler is None:
                raise DomainError("RESOURCE_NOT_FOUND", "工具处理器不存在", {"tool": tool_name})
            output_value = handler(dict(input_data))
            output = output_value if isinstance(output_value, Mapping) else {"value": output_value}
            self._validate_schema(skill.output_schema, output)
            return SkillExecution(execution_id, skill.skill_id, "succeeded", input_data, output, selected_tools)
        steps = tuple({"name": step, "status": "succeeded"} for step in skill.workflow_steps)
        output = {"steps": [step["name"] for step in steps]}
        self._validate_schema(skill.output_schema, output)
        return SkillExecution(execution_id, skill.skill_id, "succeeded", input_data, output, selected_tools, steps)

    @staticmethod
    def _validate_schema(schema: Mapping[str, object], value: Mapping[str, object]) -> None:
        required = schema.get("required", [])
        for field_name in required if isinstance(required, list) else []:
            if field_name not in value:
                raise DomainError("INVALID_ARGUMENT", "Skill 输入输出不符合 Schema", {"field": field_name})
