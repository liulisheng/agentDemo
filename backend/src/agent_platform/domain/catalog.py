"""数字员工、Skill、Tool 和部署的版本化领域对象。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum

from .common import DomainError


class LifecycleStatus(StrEnum):
    """控制面资源的最小生命周期。"""

    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    DEPRECATED = "DEPRECATED"
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class SkillType(StrEnum):
    """Skill 对 Runtime 暴露的行为类型。"""

    INSTRUCTION = "instruction"
    TOOL_BUNDLE = "tool-bundle"
    WORKFLOW = "workflow"


def _text(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    return value.strip()


def _scope(tenant_id: str) -> str:
    return _text(tenant_id, "tenant_id")


@dataclass(frozen=True, slots=True)
class ToolVersion:
    """可被 Skill 声明并由部署策略授权的工具版本。"""

    tool_id: str
    tenant_id: str
    name: str
    description: str
    input_schema: Mapping[str, object]
    version: int = 1
    status: LifecycleStatus = LifecycleStatus.DRAFT
    handler_name: str = "echo"

    def __post_init__(self) -> None:
        _text(self.tool_id, "tool_id")
        _scope(self.tenant_id)
        _text(self.name, "name")
        _text(self.description, "description")
        if self.version < 1:
            raise ValueError("version must be positive")
        if not isinstance(self.input_schema, Mapping):
            raise TypeError("input_schema must be an object")

    def publish(self) -> ToolVersion:
        self.assert_mutable()
        return replace(self, status=LifecycleStatus.PUBLISHED)

    def deprecate(self) -> ToolVersion:
        if self.status is not LifecycleStatus.PUBLISHED:
            raise DomainError("INVALID_STATE", "只有已发布 Tool 才能废弃")
        return replace(self, status=LifecycleStatus.DEPRECATED)

    def assert_mutable(self) -> None:
        if self.status is not LifecycleStatus.DRAFT:
            raise DomainError("IMMUTABLE_VERSION", "已发布或废弃的 Tool 版本不可修改")


@dataclass(frozen=True, slots=True)
class SkillVersion:
    """包含指令、契约和工具声明的可复用 Skill 版本。"""

    skill_id: str
    tenant_id: str
    name: str
    skill_type: SkillType
    instruction: str
    allowed_tool_ids: tuple[str, ...] = ()
    input_schema: Mapping[str, object] = field(default_factory=lambda: {"type": "object"})
    output_schema: Mapping[str, object] = field(default_factory=lambda: {"type": "object"})
    trigger: str | None = None
    workflow_steps: tuple[str, ...] = ()
    memory_policy: str = "none"
    approval_policy: str = "none"
    version: int = 1
    status: LifecycleStatus = LifecycleStatus.DRAFT

    def __post_init__(self) -> None:
        _text(self.skill_id, "skill_id")
        _scope(self.tenant_id)
        _text(self.name, "name")
        _text(self.instruction, "instruction")
        if self.version < 1:
            raise ValueError("version must be positive")
        if not isinstance(self.allowed_tool_ids, tuple):
            raise TypeError("allowed_tool_ids must be a tuple")

    def publish(self) -> SkillVersion:
        self.assert_mutable()
        if self.skill_type is SkillType.WORKFLOW and not self.workflow_steps:
            raise DomainError("DEPENDENCY_VALIDATION_ERROR", "workflow Skill 必须包含步骤")
        return replace(self, status=LifecycleStatus.PUBLISHED)

    def deprecate(self) -> SkillVersion:
        if self.status is not LifecycleStatus.PUBLISHED:
            raise DomainError("INVALID_STATE", "只有已发布 Skill 才能废弃")
        return replace(self, status=LifecycleStatus.DEPRECATED)

    def assert_mutable(self) -> None:
        if self.status is not LifecycleStatus.DRAFT:
            raise DomainError("IMMUTABLE_VERSION", "已发布或废弃的 Skill 版本不可修改")


@dataclass(frozen=True, slots=True)
class DigitalEmployee:
    """数字员工的稳定身份，版本内容单独保存在 DigitalEmployeeVersion。"""

    employee_id: str
    tenant_id: str
    name: str
    role_name: str
    description: str
    status: LifecycleStatus = LifecycleStatus.DRAFT

    def __post_init__(self) -> None:
        for field_name in ("employee_id", "tenant_id", "name", "role_name", "description"):
            _text(getattr(self, field_name), field_name)


@dataclass(frozen=True, slots=True)
class ExecutionProfile:
    """把引擎和策略组合成可被 Run 固化的配置版本。"""

    profile_id: str
    tenant_id: str
    engine_name: str
    model_alias: str
    tool_policy: tuple[str, ...] = ()
    context_policy: str = "default"
    version: int = 1
    status: LifecycleStatus = LifecycleStatus.DRAFT
    model_route_id: str | None = None
    workspace_profile_id: str | None = None
    sandbox_profile_id: str | None = None
    approval_policy: str = "none"

    def __post_init__(self) -> None:
        for field_name in ("profile_id", "tenant_id", "engine_name", "model_alias"):
            _text(getattr(self, field_name), field_name)
        for field_name in ("model_route_id", "workspace_profile_id", "sandbox_profile_id"):
            value = getattr(self, field_name)
            if value is not None:
                _text(value, field_name)
        if self.version < 1:
            raise ValueError("version must be positive")

    def publish(self) -> ExecutionProfile:
        if self.status is not LifecycleStatus.DRAFT:
            raise DomainError("IMMUTABLE_VERSION", "已发布 ExecutionProfile 不可修改")
        return replace(self, status=LifecycleStatus.PUBLISHED)


@dataclass(frozen=True, slots=True)
class DigitalEmployeeVersion:
    """固化员工指令、Skill/Tool 绑定和 ExecutionProfile 的不可变快照。"""

    employee_id: str
    tenant_id: str
    version: int
    system_prompt: str
    skill_version_ids: tuple[str, ...]
    tool_version_ids: tuple[str, ...]
    execution_profile_id: str
    status: LifecycleStatus = LifecycleStatus.DRAFT
    model_route_id: str | None = None
    workspace_profile_id: str | None = None
    sandbox_profile_id: str | None = None
    approval_policy: str = "none"

    def __post_init__(self) -> None:
        for field_name in ("employee_id", "tenant_id", "system_prompt", "execution_profile_id"):
            _text(getattr(self, field_name), field_name)
        if self.version < 1:
            raise ValueError("version must be positive")
        if not isinstance(self.skill_version_ids, tuple) or not isinstance(self.tool_version_ids, tuple):
            raise TypeError("version references must be tuples")
        for field_name in ("model_route_id", "workspace_profile_id", "sandbox_profile_id"):
            value = getattr(self, field_name)
            if value is not None:
                _text(value, field_name)

    def publish(self) -> DigitalEmployeeVersion:
        self.assert_mutable()
        if not self.skill_version_ids:
            raise DomainError("DEPENDENCY_VALIDATION_ERROR", "数字员工至少需要一个 Skill")
        return replace(self, status=LifecycleStatus.PUBLISHED)

    def assert_mutable(self) -> None:
        if self.status is not LifecycleStatus.DRAFT:
            raise DomainError("IMMUTABLE_VERSION", "已发布员工版本不可修改")

    def deprecate(self) -> DigitalEmployeeVersion:
        if self.status is not LifecycleStatus.PUBLISHED:
            raise DomainError("INVALID_STATE", "只有已发布员工版本才能废弃")
        return replace(self, status=LifecycleStatus.DEPRECATED)

    def selected_tool_names(self, names_by_id: Mapping[str, str]) -> tuple[str, ...]:
        """按员工版本声明顺序返回工具名，供 Runtime 请求使用。"""

        return tuple(names_by_id[tool_id] for tool_id in self.tool_version_ids if tool_id in names_by_id)


@dataclass(frozen=True, slots=True)
class Deployment:
    """把一个已发布员工版本固定到租户范围。"""

    deployment_id: str
    tenant_id: str
    employee_id: str
    employee_version: DigitalEmployeeVersion
    status: LifecycleStatus = LifecycleStatus.ACTIVE

    def __post_init__(self) -> None:
        _text(self.deployment_id, "deployment_id")
        _scope(self.tenant_id)
        _text(self.employee_id, "employee_id")
        if self.employee_version.status is not LifecycleStatus.PUBLISHED:
            raise ValueError("deployment requires a published employee version")
        if self.employee_version.tenant_id != self.tenant_id:
            raise ValueError("deployment scope does not match employee version")
        if self.employee_version.employee_id != self.employee_id:
            raise ValueError("deployment employee does not match version")

    def rollback(self, employee_version: DigitalEmployeeVersion) -> Deployment:
        """创建指向旧发布版本的新绑定，已有 Run 的快照不受影响。"""

        return replace(self, employee_version=employee_version)
