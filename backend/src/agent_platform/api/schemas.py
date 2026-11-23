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


class ResourceVersionRefRequest(BaseModel):
    resource_id: str = Field(min_length=1, max_length=64)
    version: int = Field(ge=1)


class EmployeeVersionUpdateRequest(TenantAwareRequest):
    system_prompt: str = Field(min_length=1, max_length=65535)
    skill_version_refs: list[ResourceVersionRefRequest] = Field(default_factory=list)
    tool_version_refs: list[ResourceVersionRefRequest] = Field(default_factory=list)


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
    alias: str = Field(min_length=1, max_length=128)
    model_name: str | None = Field(default=None, min_length=1, max_length=255)
    provider: str = Field(default="fake", min_length=1, max_length=64)
    endpoint: str = Field(default="fake://model", min_length=1, max_length=1024)
    secret_id: str | None = None
    connection_id: str | None = Field(default=None, min_length=1, max_length=64)
    catalog_model_id: str | None = Field(default=None, min_length=1, max_length=255)
    parameters: dict[str, object] = Field(default_factory=dict)
    pricing_snapshot_ref: str | None = None


class ModelRouteUpdateRequest(TenantAwareRequest):
    expected_route_version: int = Field(ge=1)
    alias: str | None = Field(default=None, min_length=1, max_length=128)
    connection_id: str | None = Field(default=None, min_length=1, max_length=64)
    catalog_model_id: str | None = Field(default=None, min_length=1, max_length=255)
    model_name: str | None = Field(default=None, min_length=1, max_length=255)
    parameters: dict[str, object] | None = None
    pricing_snapshot_ref: str | None = None


class ModelConnectionCreateRequest(TenantAwareRequest):
    name: str = Field(min_length=1, max_length=128)
    provider_kind: str = "openai-compatible"
    protocol: str = "openai-chat-completions"
    endpoint: str = Field(min_length=1, max_length=1024)
    secret_id: str | None = Field(default=None, min_length=1, max_length=255)
    catalog_mode: str = "REMOTE"


class ModelConnectionUpdateRequest(TenantAwareRequest):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    provider_kind: str | None = Field(default=None, min_length=1, max_length=64)
    protocol: str | None = Field(default=None, min_length=1, max_length=64)
    endpoint: str | None = Field(default=None, min_length=1, max_length=1024)
    secret_id: str | None = Field(default=None, min_length=1, max_length=255)
    catalog_mode: str | None = None
    status: str | None = None


class ModelCatalogItemCreateRequest(TenantAwareRequest):
    model_id: str = Field(min_length=1, max_length=255)
    display_name: str = Field(min_length=1, max_length=255)
    capabilities: dict[str, object] = Field(default_factory=dict)
    context_window: int | None = Field(default=None, ge=1)


class ModelTestCreateRequest(TenantAwareRequest):
    confirmed_cost: bool = Field(
        description="管理员已确认本次会向所选模型发送真实测试请求并可能产生费用"
    )


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
    model_alias: str | None = None
    model_route_id: str | None = None
    workspace_profile_id: str | None = None
    workspace_profile_version: int | None = None
    sandbox_profile_id: str | None = None
    sandbox_profile_version: int | None = None
    approval_policy: str = "none"


class ExecutionProfileVersionUpdateRequest(TenantAwareRequest):
    """完整替换一个 DRAFT ExecutionProfile 版本的引擎与环境策略。"""

    engine_name: str = Field(min_length=1, max_length=64)
    workspace_profile_id: str | None = Field(default=None, min_length=1, max_length=64)
    workspace_profile_version: int | None = Field(default=None, ge=1)
    sandbox_profile_id: str | None = Field(default=None, min_length=1, max_length=64)
    sandbox_profile_version: int | None = Field(default=None, ge=1)
    approval_policy: str = Field(default="none", min_length=1, max_length=64)


class ExecutionProfileDefaultRequest(TenantAwareRequest):
    profile_id: str | None = Field(default=None, min_length=1, max_length=64)
    profile_version: int | None = Field(default=None, ge=1)


class ModelRouteDefaultRequest(TenantAwareRequest):
    route_id: str | None = Field(default=None, min_length=1, max_length=64)

class DeploymentRollbackRequest(TenantAwareRequest):
    employee_id: str
    version: int


class DeploymentRuntimeConfigRequest(TenantAwareRequest):
    expected_config_version: int | None = Field(default=None, ge=0)
    mode: str = Field(min_length=1, max_length=16)
    execution_profile_id: str | None = Field(default=None, min_length=1, max_length=64)
    execution_profile_version: int | None = Field(default=None, ge=1)
    model_route_id: str | None = Field(default=None, min_length=1, max_length=64)
    workspace_profile_id: str | None = Field(default=None, min_length=1, max_length=64)
    workspace_profile_version: int | None = Field(default=None, ge=1)
    sandbox_profile_id: str | None = Field(default=None, min_length=1, max_length=64)
    sandbox_profile_version: int | None = Field(default=None, ge=1)
    approval_policy: dict[str, object] = Field(default_factory=dict)
    runtime_options: dict[str, object] = Field(default_factory=dict)


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
