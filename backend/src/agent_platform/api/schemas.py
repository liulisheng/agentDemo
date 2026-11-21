"""最小闭环 HTTP 请求模型，身份和租户仍由依赖注入解析。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from agent_platform.domain.catalog import SkillType
from agent_platform.domain.resource_center import HubImportMode, HubResourceType, HubVisibility


class TenantAwareRequest(BaseModel):
    tenant_id: str | None = None


class ToolCreateRequest(TenantAwareRequest):
    name: str = Field(min_length=1, max_length=128)
    description: str = ""
    input_schema: dict[str, object] = Field(default_factory=lambda: {"type": "object"})
    protocol: str = "builtin"
    requires_recipient_credentials: bool = False
    auth_secret_ref: str | None = None


class ToolUpdateRequest(TenantAwareRequest):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=1024)
    input_schema: dict[str, object] | None = None


class SkillCreateRequest(TenantAwareRequest):
    name: str = Field(min_length=1, max_length=128)
    skill_type: SkillType
    instruction: str
    allowed_tool_ids: list[str] = Field(default_factory=list)


class EmployeeCreateRequest(TenantAwareRequest):
    name: str = Field(min_length=1, max_length=128)
    role_name: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=1024)


class EmployeeUpdateRequest(TenantAwareRequest):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    role_name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, min_length=1, max_length=1024)


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


class ResourceVersionRefRequest(BaseModel):
    resource_id: str = Field(min_length=1, max_length=64)
    version: int = Field(ge=1)


class EmployeeVersionUpdateRequest(TenantAwareRequest):
    system_prompt: str = Field(min_length=1, max_length=65535)
    skill_version_refs: list[ResourceVersionRefRequest] = Field(default_factory=list)
    tool_version_refs: list[ResourceVersionRefRequest] = Field(default_factory=list)
    execution_profile_id: str = Field(default="profile-fake", min_length=1, max_length=64)
    execution_profile_version: int = Field(default=1, ge=1)
    model_route_id: str | None = Field(default=None, max_length=64)
    workspace_profile_id: str | None = Field(default=None, max_length=64)
    workspace_profile_version: int | None = Field(default=None, ge=1)
    sandbox_profile_id: str | None = Field(default=None, max_length=64)
    sandbox_profile_version: int | None = Field(default=None, ge=1)
    approval_policy: str = Field(default="none", max_length=64)


class EmployeeSkillBindingsUpdateRequest(TenantAwareRequest):
    skill_version_refs: list[ResourceVersionRefRequest] = Field(default_factory=list)


class EmployeeToolBindingsUpdateRequest(TenantAwareRequest):
    tool_version_refs: list[ResourceVersionRefRequest] = Field(default_factory=list)


class EmployeeVersionTestRequest(TenantAwareRequest):
    prompt: str = Field(min_length=1, max_length=8192)


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


class ConversationTurnRequest(TenantAwareRequest):
    text: str = Field(min_length=1, max_length=65535)
    idempotency_key: str = Field(min_length=1, max_length=255)
    employee_id: str | None = Field(default=None, min_length=1, max_length=64)


class ToolApprovalApproveRequest(TenantAwareRequest):
    preview_sha256: str = Field(min_length=64, max_length=64)
    idempotency_key: str = Field(min_length=1, max_length=255)


class ToolApprovalRejectRequest(TenantAwareRequest):
    pass


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


class HubPackageCreateRequest(TenantAwareRequest):
    resource_type: HubResourceType
    source_resource_id: str
    source_resource_version: int
    visibility: HubVisibility = HubVisibility.PLATFORM


class HubPackageVersionCreateRequest(TenantAwareRequest):
    source_resource_id: str
    source_resource_version: int


class HubResourceImportRequest(TenantAwareRequest):
    hub_resource_id: str
    hub_version: int
    mode: HubImportMode


class HubGrantCreateRequest(TenantAwareRequest):
    recipient_tenant_id: str = Field(min_length=1, max_length=26)
    copy_allowed: bool = False
    reference_allowed: bool = False
    expires_at: datetime | None = None


class HubImportUpgradeRequest(TenantAwareRequest):
    hub_version: int = Field(ge=1)


class SkillFileCreateRequest(TenantAwareRequest):
    file_path: str
    content_text: str | None = None
    content_base64: str | None = None
    mime_type: str | None = None


class SkillFileUpdateRequest(SkillFileCreateRequest):
    expected_sha256: str


class SkillFileRenameRequest(TenantAwareRequest):
    file_path: str
    new_file_path: str
    expected_sha256: str
