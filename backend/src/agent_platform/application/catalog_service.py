"""数字员工目录的内存仓储和发布、部署应用服务。"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import RLock
from uuid import uuid4

from agent_platform.domain.catalog import (
    AuditEventPage,
    AuditEventRecord,
    Deployment,
    DeploymentPage,
    DigitalEmployee,
    DigitalEmployeePage,
    DigitalEmployeeVersion,
    DigitalEmployeeVersionPage,
    ExecutionProfile,
    LifecycleStatus,
    SkillType,
    SkillVersion,
    ToolVersion,
)
from agent_platform.domain.common import DomainError, generate_database_id
from agent_platform.domain.identity import IdentityContext
from agent_platform.domain.model_routes import (
    ModelCatalogItem,
    ModelCatalogMode,
    ModelCatalogSource,
    ModelConnection,
    ModelRoute,
    ModelTestRecord,
    ModelTestResult,
    ModelTestState,
    legacy_model_connection_id,
)
from agent_platform.domain.resource_center import (
    HubImportMode,
    HubResourceType,
    HubResourceVersion,
    TenantImportStatus,
    TenantResourceImport,
)
from agent_platform.ports.catalog_repository import CatalogRepository, DeploymentRepository
from agent_platform.ports.resource_center import ResourceCenterRepository
from agent_platform.ports.workspaces import SandboxProfile, WorkspaceProfile


def _key(identity: IdentityContext, resource_id: str) -> tuple[str, str, str]:
    return identity.platform_id, identity.tenant_id, resource_id


class InMemoryCatalogRepository:
    """进程内目录实现；所有索引包含平台和租户，避免裸 ID 越权。"""

    def __init__(self) -> None:
        self.tools: dict[tuple[str, str, str], ToolVersion] = {}
        self.tool_versions: dict[tuple[str, str, str, int], ToolVersion] = {}
        self.skills: dict[tuple[str, str, str], SkillVersion] = {}
        self.skill_versions: dict[tuple[str, str, str, int], SkillVersion] = {}
        self.employees: dict[tuple[str, str, str], DigitalEmployee] = {}
        self.employee_versions: dict[tuple[str, str, str, int], DigitalEmployeeVersion] = {}
        self.profiles: dict[tuple[str, str, str], ExecutionProfile] = {}
        self.profile_versions: dict[tuple[str, str, str, int], ExecutionProfile] = {}
        self.model_routes: dict[tuple[str, str, str], object] = {}
        self.model_connections: dict[tuple[str, str, str], ModelConnection] = {}
        self.model_catalog_items: dict[tuple[str, str, str, str], ModelCatalogItem] = {}
        self.model_test_records: dict[tuple[str, str, str], ModelTestRecord] = {}
        self.workspace_profiles: dict[tuple[str, str, str], object] = {}
        self.sandbox_profiles: dict[tuple[str, str, str], object] = {}
        self.deployments: dict[tuple[str, str, str], Deployment] = {}
        self.audit_events: list[AuditEventRecord] = []
        # 员工、版本与部署共用写锁，保证版本下线与部署写入的先后顺序。
        self._employee_version_lock = RLock()
        self._model_test_lock = RLock()

    def _get(self, values: Mapping, key: tuple, resource: str):
        try:
            return values[key]
        except KeyError as error:
            raise DomainError("RESOURCE_NOT_FOUND", "资源不存在", {"resource": resource}) from error

    def get_tool(self, identity: IdentityContext, tool_id: str) -> ToolVersion:
        return self._get(self.tools, _key(identity, tool_id), "tool")

    def get_tool_version(self, identity: IdentityContext, tool_id: str, version: int) -> ToolVersion:
        return self._get(self.tool_versions, (*_key(identity, tool_id), version), "tool")

    def get_skill(self, identity: IdentityContext, skill_id: str) -> SkillVersion:
        return self._get(self.skills, _key(identity, skill_id), "skill")

    def get_skill_version(self, identity: IdentityContext, skill_id: str, version: int) -> SkillVersion:
        return self._get(self.skill_versions, (*_key(identity, skill_id), version), "skill")

    def list_skill_versions(self, identity: IdentityContext, skill_id: str) -> tuple[SkillVersion, ...]:
        prefix = _key(identity, skill_id)
        return tuple(
            value
            for key, value in sorted(self.skill_versions.items(), key=lambda item: item[0][3])
            if key[:3] == prefix
        )

    def get_employee(self, identity: IdentityContext, employee_id: str) -> DigitalEmployee:
        return self._get(self.employees, _key(identity, employee_id), "digital_employee")

    def get_version(
        self, identity: IdentityContext, employee_id: str, version: int
    ) -> DigitalEmployeeVersion:
        return self._get(
            self.employee_versions,
            (*_key(identity, employee_id), version),
            "digital_employee_version",
        )

    def get_employee_version(
        self, identity: IdentityContext, employee_id: str, version: int
    ) -> DigitalEmployeeVersion:
        return self.get_version(identity, employee_id, version)

    def list_employee_versions(
        self, identity: IdentityContext, employee_id: str
    ) -> tuple[DigitalEmployeeVersion, ...]:
        prefix = (*_key(identity, employee_id),)
        return tuple(
            value for key, value in self.employee_versions.items()
            if key[:3] == prefix
        )

    def get_profile(self, identity: IdentityContext, profile_id: str) -> ExecutionProfile:
        return self._get(self.profiles, _key(identity, profile_id), "execution_profile")

    def get_profile_version(
        self, identity: IdentityContext, profile_id: str, version: int
    ) -> ExecutionProfile:
        return self._get(self.profile_versions, (*_key(identity, profile_id), version), "execution_profile")

    def get_deployment(self, identity: IdentityContext, deployment_id: str) -> Deployment:
        """读取当前部署状态与它固定的员工版本，保留下线版本的历史绑定。"""

        with self._employee_version_lock:
            deployment = self._get(self.deployments, _key(identity, deployment_id), "deployment")
            version = self.get_employee_version(
                identity, deployment.employee_id, deployment.employee_version.version
            )
            return replace(deployment, employee_version=version)

    def list_deployments(self, identity: IdentityContext) -> tuple[Deployment, ...]:
        prefix = (identity.platform_id, identity.tenant_id)
        with self._employee_version_lock:
            return tuple(
                self.get_deployment(identity, deployment_id)
                for platform_id, tenant_id, deployment_id in self.deployments
                if (platform_id, tenant_id) == prefix
            )

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
    ) -> tuple[AuditEventRecord, ...]:
        with self._employee_version_lock:
            events = tuple(self.audit_events)
        rows = [
            event
            for event in events
            if event.platform_id == identity.platform_id
            and event.tenant_id == identity.tenant_id
            and (resource_type is None or event.resource_type == resource_type)
            and (resource_id is None or event.resource_id == resource_id)
            and (event_type is None or event.event_type == event_type)
            and (
                before_created_at is None
                or event.created_at < before_created_at
                or (event.created_at == before_created_at and event.event_id < (before_event_id or ""))
            )
        ]
        return tuple(
            sorted(rows, key=lambda event: (event.created_at, event.event_id), reverse=True)[:limit]
        )

    def save_tool(self, identity: IdentityContext, value: ToolVersion) -> None:
        self.tools[_key(identity, value.tool_id)] = value
        self.tool_versions[(*_key(identity, value.tool_id), value.version)] = value

    def save_skill(self, identity: IdentityContext, value: SkillVersion) -> None:
        self.skills[_key(identity, value.skill_id)] = value
        self.skill_versions[(*_key(identity, value.skill_id), value.version)] = value

    def save_skill_if_revision_matches(
        self,
        identity: IdentityContext,
        value: SkillVersion,
        expected_revision: SkillVersion,
    ) -> None:
        """内存实现也校验 DRAFT 修订，模拟 MySQL 的行锁乐观并发边界。"""

        current = self.get_skill_version(identity, value.skill_id, value.version)
        if current != expected_revision:
            raise DomainError("IDEMPOTENCY_CONFLICT", "Skill 在 ZIP 预览后已被修改")
        current.assert_mutable()
        self.save_skill(identity, value)

    def fork_skill_version(
        self, identity: IdentityContext, skill_id: str, source_version: int
    ) -> SkillVersion:
        """复制已发布 Skill 元数据和不可变文件引用，生成新的 DRAFT 版本。"""

        current = self.get_skill_version(identity, skill_id, source_version)
        if current.status is not LifecycleStatus.PUBLISHED:
            raise DomainError("INVALID_STATE", "只能基于已发布 Skill 版本创建草稿")
        latest = max(
            (value.version for value in self.list_skill_versions(identity, skill_id)),
            default=0,
        )
        forked = replace(current, version=latest + 1, status=LifecycleStatus.DRAFT)
        self.save_skill(identity, forked)
        return forked

    def save_employee(self, identity: IdentityContext, value: DigitalEmployee) -> None:
        with self._employee_version_lock:
            self.employees[_key(identity, value.employee_id)] = value

    def save_version(self, identity: IdentityContext, value: DigitalEmployeeVersion) -> None:
        self.save_employee_version(identity, value)

    def save_employee_version(self, identity: IdentityContext, value: DigitalEmployeeVersion) -> None:
        """保存新版本或更新仍为草稿的版本，避免过期请求覆盖已发布快照。"""

        key = (*_key(identity, value.employee_id), value.version)
        with self._employee_version_lock:
            current = self.employee_versions.get(key)
            # 以锁内的持久状态为准，避免迟到的发布或草稿保存撤销版本下线。
            if current is not None and current.status is not LifecycleStatus.DRAFT:
                # 与 MySQL 一致，相同快照视为保存成功，无需重写。
                if current == value:
                    return
                current.assert_mutable()
            if value.status not in (LifecycleStatus.DRAFT, LifecycleStatus.PUBLISHED):
                raise DomainError("INVALID_STATE", "只允许保存草稿或已发布员工版本")
            self.employee_versions[key] = value

    def fork_employee_version(
        self, identity: IdentityContext, employee_id: str, source_version: int
    ) -> DigitalEmployeeVersion:
        """在锁内分配版本号并复制历史员工配置。"""

        with self._employee_version_lock:
            employee = self.get_employee(identity, employee_id)
            if employee.status is LifecycleStatus.ARCHIVED:
                raise DomainError("INVALID_STATE", "已归档员工不能创建新版本")
            source = self.get_employee_version(identity, employee_id, source_version)
            if source.status not in (LifecycleStatus.PUBLISHED, LifecycleStatus.DEPRECATED):
                raise DomainError("INVALID_STATE", "只能基于已发布或已下线员工版本创建草稿")
            next_version = max(
                (value.version for value in self.list_employee_versions(identity, employee_id)),
                default=0,
            ) + 1
            forked = replace(source, version=next_version, status=LifecycleStatus.DRAFT)
            self.save_employee_version(identity, forked)
            return forked

    def deprecate_employee_version(
        self, identity: IdentityContext, employee_id: str, version: int
    ) -> DigitalEmployeeVersion:
        """在同一临界区废弃指定版本并记录一次状态变化。"""

        with self._employee_version_lock:
            employee = self.get_employee(identity, employee_id)
            if employee.status is LifecycleStatus.ARCHIVED:
                raise DomainError("INVALID_STATE", "已归档员工不能下线版本")
            current = self.get_employee_version(identity, employee_id, version)
            if current.status is LifecycleStatus.DEPRECATED:
                return current
            deprecated = current.deprecate()
            event = AuditEventRecord(
                event_id=generate_database_id(),
                platform_id=identity.platform_id,
                tenant_id=identity.tenant_id,
                actor_type="USER",
                actor_user_id=identity.user_id,
                event_type="EMPLOYEE_VERSION_DEPRECATED",
                resource_type="digital_employee_version",
                resource_id=employee_id,
                payload={"employee_id": employee_id, "version": version},
                created_at=datetime.now(UTC),
            )
            self.employee_versions[(*_key(identity, employee_id), version)] = deprecated
            self.audit_events.append(event)
            return deprecated

    def replace_employee_resource_bindings(
        self,
        identity: IdentityContext,
        employee_id: str,
        version: int,
        slot: str,
        refs: tuple[tuple[str, int], ...],
    ) -> None:
        """只替换同类型绑定；该实现不触碰版本其余字段。"""

        current = self.get_employee_version(identity, employee_id, version)
        current.assert_mutable()
        if slot == "skill":
            updated = replace(
                current,
                skill_version_ids=tuple(resource_id for resource_id, _ in refs),
                skill_version_refs=refs,
            )
        elif slot == "tool":
            updated = replace(
                current,
                tool_version_ids=tuple(resource_id for resource_id, _ in refs),
                tool_version_refs=refs,
            )
        else:
            raise ValueError("slot must be skill or tool")
        self.save_employee_version(identity, updated)

    def save_profile(self, identity: IdentityContext, value: ExecutionProfile) -> None:
        self.profiles[_key(identity, value.profile_id)] = value
        self.profile_versions[(*_key(identity, value.profile_id), value.version)] = value

    def save_model_route(self, identity: IdentityContext, value: ModelRoute) -> None:
        if value.tenant_id != identity.tenant_id or value.profile.tenant_id != identity.tenant_id:
            raise DomainError("AUTHORIZATION_DENIED", "模型路由不属于当前租户")
        route = value
        profile = route.profile
        if profile.connection_id is None:
            connection = next(
                (
                    item
                    for item in self.list_model_connections(identity)
                    if item.provider_kind == profile.provider
                    and item.protocol == profile.protocol
                    and item.endpoint == profile.endpoint
                    and item.secret_ref == profile.secret_ref
                ),
                None,
            )
            if connection is None:
                connection_id = legacy_model_connection_id(
                    identity.platform_id, identity.tenant_id, profile
                )
                connection = ModelConnection(
                    connection_id=connection_id,
                    tenant_id=identity.tenant_id,
                    name=f"{profile.provider}-{connection_id[-8:]}",
                    provider_kind=profile.provider,
                    protocol=profile.protocol,
                    endpoint=profile.endpoint,
                    secret_ref=profile.secret_ref,
                    catalog_mode=ModelCatalogMode.MANUAL,
                )
                self.save_model_connection(identity, connection)
            try:
                self.get_model_catalog_item(identity, connection.connection_id, profile.model_name)
            except DomainError as error:
                if error.code != "RESOURCE_NOT_FOUND":
                    raise
                self.save_model_catalog_item(
                    identity,
                    ModelCatalogItem(
                        connection_id=connection.connection_id,
                        tenant_id=identity.tenant_id,
                        model_id=profile.model_name,
                        display_name=profile.model_name,
                        catalog_source=ModelCatalogSource.MANUAL,
                    ),
                )
            route = replace(
                route,
                profile=replace(
                    profile,
                    connection_id=connection.connection_id,
                    catalog_model_id=profile.model_name,
                ),
            )
        else:
            connection = self.get_model_connection(identity, profile.connection_id)
            if (
                connection.provider_kind != profile.provider
                or connection.protocol != profile.protocol
                or connection.endpoint != profile.endpoint
                or connection.secret_ref != profile.secret_ref
            ):
                raise DomainError("INVALID_ARGUMENT", "ModelProfile 与 ModelConnection 配置不一致")
            self.get_model_catalog_item(identity, profile.connection_id, profile.catalog_model_id or "")
        self.model_routes[_key(identity, route.route_id)] = route

    def save(self, identity: IdentityContext, value: ModelRoute) -> None:
        self.save_model_route(identity, value)

    def get_model_route(self, identity: IdentityContext, route_id: str) -> ModelRoute:
        return self._get(self.model_routes, _key(identity, route_id), "model_route")

    def save_model_connection(self, identity: IdentityContext, value: ModelConnection) -> None:
        if value.tenant_id != identity.tenant_id:
            raise DomainError("AUTHORIZATION_DENIED", "模型连接不属于当前租户")
        self.model_connections[_key(identity, value.connection_id)] = value

    def get_model_connection(
        self, identity: IdentityContext, connection_id: str
    ) -> ModelConnection:
        return self._get(self.model_connections, _key(identity, connection_id), "model_connection")

    def list_model_connections(self, identity: IdentityContext) -> tuple[ModelConnection, ...]:
        prefix = (identity.platform_id, identity.tenant_id)
        return tuple(
            value
            for (platform_id, tenant_id, _), value in self.model_connections.items()
            if (platform_id, tenant_id) == prefix
        )

    def save_model_catalog_item(self, identity: IdentityContext, value: ModelCatalogItem) -> None:
        if value.tenant_id != identity.tenant_id:
            raise DomainError("AUTHORIZATION_DENIED", "模型目录项不属于当前租户")
        self.get_model_connection(identity, value.connection_id)
        self.model_catalog_items[
            (*_key(identity, value.connection_id), value.model_id)
        ] = value

    def get_model_catalog_item(
        self, identity: IdentityContext, connection_id: str, model_id: str
    ) -> ModelCatalogItem:
        return self._get(
            self.model_catalog_items,
            (*_key(identity, connection_id), model_id),
            "model_catalog_item",
        )

    def list_model_catalog_items(
        self, identity: IdentityContext, connection_id: str
    ) -> tuple[ModelCatalogItem, ...]:
        self.get_model_connection(identity, connection_id)
        prefix = (*_key(identity, connection_id),)
        return tuple(
            value
            for key, value in sorted(self.model_catalog_items.items())
            if key[:3] == prefix
        )

    def save_model_test_record(self, identity: IdentityContext, value: ModelTestRecord) -> None:
        if value.tenant_id != identity.tenant_id:
            raise DomainError("AUTHORIZATION_DENIED", "模型测试记录不属于当前租户")
        self.get_model_connection(identity, value.connection_id)
        key = _key(identity, value.test_id)
        with self._model_test_lock:
            if key in self.model_test_records:
                raise DomainError("CONFLICT", "模型测试任务标识已存在")
            self.model_test_records[key] = value

    def get_model_test_record(self, identity: IdentityContext, test_id: str) -> ModelTestRecord:
        return self._get(self.model_test_records, _key(identity, test_id), "model_test")

    def claim_next_model_test(
        self, platform_id: str, worker_id: str, *, lease_seconds: int
    ) -> ModelTestRecord | None:
        now = datetime.now(UTC)
        with self._model_test_lock:
            # 租约过期视为结果不确定，提升 fencing token 后终结，绝不自动重放模型请求。
            for key, record in tuple(self.model_test_records.items()):
                if (
                    key[0] == platform_id
                    and record.state is ModelTestState.RUNNING
                    and record.lease_expires_at is not None
                    and record.lease_expires_at <= now
                ):
                    expired = record.finish(
                        ModelTestResult(
                            ModelTestState.UNKNOWN,
                            error_category="WORKER_LEASE_EXPIRED",
                        ),
                        finished_at=now,
                    )
                    self.model_test_records[key] = replace(
                        expired, fencing_token=expired.fencing_token + 1
                    )
            queued = sorted(
                (
                    (key, record)
                    for key, record in self.model_test_records.items()
                    if key[0] == platform_id and record.state is ModelTestState.QUEUED
                ),
                key=lambda item: (item[1].created_at, item[1].test_id),
            )
            if not queued:
                return None
            key, record = queued[0]
            claimed = record.claim(worker_id, now=now, lease_seconds=lease_seconds)
            self.model_test_records[key] = claimed
            return claimed

    def heartbeat_model_test(
        self,
        identity: IdentityContext,
        test_id: str,
        worker_id: str,
        fencing_token: int,
        *,
        lease_seconds: int,
    ) -> bool:
        now = datetime.now(UTC)
        key = _key(identity, test_id)
        with self._model_test_lock:
            current = self.model_test_records.get(key)
            if (
                current is None
                or current.state is not ModelTestState.RUNNING
                or current.worker_id != worker_id
                or current.fencing_token != fencing_token
                or current.lease_expires_at is None
                or current.lease_expires_at <= now
            ):
                return False
            self.model_test_records[key] = replace(
                current,
                lease_expires_at=now + timedelta(seconds=lease_seconds),
                updated_at=now,
            )
            return True

    def finish_model_test(
        self,
        identity: IdentityContext,
        claimed: ModelTestRecord,
        result: ModelTestResult,
    ) -> bool:
        key = _key(identity, claimed.test_id)
        now = datetime.now(UTC)
        with self._model_test_lock:
            current = self.model_test_records.get(key)
            if (
                current is None
                or current.state is not ModelTestState.RUNNING
                or claimed.state is not ModelTestState.RUNNING
                or current.worker_id != claimed.worker_id
                or current.worker_id is None
                or current.fencing_token != claimed.fencing_token
                or current.lease_expires_at is None
                or current.lease_expires_at <= now
            ):
                return False
            self.model_test_records[key] = current.finish(result, finished_at=now)
            return True

    def get(self, identity: IdentityContext, route_id: str) -> ModelRoute | None:
        try:
            return self.get_model_route(identity, route_id)
        except DomainError as error:
            if error.code == "RESOURCE_NOT_FOUND":
                return None
            raise

    def list(self, identity: IdentityContext) -> tuple[ModelRoute, ...]:
        prefix = (identity.platform_id, identity.tenant_id)
        return tuple(
            route for (platform_id, tenant_id, _), route in self.model_routes.items()
            if (platform_id, tenant_id) == prefix
        )

    def save_workspace_profile(self, identity: IdentityContext, value: WorkspaceProfile, *, version: int = 1) -> None:
        self.workspace_profiles[(*_key(identity, value.profile_id), version)] = value

    def get_workspace_profile(self, identity: IdentityContext, profile_id: str, *, version: int = 1) -> WorkspaceProfile:
        return self._get(self.workspace_profiles, (*_key(identity, profile_id), version), "workspace_profile")

    def save_sandbox_profile(self, identity: IdentityContext, value: SandboxProfile, *, version: int = 1) -> None:
        self.sandbox_profiles[(*_key(identity, value.profile_id), version)] = value

    def get_sandbox_profile(self, identity: IdentityContext, profile_id: str, *, version: int = 1) -> SandboxProfile:
        return self._get(self.sandbox_profiles, (*_key(identity, profile_id), version), "sandbox_profile")

    def save_deployment(self, identity: IdentityContext, value: Deployment) -> Deployment:
        """在共用锁内校验发布状态，回滚只替换当前部署的版本绑定。"""

        key = _key(identity, value.deployment_id)
        with self._employee_version_lock:
            previous = self.deployments.get(key)
            employee = self.get_employee(identity, value.employee_id)
            if employee.status is LifecycleStatus.ARCHIVED:
                message = "已归档员工不能回滚部署" if previous else "已归档员工不能创建新的部署"
                raise DomainError("INVALID_STATE", message)
            target = self.get_employee_version(identity, value.employee_id, value.employee_version.version)
            if target.status is not LifecycleStatus.PUBLISHED:
                if previous is not None:
                    raise DomainError("DEPENDENCY_VALIDATION_ERROR", "回滚目标必须是已发布版本")
                raise DomainError("INVALID_STATE", "只能部署已发布员工版本")
            saved = previous.rollback(target) if previous else replace(value, employee_version=target)
            previous_version = previous.employee_version.version if previous else None
            if previous_version != target.version:
                self._record_deployment_event(
                    identity,
                    saved,
                    "DEPLOYMENT_CREATED" if previous is None else "DEPLOYMENT_ROLLED_BACK",
                    {"previous_version": previous_version},
                )
            self.deployments[key] = saved
            return saved

    def transition_deployment(
        self, identity: IdentityContext, deployment_id: str, target_status: LifecycleStatus
    ) -> Deployment:
        """使用最新绑定执行启停，并在同一临界区写入审计。"""

        with self._employee_version_lock:
            current = self.get_deployment(identity, deployment_id)
            if target_status is LifecycleStatus.DISABLED:
                updated = current.disable()
                event_type = "DEPLOYMENT_DISABLED"
            elif target_status is LifecycleStatus.ACTIVE:
                employee = self.get_employee(identity, current.employee_id)
                if employee.status is LifecycleStatus.ARCHIVED:
                    raise DomainError("INVALID_STATE", "已归档员工不能重新上线部署")
                updated = current.enable()
                event_type = "DEPLOYMENT_ENABLED"
            else:
                raise DomainError("INVALID_STATE", "部署只能下线或重新上线")
            self._record_deployment_event(
                identity,
                updated,
                event_type,
                {"previous_status": current.status.value, "status": updated.status.value},
            )
            self.deployments[_key(identity, deployment_id)] = updated
            return updated

    def _record_deployment_event(
        self, identity: IdentityContext, value: Deployment, event_type: str, changes: dict[str, object]
    ) -> None:
        self.audit_events.append(
            AuditEventRecord(
                event_id=generate_database_id(),
                platform_id=identity.platform_id,
                tenant_id=identity.tenant_id,
                actor_type="USER",
                actor_user_id=identity.user_id,
                event_type=event_type,
                resource_type="deployment",
                resource_id=value.deployment_id,
                payload={"employee_id": value.employee_id, "version": value.employee_version.version, **changes},
                created_at=datetime.now(UTC),
            )
        )

    def delete_tool(self, identity: IdentityContext, tool_id: str) -> None:
        current = self.get_tool(identity, tool_id)
        self.tools.pop(_key(identity, tool_id), None)
        self.tool_versions.pop((*_key(identity, tool_id), current.version), None)

    def delete_skill(self, identity: IdentityContext, skill_id: str) -> None:
        current = self.get_skill(identity, skill_id)
        self.skills.pop(_key(identity, skill_id), None)
        self.skill_versions.pop((*_key(identity, skill_id), current.version), None)

    def delete_employee(self, identity: IdentityContext, employee_id: str) -> None:
        self.get_employee(identity, employee_id)
        self.employees.pop(_key(identity, employee_id), None)

    def list_tools(self, identity: IdentityContext) -> tuple[ToolVersion, ...]:
        prefix = (identity.platform_id, identity.tenant_id)
        return tuple(value for (platform, tenant, _), value in self.tools.items() if (platform, tenant) == prefix)

    def list_skills(self, identity: IdentityContext) -> tuple[SkillVersion, ...]:
        prefix = (identity.platform_id, identity.tenant_id)
        return tuple(value for (platform, tenant, _), value in self.skills.items() if (platform, tenant) == prefix)

    def list_employees(self, identity: IdentityContext) -> tuple[DigitalEmployee, ...]:
        prefix = (identity.platform_id, identity.tenant_id)
        return tuple(value for (platform, tenant, _), value in self.employees.items() if (platform, tenant) == prefix)

    def list_profiles(self, identity: IdentityContext) -> tuple[ExecutionProfile, ...]:
        prefix = (identity.platform_id, identity.tenant_id)
        return tuple(value for (platform, tenant, _), value in self.profiles.items() if (platform, tenant) == prefix)


class CatalogService:
    """执行目录写入、依赖交集和已发布版本部署。"""

    def __init__(
        self,
        repository: CatalogRepository & DeploymentRepository,
        id_factory: Callable[[str], str] | None = None,
        *,
        resource_center_repository: ResourceCenterRepository | None = None,
    ) -> None:
        self.repository = repository
        self._id_factory = id_factory or (lambda prefix: f"{prefix}-{uuid4().hex[:10]}")
        self.resource_center_repository = resource_center_repository

    def _new_id(self, prefix: str) -> str:
        return self._id_factory(prefix)

    @staticmethod
    def _require_employee_admin(identity: IdentityContext) -> None:
        if "admin" not in identity.roles:
            raise DomainError("AUTHORIZATION_DENIED", "只有租户管理员可以修改租户目录资源")

    @staticmethod
    def _encode_page_cursor(value: dict[str, object]) -> str:
        serialized = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        return base64.urlsafe_b64encode(serialized.encode("utf-8")).decode("ascii").rstrip("=")

    @staticmethod
    def _decode_page_cursor(cursor: str, expected: dict[str, object]) -> dict[str, object]:
        try:
            padded = cursor + "=" * (-len(cursor) % 4)
            decoded = json.loads(
                base64.b64decode(padded.encode("ascii"), altchars=b"-_", validate=True)
            )
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError, binascii.Error) as error:
            raise DomainError("INVALID_ARGUMENT", "分页游标格式非法") from error
        if not isinstance(decoded, dict) or any(decoded.get(key) != value for key, value in expected.items()):
            raise DomainError("INVALID_ARGUMENT", "分页游标与当前筛选条件不匹配")
        return decoded

    def _ensure_profile(self, identity: IdentityContext) -> ExecutionProfile:
        try:
            return self.repository.get_profile(identity, "profile-fake")
        except DomainError as error:
            if error.code != "RESOURCE_NOT_FOUND":
                raise
        profile = ExecutionProfile(
            profile_id="profile-fake",
            tenant_id=identity.tenant_id,
            engine_name="fake",
            model_alias="fake-model",
        ).publish()
        self.repository.save_profile(identity, profile)
        return profile

    def create_tool(
        self,
        identity: IdentityContext,
        name: str,
        description: str,
        input_schema: Mapping[str, object],
        *,
        protocol: str = "builtin",
        requires_recipient_credentials: bool = False,
        auth_secret_ref: str | None = None,
    ) -> ToolVersion:
        self._require_employee_admin(identity)
        value = ToolVersion(
            self._new_id("tool"),
            identity.tenant_id,
            name,
            description,
            input_schema,
            protocol=protocol,
            requires_recipient_credentials=requires_recipient_credentials,
            auth_secret_ref=auth_secret_ref,
        )
        self.repository.save_tool(identity, value)
        return value

    def update_tool(
        self,
        identity: IdentityContext,
        tool_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        input_schema: Mapping[str, object] | None = None,
        auth_secret_ref: str | None = None,
    ) -> ToolVersion:
        self._require_employee_admin(identity)
        current = self.repository.get_tool(identity, tool_id)
        current.assert_mutable()
        updated = replace(
            current,
            name=name or current.name,
            description=description or current.description,
            input_schema=input_schema or current.input_schema,
            auth_secret_ref=auth_secret_ref or current.auth_secret_ref,
        )
        self.repository.save_tool(identity, updated)
        return updated

    def delete_tool(self, identity: IdentityContext, tool_id: str) -> None:
        self._require_employee_admin(identity)
        current = self.repository.get_tool(identity, tool_id)
        current.assert_mutable()
        self.repository.delete_tool(identity, tool_id)

    def publish_tool(self, identity: IdentityContext, tool_id: str) -> ToolVersion:
        self._require_employee_admin(identity)
        current = self.repository.get_tool(identity, tool_id)
        if current.requires_recipient_credentials and current.auth_secret_ref is None:
            raise DomainError(
                "DEPENDENCY_VALIDATION_ERROR",
                "Tool 需要先绑定当前租户的凭据 SecretRef",
                {"tool_id": tool_id, "kind": "recipient_credentials"},
            )
        published = current.publish()
        self.repository.save_tool(identity, published)
        return published

    def create_skill(
        self,
        identity: IdentityContext,
        name: str,
        skill_type: SkillType,
        instruction: str,
        allowed_tool_ids: Iterable[str],
    ) -> SkillVersion:
        self._require_employee_admin(identity)
        allowed_ids = tuple(allowed_tool_ids)
        value = SkillVersion(
            skill_id=self._new_id("skill"),
            tenant_id=identity.tenant_id,
            name=name,
            skill_type=skill_type,
            instruction=instruction,
            allowed_tool_ids=allowed_ids,
            allowed_tool_versions=tuple(
                (
                    tool_id,
                    self._latest_version(self.repository.get_tool, identity, tool_id, default=1),
                )
                for tool_id in allowed_ids
            ),
        )
        self.repository.save_skill(identity, value)
        return value

    def update_skill(self, identity: IdentityContext, skill_id: str, *, instruction: str | None = None, allowed_tool_ids: Iterable[str] | None = None) -> SkillVersion:
        self._require_employee_admin(identity)
        current = self.repository.get_skill(identity, skill_id)
        current.assert_mutable()
        updated = replace(current, instruction=instruction or current.instruction, allowed_tool_ids=tuple(allowed_tool_ids) if allowed_tool_ids is not None else current.allowed_tool_ids)
        self.repository.save_skill(identity, updated)
        return updated

    def delete_skill(self, identity: IdentityContext, skill_id: str) -> None:
        self._require_employee_admin(identity)
        current = self.repository.get_skill(identity, skill_id)
        current.assert_mutable()
        self.repository.delete_skill(identity, skill_id)

    def publish_skill(self, identity: IdentityContext, skill_id: str) -> SkillVersion:
        current = self.repository.get_skill(identity, skill_id)
        return self.publish_skill_version(identity, skill_id, current.version)

    def publish_skill_version(
        self, identity: IdentityContext, skill_id: str, version: int
    ) -> SkillVersion:
        """校验并发布指定 Skill 版本，避免“发布”意外作用于最新版本。"""

        self._require_employee_admin(identity)
        current = self.repository.get_skill_version(identity, skill_id, version)
        if current.instruction.startswith("hub-reference://"):
            raise DomainError("DEPENDENCY_VALIDATION_ERROR", "Hub REFERENCE Skill 不支持租户侧发布")
        for tool_id, tool_version in current.allowed_tool_versions:
            tool = self.repository.get_tool_version(identity, tool_id, tool_version)
            if tool.status is not LifecycleStatus.PUBLISHED:
                raise DomainError(
                    "DEPENDENCY_VALIDATION_ERROR",
                    "Skill 引用的 Tool 尚未发布",
                    {"skill_id": skill_id, "tool_id": tool_id},
                )
        published = current.publish()
        self.repository.save_skill(identity, published)
        return published

    def fork_skill_version(
        self, identity: IdentityContext, skill_id: str, source_version: int
    ) -> SkillVersion:
        """为本地已发布 Skill 创建新草稿；REFERENCE 需先 COPY 到租户。"""

        self._require_employee_admin(identity)
        source = self.repository.get_skill_version(identity, skill_id, source_version)
        if source.status is not LifecycleStatus.PUBLISHED:
            raise DomainError("INVALID_STATE", "只能基于已发布 Skill 版本创建草稿")
        if source.instruction.startswith("hub-reference://"):
            raise DomainError("DEPENDENCY_VALIDATION_ERROR", "请先将 Hub REFERENCE Skill COPY 到本租户")
        if self.resource_center_repository is not None:
            imported = self.resource_center_repository.get_import_for_local_resource(
                identity, HubResourceType.SKILL, skill_id, source_version
            )
            if imported is not None and imported.mode is HubImportMode.REFERENCE:
                raise DomainError("DEPENDENCY_VALIDATION_ERROR", "请先将 Hub REFERENCE Skill COPY 到本租户")
        return self.repository.fork_skill_version(identity, skill_id, source_version)

    def replace_employee_skill_bindings(
        self,
        identity: IdentityContext,
        employee_id: str,
        version: int,
        skill_version_refs: Iterable[tuple[str, int]],
    ) -> DigitalEmployeeVersion:
        """只替换 DRAFT 的 Skill 清单，并校验租户、发布状态和 REFERENCE 授权。"""

        return self._replace_employee_resource_bindings(
            identity, employee_id, version, "skill", skill_version_refs
        )

    def replace_employee_tool_bindings(
        self,
        identity: IdentityContext,
        employee_id: str,
        version: int,
        tool_version_refs: Iterable[tuple[str, int]],
    ) -> DigitalEmployeeVersion:
        """只替换 DRAFT 的 Tool 清单，并校验租户、发布状态和 REFERENCE 授权。"""

        return self._replace_employee_resource_bindings(
            identity, employee_id, version, "tool", tool_version_refs
        )

    def _replace_employee_resource_bindings(
        self,
        identity: IdentityContext,
        employee_id: str,
        version: int,
        slot: str,
        refs: Iterable[tuple[str, int]],
    ) -> DigitalEmployeeVersion:
        employee = self.repository.get_employee(identity, employee_id)
        self._require_employee_admin(identity)
        if employee.status is LifecycleStatus.ARCHIVED:
            raise DomainError("INVALID_STATE", "已归档员工不能修改资源绑定")
        current = self.repository.get_employee_version(identity, employee_id, version)
        current.assert_mutable()
        normalized_refs = tuple((resource_id, resource_version) for resource_id, resource_version in refs)
        if len({resource_id for resource_id, _ in normalized_refs}) != len(normalized_refs):
            raise DomainError("INVALID_ARGUMENT", f"{slot} 绑定不能包含重复资源")

        missing: list[dict[str, str]] = []
        for resource_id, resource_version in normalized_refs:
            if slot == "skill":
                resource = self.repository.get_skill_version(identity, resource_id, resource_version)
                if resource.status is not LifecycleStatus.PUBLISHED:
                    raise DomainError(
                        "DEPENDENCY_VALIDATION_ERROR", "只能绑定已发布 Skill 版本", {"skill_id": resource_id}
                    )
                self._validate_hub_reference(
                    identity,
                    HubResourceType.SKILL,
                    resource_id,
                    resource_version,
                    missing,
                    is_reference_marker=resource.instruction.startswith("hub-reference://"),
                )
            else:
                resource = self.repository.get_tool_version(identity, resource_id, resource_version)
                if resource.status is not LifecycleStatus.PUBLISHED:
                    raise DomainError(
                        "DEPENDENCY_VALIDATION_ERROR", "只能绑定已发布 Tool 版本", {"tool_id": resource_id}
                    )
                self._validate_hub_reference(
                    identity,
                    HubResourceType.TOOL,
                    resource_id,
                    resource_version,
                    missing,
                    is_reference_marker=resource.handler_name == "hub-reference",
                )
        if missing:
            raise DomainError(
                "DEPENDENCY_VALIDATION_ERROR", "资源中心引用未授权或摘要已变化", {"missing": missing}
            )
        self.repository.replace_employee_resource_bindings(
            identity, employee_id, version, slot, normalized_refs
        )
        return self.repository.get_employee_version(identity, employee_id, version)

    def update_employee(
        self,
        identity: IdentityContext,
        employee_id: str,
        *,
        name: str | None = None,
        role_name: str | None = None,
        description: str | None = None,
    ) -> DigitalEmployee:
        current = self.repository.get_employee(identity, employee_id)
        self._require_employee_admin(identity)
        if current.status is LifecycleStatus.ARCHIVED:
            raise DomainError("INVALID_STATE", "已归档员工不能修改基本信息")
        updated = replace(
            current,
            name=name if name is not None else current.name,
            role_name=role_name if role_name is not None else current.role_name,
            description=description if description is not None else current.description,
        )
        self.repository.save_employee(identity, updated)
        return updated

    def archive_employee(self, identity: IdentityContext, employee_id: str) -> DigitalEmployee:
        """软归档员工身份；历史版本、Deployment 和 Run 保留。"""

        current = self.repository.get_employee(identity, employee_id)
        self._require_employee_admin(identity)
        if current.status is LifecycleStatus.ARCHIVED:
            return current
        archived = replace(current, status=LifecycleStatus.ARCHIVED)
        self.repository.save_employee(identity, archived)
        return archived

    def delete_employee(self, identity: IdentityContext, employee_id: str) -> None:
        self.repository.get_employee(identity, employee_id)
        self._require_employee_admin(identity)
        self.repository.delete_employee(identity, employee_id)

    def create_profile(
        self,
        identity: IdentityContext,
        engine_name: str,
        model_alias: str,
        *,
        model_route_id: str | None = None,
        workspace_profile_id: str | None = None,
        workspace_profile_version: int | None = None,
        sandbox_profile_id: str | None = None,
        sandbox_profile_version: int | None = None,
        approval_policy: str = "none",
    ) -> ExecutionProfile:
        dependencies = []
        if model_route_id is not None:
            dependencies.append((model_route_id, self.repository.get_model_route, "model_route", None))
        if workspace_profile_id is not None:
            dependencies.append((workspace_profile_id, self.repository.get_workspace_profile, "workspace_profile", workspace_profile_version or 1))
        if sandbox_profile_id is not None:
            dependencies.append((sandbox_profile_id, self.repository.get_sandbox_profile, "sandbox_profile", sandbox_profile_version or 1))
        for ref, getter, kind, version in dependencies:
            try:
                if version is None:
                    getter(identity, ref)
                else:
                    getter(identity, ref, version=version)
            except DomainError as error:
                if error.code != "RESOURCE_NOT_FOUND":
                    raise
                raise DomainError(
                    "DEPENDENCY_VALIDATION_ERROR",
                    "ExecutionProfile 引用不存在",
                    {"kind": kind, "id": ref},
                ) from error
        profile = ExecutionProfile(
            profile_id=self._new_id("profile"),
            tenant_id=identity.tenant_id,
            engine_name=engine_name,
            model_alias=model_alias,
            model_route_id=model_route_id,
            workspace_profile_id=workspace_profile_id,
            workspace_profile_version=workspace_profile_version,
            sandbox_profile_id=sandbox_profile_id,
            sandbox_profile_version=sandbox_profile_version,
            approval_policy=approval_policy,
        ).publish()
        self.repository.save_profile(identity, profile)
        return profile

    def list_profiles(self, identity: IdentityContext) -> tuple[ExecutionProfile, ...]:
        return self.repository.list_profiles(identity)

    def create_employee(
        self, identity: IdentityContext, name: str, role_name: str, description: str
    ) -> DigitalEmployee:
        self._require_employee_admin(identity)
        value = DigitalEmployee(
            self._new_id("employee"), identity.tenant_id, name, role_name, description
        )
        self.repository.save_employee(identity, value)
        self._ensure_profile(identity)
        return value

    def create_employee_version(
        self,
        identity: IdentityContext,
        employee_id: str,
        system_prompt: str,
        skill_version_ids: Iterable[str],
        tool_version_ids: Iterable[str],
        execution_profile_id: str = "profile-fake",
        model_route_id: str | None = None,
        workspace_profile_id: str | None = None,
        sandbox_profile_id: str | None = None,
        approval_policy: str = "none",
        workspace_profile_version: int | None = None,
        sandbox_profile_version: int | None = None,
    ) -> DigitalEmployeeVersion:
        self._require_employee_admin(identity)
        employee = self.repository.get_employee(identity, employee_id)
        if employee.status is LifecycleStatus.ARCHIVED:
            raise DomainError("INVALID_STATE", "已归档员工不能创建新版本")
        versions = self.repository.list_employee_versions(identity, employee.employee_id)
        next_version = max((version.version for version in versions), default=0) + 1
        self._ensure_profile(identity)
        try:
            profile_version = self.repository.get_profile(identity, execution_profile_id).version
        except DomainError:
            profile_version = 1
        skill_ids = tuple(skill_version_ids)
        tool_ids = tuple(tool_version_ids)
        value = DigitalEmployeeVersion(
            employee_id=employee.employee_id,
            tenant_id=identity.tenant_id,
            version=next_version,
            system_prompt=system_prompt,
            skill_version_ids=skill_ids,
            tool_version_ids=tool_ids,
            execution_profile_id=execution_profile_id,
            model_route_id=model_route_id,
            workspace_profile_id=workspace_profile_id,
            workspace_profile_version=workspace_profile_version,
            sandbox_profile_id=sandbox_profile_id,
            sandbox_profile_version=sandbox_profile_version,
            approval_policy=approval_policy,
            execution_profile_version=profile_version,
            skill_version_refs=tuple(
                (skill_id, self._latest_version(self.repository.get_skill, identity, skill_id, default=1))
                for skill_id in skill_ids
            ),
            tool_version_refs=tuple(
                (tool_id, self._latest_version(self.repository.get_tool, identity, tool_id, default=1))
                for tool_id in tool_ids
            ),
        )
        self.repository.save_employee_version(identity, value)
        return value

    def update_employee_version(
        self,
        identity: IdentityContext,
        employee_id: str,
        version: int,
        *,
        system_prompt: str,
        skill_version_refs: Iterable[tuple[str, int]],
        tool_version_refs: Iterable[tuple[str, int]],
        execution_profile_id: str,
        execution_profile_version: int,
        model_route_id: str | None = None,
        workspace_profile_id: str | None = None,
        workspace_profile_version: int | None = None,
        sandbox_profile_id: str | None = None,
        sandbox_profile_version: int | None = None,
        approval_policy: str = "none",
    ) -> DigitalEmployeeVersion:
        """原子替换指定 DRAFT 版本的内容和精确依赖绑定。"""

        employee = self.repository.get_employee(identity, employee_id)
        self._require_employee_admin(identity)
        if employee.status is LifecycleStatus.ARCHIVED:
            raise DomainError("INVALID_STATE", "已归档员工不能更新版本")
        current = self.repository.get_employee_version(identity, employee_id, version)
        current.assert_mutable()
        skill_refs = tuple((resource_id, resource_version) for resource_id, resource_version in skill_version_refs)
        tool_refs = tuple((resource_id, resource_version) for resource_id, resource_version in tool_version_refs)
        if len({resource_id for resource_id, _ in skill_refs}) != len(skill_refs):
            raise DomainError("INVALID_ARGUMENT", "Skill 绑定不能包含重复资源")
        if len({resource_id for resource_id, _ in tool_refs}) != len(tool_refs):
            raise DomainError("INVALID_ARGUMENT", "Tool 绑定不能包含重复资源")
        updated = replace(
            current,
            system_prompt=system_prompt,
            skill_version_ids=tuple(resource_id for resource_id, _ in skill_refs),
            tool_version_ids=tuple(resource_id for resource_id, _ in tool_refs),
            skill_version_refs=skill_refs,
            tool_version_refs=tool_refs,
            execution_profile_id=execution_profile_id,
            execution_profile_version=execution_profile_version,
            model_route_id=model_route_id,
            workspace_profile_id=workspace_profile_id,
            workspace_profile_version=workspace_profile_version,
            sandbox_profile_id=sandbox_profile_id,
            sandbox_profile_version=sandbox_profile_version,
            approval_policy=approval_policy,
        )
        self.repository.save_employee_version(identity, updated)
        return updated

    def fork_employee_version(
        self, identity: IdentityContext, employee_id: str, source_version: int
    ) -> DigitalEmployeeVersion:
        """复制已发布或已下线员工版本的完整配置到新的 DRAFT。"""

        employee = self.repository.get_employee(identity, employee_id)
        self._require_employee_admin(identity)
        if employee.status is LifecycleStatus.ARCHIVED:
            raise DomainError("INVALID_STATE", "已归档员工不能创建新版本")
        source = self.repository.get_employee_version(identity, employee_id, source_version)
        if source.status not in (LifecycleStatus.PUBLISHED, LifecycleStatus.DEPRECATED):
            raise DomainError("INVALID_STATE", "只能基于已发布或已下线员工版本创建草稿")
        return self.repository.fork_employee_version(identity, employee_id, source_version)

    def deprecate_employee_version(
        self, identity: IdentityContext, employee_id: str, version: int
    ) -> DigitalEmployeeVersion:
        """管理员下线指定员工版本，保留其配置与既有部署。"""

        employee = self.repository.get_employee(identity, employee_id)
        self._require_employee_admin(identity)
        if employee.status is LifecycleStatus.ARCHIVED:
            raise DomainError("INVALID_STATE", "已归档员工不能下线版本")
        current = self.repository.get_employee_version(identity, employee_id, version)
        if current.status not in (LifecycleStatus.PUBLISHED, LifecycleStatus.DEPRECATED):
            raise DomainError("INVALID_STATE", "只有已发布员工版本才能废弃")
        return self.repository.deprecate_employee_version(identity, employee_id, version)

    @staticmethod
    def _canonical_hub_manifest_digest(package: HubResourceVersion) -> str:
        canonical = json.dumps(
            package.manifest.data,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _validate_hub_reference(
        self,
        identity: IdentityContext,
        resource_type: HubResourceType,
        local_resource_id: str,
        local_resource_version: int,
        missing: list[dict[str, str]],
        *,
        is_reference_marker: bool,
    ) -> tuple[TenantResourceImport, HubResourceVersion] | None:
        """发布预检重新核对引用映射、精确 Grant 和 Hub Manifest 摘要。"""

        repository = self.resource_center_repository
        if repository is None:
            if is_reference_marker:
                missing.append(
                    {"kind": "hub_resolver", "id": local_resource_id, "reason": "REPOSITORY_NOT_CONFIGURED"}
                )
            return None
        imported = repository.get_import_for_local_resource(
            identity, resource_type, local_resource_id, local_resource_version
        )
        if imported is None:
            if is_reference_marker:
                missing.append(
                    {"kind": "hub_import", "id": local_resource_id, "reason": "IMPORT_MAPPING_MISSING"}
                )
            return None
        if imported.status is not TenantImportStatus.ACTIVE:
            missing.append(
                {"kind": "hub_import", "id": local_resource_id, "reason": "IMPORT_NOT_ACTIVE"}
            )
            return None
        if imported.mode is HubImportMode.COPY:
            return None
        try:
            package = repository.get_authorized_version(
                identity,
                imported.hub_resource_id,
                imported.hub_version,
                HubImportMode.REFERENCE,
            )
        except DomainError as error:
            if error.code != "RESOURCE_NOT_FOUND":
                raise
            missing.append(
                {
                    "kind": "hub_grant",
                    "id": local_resource_id,
                    "hub_resource_id": imported.hub_resource_id,
                    "reason": "GRANT_REVOKED_OR_EXPIRED",
                }
            )
            return None
        if (
            package.source_resource_type is not resource_type
            or package.manifest.sha256 != imported.manifest_sha256
            or self._canonical_hub_manifest_digest(package) != imported.manifest_sha256
        ):
            missing.append(
                {
                    "kind": "hub_manifest",
                    "id": local_resource_id,
                    "hub_resource_id": imported.hub_resource_id,
                    "reason": "MANIFEST_DIGEST_MISMATCH",
                }
            )
            return None
        return imported, package

    def publish_employee_version(
        self, identity: IdentityContext, employee_id: str, version: int
    ) -> DigitalEmployeeVersion:
        employee = self.repository.get_employee(identity, employee_id)
        self._require_employee_admin(identity)
        if employee.status is LifecycleStatus.ARCHIVED:
            raise DomainError("INVALID_STATE", "已归档员工不能发布新版本")
        current = self.repository.get_employee_version(identity, employee_id, version)
        current.assert_mutable()
        missing: list[dict[str, str]] = []
        for skill_id, skill_version in current.skill_version_refs:
            try:
                skill = self.repository.get_skill_version(identity, skill_id, skill_version)
            except DomainError:
                missing.append({"kind": "skill", "id": skill_id})
            else:
                if skill.status is not LifecycleStatus.PUBLISHED:
                    missing.append({"kind": "skill", "id": skill_id})
                reference = self._validate_hub_reference(
                    identity,
                    HubResourceType.SKILL,
                    skill_id,
                    skill_version,
                    missing,
                    is_reference_marker=skill.instruction.startswith("hub-reference://"),
                )
                if reference is not None:
                    imported, package = reference
                    content = package.manifest.data.get("content")
                    if (
                        isinstance(content, dict)
                        and content.get("content_format") == "FILE_PACKAGE"
                        and content.get("file_manifest_sha256") != skill.file_manifest_sha256
                    ):
                        missing.append(
                            {
                                "kind": "hub_manifest",
                                "id": skill_id,
                                "hub_resource_id": imported.hub_resource_id,
                                "reason": "SKILL_FILE_MANIFEST_MISMATCH",
                            }
                        )
                    if imported.mode is HubImportMode.REFERENCE:
                        expected_tools: list[tuple[str, int]] = []
                        dependencies = package.manifest.data.get("dependencies", [])
                        if isinstance(dependencies, list) and self.resource_center_repository is not None:
                            for dependency in dependencies:
                                if not isinstance(dependency, dict) or dependency.get("type") != HubResourceType.TOOL.value:
                                    continue
                                try:
                                    child_id = str(dependency["resource_id"])
                                    child_version = int(dependency["version"])
                                except (KeyError, TypeError, ValueError):
                                    missing.append(
                                        {
                                            "kind": "hub_dependency",
                                            "id": skill_id,
                                            "reason": "DEPENDENCY_REFERENCE_INVALID",
                                        }
                                    )
                                    continue
                                child_import = self.resource_center_repository.get_import(
                                    identity, child_id, child_version, HubImportMode.REFERENCE
                                )
                                if (
                                    child_import is None
                                    or child_import.status is not TenantImportStatus.ACTIVE
                                    or child_import.local_resource_type is not HubResourceType.TOOL
                                ):
                                    missing.append(
                                        {
                                            "kind": "hub_grant",
                                            "id": child_id,
                                            "hub_resource_id": child_id,
                                            "reason": "DEPENDENCY_IMPORT_MISSING",
                                        }
                                    )
                                    continue
                                expected_tools.append(
                                    (child_import.local_resource_id, child_import.local_resource_version)
                                )
                                self._validate_hub_reference(
                                    identity,
                                    HubResourceType.TOOL,
                                    child_import.local_resource_id,
                                    child_import.local_resource_version,
                                    missing,
                                    is_reference_marker=True,
                                )
                        if tuple(expected_tools) != skill.allowed_tool_versions:
                            missing.append(
                                {
                                    "kind": "hub_dependency",
                                    "id": skill_id,
                                    "hub_resource_id": imported.hub_resource_id,
                                    "reason": "LOCAL_BINDINGS_MISMATCH",
                                }
                            )
        for tool_id, tool_version in current.tool_version_refs:
            try:
                tool = self.repository.get_tool_version(identity, tool_id, tool_version)
            except DomainError:
                missing.append({"kind": "tool", "id": tool_id})
            else:
                if tool.status is not LifecycleStatus.PUBLISHED:
                    missing.append({"kind": "tool", "id": tool_id})
                self._validate_hub_reference(
                    identity,
                    HubResourceType.TOOL,
                    tool_id,
                    tool_version,
                    missing,
                    is_reference_marker=tool.handler_name == "hub-reference",
                )
                if tool.requires_recipient_credentials and tool.auth_secret_ref is None:
                    missing.append(
                        {
                            "kind": "recipient_credentials",
                            "id": tool_id,
                            "reason": "SECRET_REF_MISSING",
                        }
                    )
        try:
            profile = self.repository.get_profile_version(
                identity, current.execution_profile_id, current.execution_profile_version
            )
        except DomainError:
            missing.append({"kind": "execution_profile", "id": current.execution_profile_id})
        else:
            if profile.status is not LifecycleStatus.PUBLISHED:
                missing.append({"kind": "execution_profile", "id": current.execution_profile_id})
        for field_name, version_field, getter, kind in (
            ("model_route_id", None, self.repository.get_model_route, "model_route"),
            ("workspace_profile_id", "workspace_profile_version", self.repository.get_workspace_profile, "workspace_profile"),
            ("sandbox_profile_id", "sandbox_profile_version", self.repository.get_sandbox_profile, "sandbox_profile"),
        ):
            ref = getattr(current, field_name)
            if ref is not None:
                try:
                    if version_field is None:
                        dependency = getter(identity, ref)
                    else:
                        dependency = getter(identity, ref, version=getattr(current, version_field) or 1)
                except DomainError as error:
                    if error.code != "RESOURCE_NOT_FOUND":
                        raise
                    missing.append({"kind": kind, "id": ref})
                    continue
                if kind == "model_route":
                    if dependency.status != "ACTIVE":
                        missing.append(
                            {"kind": kind, "id": ref, "reason": "ROUTE_DISABLED"}
                        )
                        continue
                    connection_id = dependency.profile.connection_id
                    catalog_model_id = dependency.profile.catalog_model_id
                    if connection_id is None or catalog_model_id is None:
                        missing.append(
                            {"kind": kind, "id": ref, "reason": "MODEL_CONNECTION_MISSING"}
                        )
                        continue
                    try:
                        model_connection = self.repository.get_model_connection(
                            identity, connection_id
                        )
                    except DomainError as error:
                        if error.code != "RESOURCE_NOT_FOUND":
                            raise
                        missing.append(
                            {
                                "kind": "model_connection",
                                "id": connection_id,
                                "reason": "CONNECTION_MISSING",
                            }
                        )
                        continue
                    if model_connection.status.value != "ACTIVE":
                        missing.append(
                            {
                                "kind": "model_connection",
                                "id": connection_id,
                                "reason": "CONNECTION_DISABLED",
                            }
                        )
                        continue
                    try:
                        catalog_item = self.repository.get_model_catalog_item(
                            identity, connection_id, catalog_model_id
                        )
                    except DomainError as error:
                        if error.code != "RESOURCE_NOT_FOUND":
                            raise
                        missing.append(
                            {
                                "kind": "model_catalog_item",
                                "id": catalog_model_id,
                                "reason": "MODEL_MISSING",
                            }
                        )
                        continue
                    if catalog_item.status.value != "ACTIVE":
                        missing.append(
                            {
                                "kind": "model_catalog_item",
                                "id": catalog_model_id,
                                "reason": "MODEL_DISABLED",
                            }
                        )
        if missing:
            raise DomainError(
                "DEPENDENCY_VALIDATION_ERROR",
                "数字员工版本依赖未满足发布条件",
                {"missing": missing},
            )
        published = current.publish()
        self.repository.save_employee_version(identity, published)
        return published

    def deploy(self, identity: IdentityContext, employee_id: str, version: int) -> Deployment:
        employee = self.repository.get_employee(identity, employee_id)
        self._require_employee_admin(identity)
        if employee.status is LifecycleStatus.ARCHIVED:
            raise DomainError("INVALID_STATE", "已归档员工不能创建新的部署")
        employee_version = self.repository.get_employee_version(identity, employee_id, version)
        # 历史部署可引用已下线版本，新建部署仍只接受已发布版本。
        if employee_version.status is not LifecycleStatus.PUBLISHED:
            raise DomainError("INVALID_STATE", "只能部署已发布员工版本")
        deployment = Deployment(
            deployment_id=self._new_id("deployment"),
            tenant_id=identity.tenant_id,
            employee_id=employee_id,
            employee_version=employee_version,
        )
        return self.repository.save_deployment(identity, deployment)

    def list_tools(self, identity: IdentityContext) -> tuple[ToolVersion, ...]:
        return self.repository.list_tools(identity)

    def list_skills(self, identity: IdentityContext) -> tuple[SkillVersion, ...]:
        return self.repository.list_skills(identity)

    def list_all_skill_versions(self, identity: IdentityContext) -> tuple[SkillVersion, ...]:
        """返回租户内全部精确 Skill 版本，供绑定选择器找到历史已发布版本。"""

        versions = [
            version
            for latest in self.repository.list_skills(identity)
            for version in self.repository.list_skill_versions(identity, latest.skill_id)
        ]
        return tuple(sorted(versions, key=lambda item: (item.name.casefold(), item.skill_id, item.version)))

    def list_employees(self, identity: IdentityContext) -> tuple[DigitalEmployee, ...]:
        return self.repository.list_employees(identity)

    def get_employee(self, identity: IdentityContext, employee_id: str) -> DigitalEmployee:
        return self.repository.get_employee(identity, employee_id)

    def get_employee_version(
        self, identity: IdentityContext, employee_id: str, version: int
    ) -> DigitalEmployeeVersion:
        self.repository.get_employee(identity, employee_id)
        return self.repository.get_employee_version(identity, employee_id, version)

    def list_employees_page(
        self,
        identity: IdentityContext,
        *,
        keyword: str | None = None,
        status: LifecycleStatus | str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> DigitalEmployeePage:
        """在当前租户集合内按名称/ID 稳定排序，并返回与筛选绑定的游标。"""

        if not 1 <= limit <= 200:
            raise DomainError("INVALID_ARGUMENT", "limit 必须在 1 到 200 之间")
        selected_status = LifecycleStatus(status).value if status is not None else None
        normalized_keyword = keyword.strip().casefold() if keyword and keyword.strip() else None
        cursor_context: dict[str, object] = {
            "kind": "employee",
            "keyword": normalized_keyword,
            "status": selected_status,
        }
        after: tuple[str, str] | None = None
        if cursor:
            decoded = self._decode_page_cursor(cursor, cursor_context)
            name, employee_id = decoded.get("name"), decoded.get("employee_id")
            if not isinstance(name, str) or not isinstance(employee_id, str):
                raise DomainError("INVALID_ARGUMENT", "员工列表游标内容非法")
            after = (name.casefold(), employee_id)
        employees = self.repository.list_employees(identity)
        if selected_status is not None:
            employees = tuple(item for item in employees if item.status.value == selected_status)
        if normalized_keyword is not None:
            employees = tuple(
                item
                for item in employees
                if normalized_keyword
                in f"{item.name} {item.role_name} {item.description}".casefold()
            )
        ordered = tuple(sorted(employees, key=lambda item: (item.name.casefold(), item.employee_id)))
        if after is not None:
            ordered = tuple(
                item
                for item in ordered
                if (item.name.casefold(), item.employee_id) > after
            )
        has_more = len(ordered) > limit
        page_items = ordered[:limit]
        next_cursor = None
        if has_more and page_items:
            last = page_items[-1]
            next_cursor = self._encode_page_cursor(
                {**cursor_context, "name": last.name, "employee_id": last.employee_id}
            )
        return DigitalEmployeePage(page_items, next_cursor)

    def list_employee_versions(
        self, identity: IdentityContext, employee_id: str
    ) -> tuple[DigitalEmployeeVersion, ...]:
        self.repository.get_employee(identity, employee_id)
        return self.repository.list_employee_versions(identity, employee_id)

    def list_employee_versions_page(
        self,
        identity: IdentityContext,
        employee_id: str,
        *,
        status: LifecycleStatus | str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> DigitalEmployeeVersionPage:
        """按版本号倒序返回单个租户员工的稳定版本游标页。"""

        if not 1 <= limit <= 200:
            raise DomainError("INVALID_ARGUMENT", "limit 必须在 1 到 200 之间")
        self.repository.get_employee(identity, employee_id)
        selected_status = LifecycleStatus(status).value if status is not None else None
        cursor_context: dict[str, object] = {
            "kind": "employee_version",
            "employee_id": employee_id,
            "status": selected_status,
        }
        before_version: int | None = None
        if cursor:
            decoded = self._decode_page_cursor(cursor, cursor_context)
            last_version = decoded.get("version")
            if not isinstance(last_version, int) or last_version < 1:
                raise DomainError("INVALID_ARGUMENT", "员工版本游标内容非法")
            before_version = last_version
        versions = self.repository.list_employee_versions(identity, employee_id)
        if selected_status is not None:
            versions = tuple(item for item in versions if item.status.value == selected_status)
        ordered = tuple(sorted(versions, key=lambda item: item.version, reverse=True))
        if before_version is not None:
            ordered = tuple(item for item in ordered if item.version < before_version)
        has_more = len(ordered) > limit
        page_items = ordered[:limit]
        next_cursor = None
        if has_more and page_items:
            next_cursor = self._encode_page_cursor(
                {**cursor_context, "version": page_items[-1].version}
            )
        return DigitalEmployeeVersionPage(page_items, next_cursor)

    def rollback(self, identity: IdentityContext, deployment_id: str, employee_id: str, version: int) -> Deployment:
        self._require_employee_admin(identity)
        employee = self.repository.get_employee(identity, employee_id)
        if employee.status is LifecycleStatus.ARCHIVED:
            raise DomainError("INVALID_STATE", "已归档员工不能回滚部署")
        current = self.repository.get_deployment(identity, deployment_id)
        target = self.repository.get_employee_version(identity, employee_id, version)
        if target.status is not LifecycleStatus.PUBLISHED:
            raise DomainError("DEPENDENCY_VALIDATION_ERROR", "回滚目标必须是已发布版本")
        return self.repository.save_deployment(identity, current.rollback(target))

    def disable_deployment(self, identity: IdentityContext, deployment_id: str) -> Deployment:
        """管理员下线当前租户的部署，停止该入口的新会话和运行。"""

        self._require_employee_admin(identity)
        return self.repository.transition_deployment(identity, deployment_id, LifecycleStatus.DISABLED)

    def enable_deployment(self, identity: IdentityContext, deployment_id: str) -> Deployment:
        """管理员重新上线固定版本仍为已发布的部署。"""

        self._require_employee_admin(identity)
        current = self.repository.get_deployment(identity, deployment_id)
        employee = self.repository.get_employee(identity, current.employee_id)
        if employee.status is LifecycleStatus.ARCHIVED:
            raise DomainError("INVALID_STATE", "已归档员工不能重新上线部署")
        # 仓储在锁内再次校验，避免读取后发生回滚、归档或版本下线。
        current.enable()
        return self.repository.transition_deployment(identity, deployment_id, LifecycleStatus.ACTIVE)

    def resolve_deployment(self, identity: IdentityContext, deployment_id: str) -> Deployment:
        return self.repository.get_deployment(identity, deployment_id)

    def list_deployments(self, identity: IdentityContext) -> tuple[Deployment, ...]:
        return self.repository.list_deployments(identity)

    def list_deployments_page(
        self,
        identity: IdentityContext,
        *,
        employee_id: str | None = None,
        status: LifecycleStatus | str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> DeploymentPage:
        """返回当前租户部署列表；稳定排序键为 deployment_id。"""

        if not 1 <= limit <= 200:
            raise DomainError("INVALID_ARGUMENT", "limit 必须在 1 到 200 之间")
        selected_status = LifecycleStatus(status).value if status is not None else None
        context: dict[str, object] = {
            "kind": "deployment",
            "employee_id": employee_id,
            "status": selected_status,
        }
        after_id: str | None = None
        if cursor:
            decoded = self._decode_page_cursor(cursor, context)
            after_id = decoded.get("deployment_id")
            if not isinstance(after_id, str):
                raise DomainError("INVALID_ARGUMENT", "Deployment 游标内容非法")
        deployments = self.repository.list_deployments(identity)
        if employee_id is not None:
            deployments = tuple(item for item in deployments if item.employee_id == employee_id)
        if selected_status is not None:
            deployments = tuple(item for item in deployments if item.status.value == selected_status)
        ordered = tuple(sorted(deployments, key=lambda item: item.deployment_id))
        if after_id is not None:
            ordered = tuple(item for item in ordered if item.deployment_id > after_id)
        has_more = len(ordered) > limit
        page_items = ordered[:limit]
        next_cursor = (
            self._encode_page_cursor({**context, "deployment_id": page_items[-1].deployment_id})
            if has_more and page_items
            else None
        )
        return DeploymentPage(page_items, next_cursor)

    def list_audit_events_page(
        self,
        identity: IdentityContext,
        *,
        resource_type: str | None = None,
        resource_id: str | None = None,
        event_type: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> AuditEventPage:
        """管理员查询当前租户审计事件，按时间和 event_id 稳定倒序分页。"""

        self._require_employee_admin(identity)
        if not 1 <= limit <= 200:
            raise DomainError("INVALID_ARGUMENT", "limit 必须在 1 到 200 之间")
        context: dict[str, object] = {
            "kind": "audit_event",
            "resource_type": resource_type,
            "resource_id": resource_id,
            "event_type": event_type,
        }
        before_created_at: datetime | None = None
        before_event_id: str | None = None
        if cursor:
            decoded = self._decode_page_cursor(cursor, context)
            created_value = decoded.get("created_at")
            event_id = decoded.get("event_id")
            if not isinstance(created_value, str) or not isinstance(event_id, str):
                raise DomainError("INVALID_ARGUMENT", "审计事件游标内容非法")
            try:
                before_created_at = datetime.fromisoformat(created_value)
            except ValueError as error:
                raise DomainError("INVALID_ARGUMENT", "审计事件游标时间非法") from error
            if before_created_at.tzinfo is None:
                raise DomainError("INVALID_ARGUMENT", "审计事件游标时间必须包含时区")
            before_event_id = event_id
        events = self.repository.list_audit_events(
            identity,
            resource_type=resource_type,
            resource_id=resource_id,
            event_type=event_type,
            limit=limit + 1,
            before_created_at=before_created_at,
            before_event_id=before_event_id,
        )
        has_more = len(events) > limit
        page_items = events[:limit]
        next_cursor = None
        if has_more and page_items:
            last = page_items[-1]
            next_cursor = self._encode_page_cursor(
                {
                    **context,
                    "created_at": last.created_at.isoformat(),
                    "event_id": last.event_id,
                }
            )
        return AuditEventPage(page_items, next_cursor)

    @staticmethod
    def _latest_version(getter: Callable, identity: IdentityContext, resource_id: str, *, default: int) -> int:
        try:
            return int(getter(identity, resource_id).version)
        except DomainError as error:
            if error.code == "RESOURCE_NOT_FOUND":
                return default
            raise
