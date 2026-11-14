"""最小闭环 HTTP 请求模型，身份和租户仍由依赖注入解析。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from agent_platform.domain.catalog import SkillType


class TenantAwareRequest(BaseModel):
    tenant_id: str | None = None


class ToolCreateRequest(TenantAwareRequest):
    name: str
    description: str = ""
    input_schema: dict[str, object] = Field(default_factory=lambda: {"type": "object"})


class SkillCreateRequest(TenantAwareRequest):
    name: str
    skill_type: SkillType
    instruction: str
    allowed_tool_ids: list[str] = Field(default_factory=list)


class EmployeeCreateRequest(TenantAwareRequest):
    name: str
    role_name: str
    description: str


class EmployeeVersionCreateRequest(TenantAwareRequest):
    system_prompt: str
    skill_ids: list[str] = Field(default_factory=list)
    tool_ids: list[str] = Field(default_factory=list)
    execution_profile_id: str = "profile-fake"


class DeploymentCreateRequest(TenantAwareRequest):
    employee_id: str
    version: int


class SessionCreateRequest(TenantAwareRequest):
    deployment_id: str


class RunCreateRequest(TenantAwareRequest):
    prompt: str
    idempotency_key: str | None = None
