"""数字员工目录和 Deployment 的持久化端口。"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from agent_platform.domain.catalog import (
    AuditEventRecord,
    Deployment,
    DigitalEmployee,
    DigitalEmployeeVersion,
    ExecutionProfile,
    LifecycleStatus,
    SkillVersion,
    ToolVersion,
)
from agent_platform.domain.conversations import CapabilityBinding
from agent_platform.domain.identity import IdentityContext
from agent_platform.domain.model_routes import (
    ModelCatalogItem,
    ModelConnection,
    ModelRoute,
    ModelTestRecord,
    ModelTestResult,
)
from agent_platform.ports.workspaces import SandboxProfile, WorkspaceProfile


class CatalogRepository(Protocol):
    """租户范围 Catalog 读写契约，持久化实现不能暴露 SQL 行对象。"""

    def save_employee(self, identity: IdentityContext, value: DigitalEmployee) -> None: ...

    def get_employee(self, identity: IdentityContext, employee_id: str) -> DigitalEmployee: ...

    def list_employees(self, identity: IdentityContext) -> tuple[DigitalEmployee, ...]: ...

    def delete_employee(self, identity: IdentityContext, employee_id: str) -> None: ...

    def save_employee_version(self, identity: IdentityContext, value: DigitalEmployeeVersion) -> None: ...

    def deprecate_employee_version(
        self, identity: IdentityContext, employee_id: str, version: int
    ) -> DigitalEmployeeVersion:
        """在租户事务内下线精确员工版本并写入审计；已下线时返回当前版本。"""
        ...

    def get_employee_version(
        self, identity: IdentityContext, employee_id: str, version: int
    ) -> DigitalEmployeeVersion: ...

    def list_employee_versions(
        self, identity: IdentityContext, employee_id: str
    ) -> tuple[DigitalEmployeeVersion, ...]: ...

    def fork_employee_version(
        self, identity: IdentityContext, employee_id: str, source_version: int
    ) -> DigitalEmployeeVersion: ...

    def replace_employee_resource_bindings(
        self,
        identity: IdentityContext,
        employee_id: str,
        version: int,
        slot: str,
        refs: tuple[tuple[str, int], ...],
    ) -> None: ...

    def fork_skill_version(
        self, identity: IdentityContext, skill_id: str, source_version: int
    ) -> SkillVersion: ...

    def save_skill(self, identity: IdentityContext, value: SkillVersion) -> None: ...

    def save_skill_if_revision_matches(
        self,
        identity: IdentityContext,
        value: SkillVersion,
        expected_revision: SkillVersion,
    ) -> None: ...

    def get_skill_version(
        self, identity: IdentityContext, skill_id: str, version: int
    ) -> SkillVersion: ...

    def get_skill(self, identity: IdentityContext, skill_id: str) -> SkillVersion: ...

    def list_skill_versions(self, identity: IdentityContext, skill_id: str) -> tuple[SkillVersion, ...]: ...

    def list_skills(self, identity: IdentityContext) -> tuple[SkillVersion, ...]: ...

    def delete_skill(self, identity: IdentityContext, skill_id: str) -> None: ...

    def save_tool(self, identity: IdentityContext, value: ToolVersion) -> None: ...

    def get_tool_version(self, identity: IdentityContext, tool_id: str, version: int) -> ToolVersion: ...

    def get_tool(self, identity: IdentityContext, tool_id: str) -> ToolVersion: ...

    def list_tools(self, identity: IdentityContext) -> tuple[ToolVersion, ...]: ...

    def delete_tool(self, identity: IdentityContext, tool_id: str) -> None: ...

    def save_profile(self, identity: IdentityContext, value: ExecutionProfile) -> None: ...

    def get_profile_version(
        self, identity: IdentityContext, profile_id: str, version: int
    ) -> ExecutionProfile: ...

    def get_profile(self, identity: IdentityContext, profile_id: str) -> ExecutionProfile: ...

    def list_profiles(self, identity: IdentityContext) -> tuple[ExecutionProfile, ...]: ...

    def save_model_route(self, identity: IdentityContext, value: ModelRoute) -> None: ...

    def get_model_route(self, identity: IdentityContext, route_id: str) -> ModelRoute: ...

    def save_model_connection(self, identity: IdentityContext, value: ModelConnection) -> None: ...

    def get_model_connection(self, identity: IdentityContext, connection_id: str) -> ModelConnection: ...

    def list_model_connections(self, identity: IdentityContext) -> tuple[ModelConnection, ...]: ...

    def save_model_catalog_item(self, identity: IdentityContext, value: ModelCatalogItem) -> None: ...

    def get_model_catalog_item(
        self, identity: IdentityContext, connection_id: str, model_id: str
    ) -> ModelCatalogItem: ...

    def list_model_catalog_items(
        self, identity: IdentityContext, connection_id: str
    ) -> tuple[ModelCatalogItem, ...]: ...

    def save_model_test_record(self, identity: IdentityContext, value: ModelTestRecord) -> None: ...

    def get_model_test_record(self, identity: IdentityContext, test_id: str) -> ModelTestRecord: ...

    def claim_next_model_test(
        self, platform_id: str, worker_id: str, *, lease_seconds: int
    ) -> ModelTestRecord | None: ...

    def heartbeat_model_test(
        self,
        identity: IdentityContext,
        test_id: str,
        worker_id: str,
        fencing_token: int,
        *,
        lease_seconds: int,
    ) -> bool: ...

    def finish_model_test(
        self,
        identity: IdentityContext,
        claimed: ModelTestRecord,
        result: ModelTestResult,
    ) -> bool: ...

    def save_workspace_profile(
        self, identity: IdentityContext, value: WorkspaceProfile, *, version: int = 1
    ) -> None: ...

    def get_workspace_profile(
        self, identity: IdentityContext, profile_id: str, *, version: int = 1
    ) -> WorkspaceProfile: ...

    def save_sandbox_profile(
        self, identity: IdentityContext, value: SandboxProfile, *, version: int = 1
    ) -> None: ...

    def get_sandbox_profile(
        self, identity: IdentityContext, profile_id: str, *, version: int = 1
    ) -> SandboxProfile: ...

    def list_capability_bindings(
        self, identity: IdentityContext, employee_id: str, version: int
    ) -> tuple[CapabilityBinding, ...]: ...

    def transition_deployment(
        self, identity: IdentityContext, deployment_id: str, target_status: LifecycleStatus
    ) -> Deployment:
        """在租户事务内校验并切换 ACTIVE 或 DISABLED 状态，同时写入审计。"""
        ...


class DeploymentRepository(Protocol):
    """租户范围 Deployment 查询、创建、版本切换和启停契约。"""

    def save_deployment(self, identity: IdentityContext, value: Deployment) -> Deployment:
        """保存已发布版本绑定，返回写入时的实际部署状态。"""
        ...

    def transition_deployment(
        self, identity: IdentityContext, deployment_id: str, target_status: LifecycleStatus
    ) -> Deployment:
        """在当前租户内原子校验并更新部署状态，同时记录审计。"""
        ...

    def get_deployment(self, identity: IdentityContext, deployment_id: str) -> Deployment: ...

    def list_deployments(self, identity: IdentityContext) -> tuple[Deployment, ...]: ...

    def list_audit_events(
        self,
        identity: IdentityContext,
        *,
        resource_type: str | None = None,
        resource_id: str | None = None,
        event_type: str | None = None,
        limit: int = 100,
        before_created_at: datetime | None = None,
        before_event_id: str | None = None,
    ) -> tuple[AuditEventRecord, ...]: ...
