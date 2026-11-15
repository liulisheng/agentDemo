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
    model_route_id: str | None = None
    workspace_profile_id: str | None = None
    workspace_profile_version: int | None = None
    sandbox_profile_id: str | None = None
    sandbox_profile_version: int | None = None
    approval_policy: str = "none"


class DeploymentCreateRequest(TenantAwareRequest):
    employee_id: str
    version: int


class SessionCreateRequest(TenantAwareRequest):
    deployment_id: str


class RunCreateRequest(TenantAwareRequest):
    prompt: str
    idempotency_key: str | None = None

class ModelRouteCreateRequest(TenantAwareRequest):
    alias: str
    model_name: str
    provider: str = "fake"
    endpoint: str = "fake://model"
    secret_id: str | None = None


class ConversationCreateRequest(TenantAwareRequest):
    title: str
    is_group: bool = False


class AgentBindingRequest(TenantAwareRequest):
    employee_id: str
    employee_name: str
    session_id: str


class MessageCreateRequest(TenantAwareRequest):
    sender_id: str
    text: str
    source: str = "user"
    routing_depth: int = 0


class DeliveryCreateRequest(TenantAwareRequest):
    body: str
    idempotency_key: str

class ExecutionProfileCreateRequest(TenantAwareRequest):
    engine_name: str = "fake"
    model_alias: str = "fake-model"
    model_route_id: str | None = None
    workspace_profile_id: str | None = None
    workspace_profile_version: int | None = None
    sandbox_profile_id: str | None = None
    sandbox_profile_version: int | None = None
    approval_policy: str = "none"

class DeploymentRollbackRequest(TenantAwareRequest):
    employee_id: str
    version: int
