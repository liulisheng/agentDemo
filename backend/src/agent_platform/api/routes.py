import base64
import binascii
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request

from agent_platform.api.dependencies import (
    catalog_service,
    control_plane_service,
    conversation_service,
    current_identity,
    model_route_service,
    resource_center_service,
    skill_file_package_service,
)
from agent_platform.api.schemas import (
    AgentBindingRequest,
    ConversationCreateRequest,
    DeliveryCreateRequest,
    DeploymentCreateRequest,
    DeploymentRollbackRequest,
    EmployeeCreateRequest,
    EmployeeUpdateRequest,
    EmployeeVersionCreateRequest,
    EmployeeVersionTestRequest,
    EmployeeVersionUpdateRequest,
    ExecutionProfileCreateRequest,
    HubGrantCreateRequest,
    HubImportUpgradeRequest,
    HubPackageCreateRequest,
    HubPackageVersionCreateRequest,
    HubResourceImportRequest,
    MessageCreateRequest,
    ModelRouteCreateRequest,
    RunCreateRequest,
    SessionCreateRequest,
    SkillCreateRequest,
    SkillFileCreateRequest,
    SkillFileRenameRequest,
    SkillFileUpdateRequest,
    TenantAwareRequest,
    ToolCreateRequest,
    ToolUpdateRequest,
)
from agent_platform.application.catalog_service import CatalogService
from agent_platform.application.control_plane import ControlPlaneService
from agent_platform.application.conversations import ConversationService
from agent_platform.application.model_routes import ModelRouteService
from agent_platform.application.resource_center import ResourceCenterService
from agent_platform.application.skill_files import SkillFilePackageService, SkillZipPreview
from agent_platform.domain.agents import FakeDigitalEmployeeRepository
from agent_platform.domain.catalog import (
    Deployment,
    DigitalEmployee,
    DigitalEmployeeVersion,
    LifecycleStatus,
    SkillVersion,
    ToolVersion,
)
from agent_platform.domain.common import DomainError
from agent_platform.domain.identity import IdentityContext
from agent_platform.domain.observability import RunRecord, RunTimeline, SessionRecord, TraceSpan
from agent_platform.domain.resource_center import (
    HubImportMode,
    HubResource,
    HubResourceType,
    HubResourceVersion,
    TenantImportStatus,
)

router = APIRouter(prefix="/api/v1")

# 旧 Gallery 契约保留本地目录；新建资源通过 CatalogService 进入最小闭环。
employee_repository = FakeDigitalEmployeeRepository()

Identity = Annotated[IdentityContext, Depends(current_identity)]
Catalog = Annotated[CatalogService, Depends(catalog_service)]
ControlPlane = Annotated[ControlPlaneService, Depends(control_plane_service)]
Conversations = Annotated[ConversationService, Depends(conversation_service)]
ModelRoutes = Annotated[ModelRouteService, Depends(model_route_service)]
ResourceCenter = Annotated[ResourceCenterService, Depends(resource_center_service)]
SkillFiles = Annotated[SkillFilePackageService, Depends(skill_file_package_service)]


def _check_tenant(identity: IdentityContext, request: TenantAwareRequest) -> None:
    if request.tenant_id is not None and request.tenant_id != identity.tenant_id:
        raise DomainError(
            "AUTHORIZATION_DENIED",
            "请求租户与认证上下文不一致",
            {"tenant_id": identity.tenant_id},
        )


def _tool_response(value: ToolVersion) -> dict[str, object]:
    return {
        "id": value.tool_id,
        "tenant_id": value.tenant_id,
        "name": value.name,
        "description": value.description,
        "version": value.version,
        "status": value.status.value,
        "protocol": value.protocol,
        "requires_recipient_credentials": value.requires_recipient_credentials,
        "credentials_configured": value.auth_secret_ref is not None,
        "input_schema": dict(value.input_schema),
    }


def _skill_response(value: SkillVersion) -> dict[str, object]:
    return {
        "id": value.skill_id,
        "tenant_id": value.tenant_id,
        "name": value.name,
        "skill_type": value.skill_type.value,
        "instruction": value.instruction,
        "allowed_tool_ids": list(value.allowed_tool_ids),
        "allowed_tool_versions": [
            {"tool_id": tool_id, "version": version}
            for tool_id, version in value.allowed_tool_versions
        ],
        "version": value.version,
        "status": value.status.value,
    }


def _employee_response(value: DigitalEmployee) -> dict[str, object]:
    return {
        "id": value.employee_id,
        "tenant_id": value.tenant_id,
        "name": value.name,
        "role_name": value.role_name,
        "description": value.description,
        "status": value.status.value,
        "published_to_gallery": value.published_to_gallery,
    }


def _version_response(value: DigitalEmployeeVersion) -> dict[str, object]:
    return {
        "employee_id": value.employee_id,
        "tenant_id": value.tenant_id,
        "version": value.version,
        "system_prompt": value.system_prompt,
        "skill_ids": list(value.skill_version_ids),
        "tool_ids": list(value.tool_version_ids),
        "skill_version_refs": [
            {"skill_id": skill_id, "version": version}
            for skill_id, version in value.skill_version_refs
        ],
        "tool_version_refs": [
            {"tool_id": tool_id, "version": version}
            for tool_id, version in value.tool_version_refs
        ],
        "execution_profile_id": value.execution_profile_id,
        "model_route_id": value.model_route_id,
        "workspace_profile_id": value.workspace_profile_id,
        "workspace_profile_version": value.workspace_profile_version,
        "sandbox_profile_id": value.sandbox_profile_id,
        "sandbox_profile_version": value.sandbox_profile_version,
        "execution_profile_version": value.execution_profile_version,
        "approval_policy": value.approval_policy,
        "status": value.status.value,
    }


def _deployment_response(value: Deployment) -> dict[str, object]:
    return {
        "id": value.deployment_id,
        "tenant_id": value.tenant_id,
        "employee_id": value.employee_id,
        "version": value.employee_version.version,
        "status": value.status.value,
        "version_summary": {
            "version": value.employee_version.version,
            "status": value.employee_version.status.value,
            "skill_count": len(value.employee_version.skill_version_refs),
            "tool_count": len(value.employee_version.tool_version_refs),
            "execution_profile_id": value.employee_version.execution_profile_id,
            "execution_profile_version": value.employee_version.execution_profile_version,
            "model_route_id": value.employee_version.model_route_id,
        },
    }


def _session_response(value: SessionRecord) -> dict[str, object]:
    return {
        "id": value.session_id,
        "deployment_id": value.deployment_id,
        "employee_version": value.employee_version,
        "trace_id": value.trace_id,
        "runtime_session_ref": value.runtime_session_ref,
        "status": value.status,
    }


def _run_response(value: RunRecord) -> dict[str, object]:
    return {
        "id": value.run_id,
        "session_id": value.session_id,
        "trace_id": value.trace_id,
        "status": value.status.value,
        "result": value.result_text,
        "result_ref": value.result_ref,
        "error_category": value.error_category,
        "snapshot": {
            "employee_id": value.snapshot.employee_id,
            "employee_version": value.snapshot.employee_version,
            "skill_ids": list(value.snapshot.skill_version_ids),
            "tool_ids": list(value.snapshot.tool_version_ids),
            "execution_profile_id": value.snapshot.execution_profile_id,
            "hub_resource_refs": [
                {
                    "resource_type": item.local_resource_type,
                    "local_resource_id": item.local_resource_id,
                    "local_resource_version": item.local_resource_version,
                    "hub_resource_id": item.hub_resource_id,
                    "hub_version": item.hub_version,
                    "manifest_sha256": item.manifest_sha256,
                    "mode": item.mode,
                }
                for item in value.snapshot.hub_resource_refs
            ],
            "skill_file_packages": [
                {
                    "skill_id": package.skill_id,
                    "skill_version": package.skill_version,
                    "entry_path": package.entry_path,
                    "manifest_sha256": package.manifest_sha256,
                    "files": [
                        {
                            "path": item.file_path,
                            "mime_type": item.mime_type,
                            "size_bytes": item.size_bytes,
                            "sha256": item.content_sha256,
                        }
                        for item in package.files
                    ],
                }
                for package in value.snapshot.skill_file_packages
            ],
        },
        "runtime_reference": (
            {
                "engine_name": value.runtime_reference.engine_name,
                "runtime_session_ref": value.runtime_reference.runtime_session_ref,
                "runtime_result_ref": value.runtime_reference.runtime_result_ref,
            }
            if value.runtime_reference
            else None
        ),
    }


def _span_response(value: TraceSpan) -> dict[str, object]:
    return {
        "id": value.span_id,
        "trace_id": value.trace_id,
        "parent_span_id": value.parent_span_id,
        "run_id": value.run_id,
        "span_type": value.span_type.value,
        "status": value.status,
        "sequence": value.sequence,
        "attributes": dict(value.attributes),
    }


def _timeline_response(value: RunTimeline) -> dict[str, object]:
    return {
        "run": _run_response(value.run),
        "spans": [_span_response(span) for span in value.spans],
        "usage": {
            "input_tokens": value.usage.input_tokens,
            "output_tokens": value.usage.output_tokens,
            "total_tokens": value.usage.total_tokens,
            "source": value.usage.source,
            "estimated": value.usage.estimated,
            "cost_amount": value.usage.cost_amount,
            "currency": value.usage.currency,
            "pricing_version": value.usage.pricing_version,
        },
        "runtime_reference": (
            {
                "engine_name": value.runtime_reference.engine_name,
                "runtime_session_ref": value.runtime_reference.runtime_session_ref,
                "runtime_result_ref": value.runtime_reference.runtime_result_ref,
            }
            if value.runtime_reference
            else None
        ),
    }


@router.get("/digital-employees")
def list_digital_employees(
    identity: Identity,
    service: Catalog,
    request: Request,
    keyword: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
    status: Annotated[LifecycleStatus | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query(max_length=1024)] = None,
) -> dict[str, object]:
    """按名称/ID 稳定排序，支持租户范围关键词、状态和游标筛选。"""

    if request.app.state.use_fake_gallery:
        employees = [
                {
                    "id": employee.employee_id,
                    "tenant_id": employee.tenant_id,
                    "name": employee.name,
                    "role_name": employee.role_name,
                    "description": employee.description,
                    "status": employee.status,
                    "published_to_gallery": employee.published_to_gallery,
                    "resource_counts": {
                        "knowledge_base": employee.knowledge_count,
                        "skill": employee.skill_count,
                        "sop": employee.sop_count,
                    },
                }
                for employee in employee_repository.list_available(identity)
            ]
        return {"items": employees, "next_cursor": None}

    page = service.list_employees_page(
        identity, keyword=keyword, status=status, limit=limit, cursor=cursor
    )
    items = []
    for employee in page.items:
        published_versions = [
            version
            for version in service.list_employee_versions(identity, employee.employee_id)
            if version.status.value == "PUBLISHED"
        ]
        latest = max(published_versions, key=lambda value: value.version, default=None)
        items.append(
            {
                "id": employee.employee_id,
                "tenant_id": employee.tenant_id,
                "name": employee.name,
                "role_name": employee.role_name,
                "description": employee.description,
                "status": employee.status.value,
                "published_to_gallery": employee.published_to_gallery,
                "resource_counts": {
                    "knowledge_base": 0,
                    "skill": len(latest.skill_version_ids) if latest else 0,
                    "sop": 0,
                    "tool": len(latest.tool_version_ids) if latest else 0,
                },
            }
        )
    return {"items": items, "next_cursor": page.next_cursor}


@router.get("/digital-employees/{employee_id}")
def get_digital_employee(
    employee_id: str, identity: Identity, service: Catalog
) -> dict[str, object]:
    """返回员工基本信息和当前已发布版本摘要。"""

    employee = service.get_employee(identity, employee_id)
    versions = service.list_employee_versions(identity, employee_id)
    published = [version for version in versions if version.status is LifecycleStatus.PUBLISHED]
    latest = max(published, key=lambda version: version.version, default=None)
    return {
        **_employee_response(employee),
        "version_count": len(versions),
        "latest_published_version": (
            {"version": latest.version, "status": latest.status.value} if latest else None
        ),
    }


@router.patch("/digital-employees/{employee_id}")
def patch_digital_employee(
    employee_id: str,
    request: EmployeeUpdateRequest,
    identity: Identity,
    service: Catalog,
) -> dict[str, object]:
    """部分更新员工稳定资料；租户成员只读，管理员可写。"""

    _check_tenant(identity, request)
    if request.name is None and request.role_name is None and request.description is None:
        raise DomainError("INVALID_ARGUMENT", "PATCH 至少提供一个员工基本信息字段")
    return _employee_response(
        service.update_employee(
            identity,
            employee_id,
            name=request.name,
            role_name=request.role_name,
            description=request.description,
        )
    )


@router.post("/digital-employees/{employee_id}/archive")
def archive_digital_employee(
    employee_id: str, identity: Identity, service: Catalog
) -> dict[str, object]:
    """软归档员工；历史版本、部署和 Run 保留，停止新版本/部署写入。"""

    return _employee_response(service.archive_employee(identity, employee_id))


@router.get("/digital-employees/{employee_id}/versions")
def list_digital_employee_versions(
    employee_id: str,
    identity: Identity,
    service: Catalog,
    status: Annotated[LifecycleStatus | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query(max_length=1024)] = None,
) -> dict[str, object]:
    """按员工版本号倒序返回租户隔离的版本清单。"""

    page = service.list_employee_versions_page(
        identity, employee_id, status=status, limit=limit, cursor=cursor
    )
    return {
        "items": [_version_response(version) for version in page.items],
        "next_cursor": page.next_cursor,
    }


@router.get("/digital-employees/{employee_id}/versions/{version}")
def get_digital_employee_version(
    employee_id: str, version: int, identity: Identity, service: Catalog
) -> dict[str, object]:
    """返回员工不可变版本的执行配置和精确 Skill/Tool 绑定。"""

    return _version_response(service.get_employee_version(identity, employee_id, version))


@router.put("/digital-employees/{employee_id}/versions/{version}")
def update_digital_employee_version(
    employee_id: str,
    version: int,
    request: EmployeeVersionUpdateRequest,
    identity: Identity,
    service: Catalog,
) -> dict[str, object]:
    """原子更新指定 DRAFT 版本；精确 Skill/Tool 版本绑定一并保存。"""

    _check_tenant(identity, request)
    updated = service.update_employee_version(
        identity,
        employee_id,
        version,
        system_prompt=request.system_prompt,
        skill_version_refs=tuple(
            (item.resource_id, item.version) for item in request.skill_version_refs
        ),
        tool_version_refs=tuple(
            (item.resource_id, item.version) for item in request.tool_version_refs
        ),
        execution_profile_id=request.execution_profile_id,
        execution_profile_version=request.execution_profile_version,
        model_route_id=request.model_route_id,
        workspace_profile_id=request.workspace_profile_id,
        workspace_profile_version=request.workspace_profile_version,
        sandbox_profile_id=request.sandbox_profile_id,
        sandbox_profile_version=request.sandbox_profile_version,
        approval_policy=request.approval_policy,
    )
    return _version_response(updated)


@router.post("/digital-employees/{employee_id}/versions/{version}/test")
def test_digital_employee_version(
    employee_id: str,
    version: int,
    request: EmployeeVersionTestRequest,
    identity: Identity,
    service: ControlPlane,
) -> dict[str, object]:
    """用当前 Runtime 预览 DRAFT 版本，不创建正式 Deployment 或 Run。"""

    _check_tenant(identity, request)
    result = service.test_employee_version(identity, employee_id, version, request.prompt)
    return {
        "status": result.status.value,
        "result": result.text,
        "usage": {
            "input_tokens": result.usage.input_tokens,
            "output_tokens": result.usage.output_tokens,
            "total_tokens": result.usage.total_tokens,
            "source": result.usage.source,
            "estimated": result.usage.estimated,
        },
        "events": [
            {"event_type": event.event_type, "span_type": event.span_type}
            for event in result.events
        ],
    }


@router.post("/tools", status_code=201)
def create_tool(request: ToolCreateRequest, identity: Identity, service: Catalog) -> dict[str, object]:
    _check_tenant(identity, request)
    return _tool_response(
        service.create_tool(
            identity,
            request.name,
            request.description,
            request.input_schema,
            protocol=request.protocol,
            requires_recipient_credentials=request.requires_recipient_credentials,
            auth_secret_ref=request.auth_secret_ref,
        )
    )


@router.get("/tools")
def list_tools(identity: Identity, service: Catalog) -> dict[str, object]:
    return {"items": [_tool_response(item) for item in service.list_tools(identity)]}


@router.patch("/tools/{tool_id}")
def update_tool(
    tool_id: str, request: ToolUpdateRequest, identity: Identity, service: Catalog
) -> dict[str, object]:
    """部分更新当前租户 Tool DRAFT；已发布版本保持不可变。"""

    _check_tenant(identity, request)
    if request.name is None and request.description is None and request.input_schema is None:
        raise DomainError("INVALID_ARGUMENT", "PATCH 至少提供一个 Tool 字段")
    return _tool_response(
        service.update_tool(
            identity,
            tool_id,
            name=request.name,
            description=request.description,
            input_schema=request.input_schema,
        )
    )


@router.post("/tools/{tool_id}/publish")
def publish_tool(tool_id: str, identity: Identity, service: Catalog) -> dict[str, object]:
    return _tool_response(service.publish_tool(identity, tool_id))


@router.post("/skills", status_code=201)
def create_skill(request: SkillCreateRequest, identity: Identity, service: Catalog) -> dict[str, object]:
    _check_tenant(identity, request)
    return _skill_response(
        service.create_skill(
            identity,
            request.name,
            request.skill_type,
            request.instruction,
            request.allowed_tool_ids,
        )
    )


@router.get("/skills")
def list_skills(identity: Identity, service: Catalog) -> dict[str, object]:
    return {"items": [_skill_response(item) for item in service.list_skills(identity)]}


@router.post("/skills/{skill_id}/publish")
def publish_skill(skill_id: str, identity: Identity, service: Catalog) -> dict[str, object]:
    return _skill_response(service.publish_skill(identity, skill_id))


@router.post("/digital-employees", status_code=201)
def create_employee(
    request: EmployeeCreateRequest, identity: Identity, service: Catalog
) -> dict[str, object]:
    _check_tenant(identity, request)
    return _employee_response(service.create_employee(identity, request.name, request.role_name, request.description))


@router.post("/digital-employees/{employee_id}/versions", status_code=201)
def create_employee_version(
    employee_id: str,
    request: EmployeeVersionCreateRequest,
    identity: Identity,
    service: Catalog,
) -> dict[str, object]:
    _check_tenant(identity, request)
    return _version_response(
        service.create_employee_version(
            identity,
            employee_id,
            request.system_prompt,
            request.skill_ids,
            request.tool_ids,
            request.execution_profile_id,
            request.model_route_id,
            request.workspace_profile_id,
            request.sandbox_profile_id,
            request.approval_policy,
            request.workspace_profile_version,
            request.sandbox_profile_version,
        )
    )


@router.post("/digital-employees/{employee_id}/versions/{version}/publish")
def publish_employee_version(
    employee_id: str, version: int, identity: Identity, service: Catalog
) -> dict[str, object]:
    return _version_response(service.publish_employee_version(identity, employee_id, version))


@router.post("/deployments", status_code=201)
def create_deployment(
    request: DeploymentCreateRequest, identity: Identity, service: Catalog
) -> dict[str, object]:
    _check_tenant(identity, request)
    return _deployment_response(service.deploy(identity, request.employee_id, request.version))


@router.get("/deployments")
def list_deployments(
    identity: Identity,
    service: Catalog,
    employee_id: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
    status: Annotated[LifecycleStatus | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query(max_length=1024)] = None,
) -> dict[str, object]:
    """按当前租户和可选员工/状态过滤部署，附带固定员工版本摘要。"""

    page = service.list_deployments_page(
        identity,
        employee_id=employee_id,
        status=status,
        limit=limit,
        cursor=cursor,
    )
    return {"items": [_deployment_response(item) for item in page.items], "next_cursor": page.next_cursor}


@router.get("/audit-events")
def list_audit_events(
    identity: Identity,
    service: Catalog,
    resource_type: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
    resource_id: Annotated[str | None, Query(min_length=1, max_length=26)] = None,
    event_type: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query(max_length=1024)] = None,
) -> dict[str, object]:
    """租户管理员查询审计事件；资源过滤和分页始终附加当前租户范围。"""

    page = service.list_audit_events_page(
        identity,
        resource_type=resource_type,
        resource_id=resource_id,
        event_type=event_type,
        limit=limit,
        cursor=cursor,
    )
    return {
        "items": [
            {
                "event_id": item.event_id,
                "actor_type": item.actor_type,
                "actor_user_id": item.actor_user_id,
                "event_type": item.event_type,
                "resource_type": item.resource_type,
                "resource_id": item.resource_id,
                "payload": dict(item.payload),
                "created_at": item.created_at.isoformat(),
            }
            for item in page.items
        ],
        "next_cursor": page.next_cursor,
    }


@router.post("/deployments/{deployment_id}/rollback")
def rollback_deployment(deployment_id: str, request: DeploymentRollbackRequest, identity: Identity, service: Catalog) -> dict[str, object]:
    _check_tenant(identity, request)
    return _deployment_response(service.rollback(identity, deployment_id, request.employee_id, request.version))


@router.post("/sessions", status_code=201)
def create_session(
    request: SessionCreateRequest, identity: Identity, service: ControlPlane
) -> dict[str, object]:
    _check_tenant(identity, request)
    return _session_response(service.create_session(identity, request.deployment_id))


@router.post("/sessions/{session_id}/runs", status_code=201)
def create_run(
    session_id: str,
    request: RunCreateRequest,
    identity: Identity,
    service: ControlPlane,
) -> dict[str, object]:
    _check_tenant(identity, request)
    return _run_response(
        service.create_run(identity, session_id, request.prompt, request.idempotency_key)
    )


@router.get("/runs/{run_id}")
def get_run(run_id: str, identity: Identity, service: ControlPlane) -> dict[str, object]:
    return _run_response(service.get_run(identity, run_id))


@router.get("/runs/{run_id}/trace")
def get_run_trace(run_id: str, identity: Identity, service: ControlPlane) -> dict[str, object]:
    return _timeline_response(service.get_trace(identity, run_id))


@router.get("/sessions/{session_id}/trace")
def get_session_trace(session_id: str, identity: Identity, service: ControlPlane) -> dict[str, object]:
    spans = service.get_session_trace(identity, session_id)
    return {"session_id": session_id, "spans": [_span_response(span) for span in spans]}


@router.post("/execution-profiles", status_code=201)
def create_execution_profile(request: ExecutionProfileCreateRequest, identity: Identity, service: Catalog) -> dict[str, object]:
    _check_tenant(identity, request)
    profile = service.create_profile(
        identity,
        request.engine_name,
        request.model_alias,
        model_route_id=request.model_route_id,
        workspace_profile_id=request.workspace_profile_id,
        workspace_profile_version=request.workspace_profile_version,
        sandbox_profile_id=request.sandbox_profile_id,
        sandbox_profile_version=request.sandbox_profile_version,
        approval_policy=request.approval_policy,
    )
    return {
        "id": profile.profile_id,
        "tenant_id": profile.tenant_id,
        "version": profile.version,
        "engine_name": profile.engine_name,
        "model_alias": profile.model_alias,
        "tool_policy": list(profile.tool_policy),
        "context_policy": profile.context_policy,
        "model_route_id": profile.model_route_id,
        "workspace_profile_id": profile.workspace_profile_id,
        "workspace_profile_version": profile.workspace_profile_version,
        "sandbox_profile_id": profile.sandbox_profile_id,
        "sandbox_profile_version": profile.sandbox_profile_version,
        "approval_policy": profile.approval_policy,
        "status": profile.status.value,
    }


@router.get("/execution-profiles")
def list_execution_profiles(identity: Identity, service: Catalog) -> dict[str, object]:
    return {
        "items": [
            {
                "id": profile.profile_id,
                "tenant_id": profile.tenant_id,
                "version": profile.version,
                "engine_name": profile.engine_name,
                "model_alias": profile.model_alias,
                "tool_policy": list(profile.tool_policy),
                "context_policy": profile.context_policy,
                "model_route_id": profile.model_route_id,
                "workspace_profile_id": profile.workspace_profile_id,
                "workspace_profile_version": profile.workspace_profile_version,
                "sandbox_profile_id": profile.sandbox_profile_id,
                "sandbox_profile_version": profile.sandbox_profile_version,
                "approval_policy": profile.approval_policy,
                "status": profile.status.value,
            }
            for profile in service.list_profiles(identity)
        ]
    }


@router.post("/model-routes", status_code=201)
def create_model_route(request: ModelRouteCreateRequest, identity: Identity, service: ModelRoutes) -> dict[str, object]:
    _check_tenant(identity, request)
    return service.create(identity, request.alias, request.model_name, request.provider, request.endpoint, request.secret_id).to_public_dict()


@router.get("/model-routes")
def list_model_routes(identity: Identity, service: ModelRoutes) -> dict[str, object]:
    return {"items": [item.to_public_dict() for item in service.list(identity)]}


def _hub_version_response(resource: HubResource, version: HubResourceVersion) -> dict[str, object]:
    return {
        "hub_resource_id": resource.hub_resource_id,
        "resource_type": resource.resource_type.value,
        "name": resource.name,
        "visibility": resource.visibility.value,
        "version": version.version,
        "status": version.status.value,
        "manifest_sha256": version.manifest.sha256,
        "dependency_count": len(version.manifest.data.get("dependencies", [])),
    }


def _hub_summary_response(value: object) -> dict[str, object]:
    return {
        "hub_resource_id": value.hub_resource_id,
        "resource_type": value.resource_type.value,
        "name": value.name,
        "description": value.description,
        "publisher_name": value.publisher_name,
        "visibility": value.visibility.value,
        "latest_version": value.latest_version,
        "available_modes": [mode.value for mode in value.available_modes],
        "is_publisher": value.is_publisher,
    }


@router.get("/hub/resources")
def list_hub_resources(
    identity: Identity,
    service: ResourceCenter,
    resource_type: Annotated[HubResourceType | None, Query()] = None,
    keyword: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
    mode: Annotated[HubImportMode | None, Query()] = None,
    scope: Annotated[Literal["DISCOVERABLE", "OWNED"], Query()] = "DISCOVERABLE",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, object]:
    """浏览平台可发现的脱敏目录元数据；模式筛选只显示当前租户已获授权的导入能力。"""

    return {
        "items": [
            _hub_summary_response(item)
            for item in service.list_discoverable(
                identity,
                resource_type=resource_type.value if resource_type else None,
                keyword=keyword,
                mode=mode,
                owned_only=scope == "OWNED",
                limit=limit,
            )
        ]
    }


@router.get("/hub/resources/{hub_resource_id}/versions/{version}")
def get_hub_resource_version_detail(
    hub_resource_id: str, version: int, identity: Identity, service: ResourceCenter
) -> dict[str, object]:
    """返回脱敏版本详情/摘要，不返回 Package Manifest 或源租户内部标识。"""

    value = service.get_discoverable_version(identity, hub_resource_id, version)
    return {
        "hub_resource_id": value.hub_resource_id,
        "resource_type": value.resource_type.value,
        "name": value.name,
        "description": value.description,
        "publisher_name": value.publisher_name,
        "visibility": value.visibility.value,
        "version": value.version,
        "manifest_sha256": value.manifest_sha256,
        "dependency_count": value.dependency_count,
        "available_modes": [mode.value for mode in value.available_modes],
    }


@router.post("/hub/resources", status_code=201)
def create_hub_resource_package(
    request: HubPackageCreateRequest, identity: Identity, service: ResourceCenter
) -> dict[str, object]:
    """将本租户已发布的 Employee/Skill/Tool 精确版本打成 Hub DRAFT Package。"""

    _check_tenant(identity, request)
    resource, version = service.create_package(
        identity,
        request.resource_type,
        request.source_resource_id,
        request.source_resource_version,
        visibility=request.visibility,
    )
    return _hub_version_response(resource, version)


@router.post("/hub/resources/{hub_resource_id}/versions", status_code=201)
def create_hub_resource_version(
    hub_resource_id: str,
    request: HubPackageVersionCreateRequest,
    identity: Identity,
    service: ResourceCenter,
) -> dict[str, object]:
    """给当前租户已有 Hub 条目追加新源版本，不覆盖旧 Package。"""

    _check_tenant(identity, request)
    resource, version = service.create_package(
        identity,
        service.repository.get_owned_resource(identity, hub_resource_id).resource_type,
        request.source_resource_id,
        request.source_resource_version,
        hub_resource_id=hub_resource_id,
    )
    return _hub_version_response(resource, version)


@router.post("/hub/resources/{hub_resource_id}/versions/{version}/publish")
def publish_hub_resource_version(
    hub_resource_id: str, version: int, identity: Identity, service: ResourceCenter
) -> dict[str, object]:
    """发布当前租户的 Hub Package 版本，返回固定摘要供后续授权。"""

    published = service.publish_version(identity, hub_resource_id, version)
    resource = service.repository.get_owned_resource(identity, hub_resource_id)
    return _hub_version_response(resource, published)


def _hub_grant_response(grant: object) -> dict[str, object]:
    return {
        "grant_id": grant.grant_id,
        "hub_resource_id": grant.hub_resource_id,
        "hub_version": grant.hub_version,
        "recipient_tenant_id": grant.recipient_tenant_id,
        "copy_allowed": grant.copy_allowed,
        "reference_allowed": grant.reference_allowed,
        "expires_at": grant.expires_at.isoformat() if grant.expires_at else None,
        "status": grant.status.value,
        "created_by": grant.created_by,
    }


@router.post("/hub/resources/{hub_resource_id}/versions/{version}/grants", status_code=201)
def create_hub_grant(
    hub_resource_id: str,
    version: int,
    request: HubGrantCreateRequest,
    identity: Identity,
    service: ResourceCenter,
) -> dict[str, object]:
    """配置精确 Hub 版本的 COPY/REFERENCE Grant。"""

    _check_tenant(identity, request)
    grant = service.create_grant(
        identity,
        hub_resource_id,
        version,
        request.recipient_tenant_id,
        copy_allowed=request.copy_allowed,
        reference_allowed=request.reference_allowed,
        expires_at=request.expires_at,
    )
    return _hub_grant_response(grant)


@router.get("/hub/resources/{hub_resource_id}/versions/{version}/grants")
def list_hub_grants(
    hub_resource_id: str, version: int, identity: Identity, service: ResourceCenter
) -> dict[str, object]:
    """仅发布租户管理员可查看该精确版本的接收租户授权。"""

    return {
        "items": [
            _hub_grant_response(item)
            for item in service.list_grants(identity, hub_resource_id, version)
        ]
    }


@router.get("/hub/resources/{hub_resource_id}/versions/{version}/manifest")
def get_authorized_hub_manifest(
    hub_resource_id: str,
    version: int,
    identity: Identity,
    service: ResourceCenter,
    mode: Annotated[HubImportMode, Query()],
) -> dict[str, object]:
    """先按接收方 Grant 校验模式，再返回固定 Manifest 以展示已授权依赖。"""

    package = service.get_authorized_manifest(identity, hub_resource_id, version, mode)
    return {
        "hub_resource_id": package.hub_resource_id,
        "version": package.version,
        "manifest_sha256": package.manifest.sha256,
        "manifest": package.manifest.data,
    }


@router.post("/hub/imports", status_code=201)
def import_hub_resource(
    request: HubResourceImportRequest, identity: Identity, service: ResourceCenter
) -> dict[str, object]:
    """按有效 Grant 将精确 Hub 版本导入为本地 COPY 副本或 REFERENCE 代理。"""

    _check_tenant(identity, request)
    imported = service.import_resource(
        identity,
        request.hub_resource_id,
        request.hub_version,
        request.mode,
    )
    return {
        "import_id": imported.import_id,
        "hub_resource_id": imported.hub_resource_id,
        "hub_version": imported.hub_version,
        "mode": imported.mode.value,
        "local_resource_type": imported.local_resource_type.value,
        "local_resource_id": imported.local_resource_id,
        "local_resource_version": imported.local_resource_version,
        "manifest_sha256": imported.manifest_sha256,
        "status": imported.status.value,
    }


def _hub_import_response(imported: object) -> dict[str, object]:
    return {
        "import_id": imported.import_id,
        "hub_resource_id": imported.hub_resource_id,
        "hub_version": imported.hub_version,
        "mode": imported.mode.value,
        "local_resource_type": imported.local_resource_type.value,
        "local_resource_id": imported.local_resource_id,
        "local_resource_version": imported.local_resource_version,
        "manifest_sha256": imported.manifest_sha256,
        "status": imported.status.value,
    }


@router.get("/hub/imports")
def list_hub_imports(
    identity: Identity,
    service: ResourceCenter,
    status: Annotated[TenantImportStatus | None, Query()] = None,
    resource_type: Annotated[HubResourceType | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, object]:
    """列出当前租户的导入状态；不返回发布租户内部 ID 或 Package 正文。"""

    return {
        "items": [
            _hub_import_response(item)
            for item in service.list_imports(
                identity,
                status=status,
                resource_type=resource_type,
                limit=limit,
            )
        ]
    }


@router.post("/hub/imports/{import_id}/upgrade", status_code=201)
def upgrade_hub_import(
    import_id: str,
    request: HubImportUpgradeRequest,
    identity: Identity,
    service: ResourceCenter,
) -> dict[str, object]:
    """显式导入同一 Hub 条目的更高版本，返回新代理而不改写旧绑定。"""

    _check_tenant(identity, request)
    return _hub_import_response(service.upgrade_import(identity, import_id, request.hub_version))


@router.delete("/hub/imports/{import_id}")
def revoke_hub_import(
    import_id: str, identity: Identity, service: ResourceCenter
) -> dict[str, object]:
    """撤销当前租户 REFERENCE 映射；保留历史代理与已完成 Run 快照。"""

    return _hub_import_response(service.revoke_import(identity, import_id))


@router.delete("/hub/grants/{grant_id}")
def revoke_hub_grant(
    grant_id: str, identity: Identity, service: ResourceCenter
) -> dict[str, str]:
    """发布租户管理员撤销精确版本 Grant，后续 Reference Run 会重新授权失败。"""

    service.revoke_grant(identity, grant_id)
    return {"grant_id": grant_id, "status": "REVOKED"}


def _skill_zip_preview_response(value: SkillZipPreview) -> dict[str, object]:
    return {
        "staging_id": value.staging_id,
        "skill_id": value.skill_id,
        "version": value.version,
        "strategy": value.strategy,
        "archive_sha256": value.archive_sha256,
        "base_manifest_sha256": value.base_manifest_sha256,
        "manifest_sha256": value.manifest_sha256,
        "entry_path": value.entry_path,
        "file_count": value.file_count,
        "expires_in_seconds": value.expires_in_seconds,
        "files": [
            {
                "path": item.file_path,
                "mime_type": item.mime_type,
                "size_bytes": item.size_bytes,
                "sha256": item.content_sha256,
            }
            for item in value.files
        ],
        "diff": {
            "added": list(value.added_paths),
            "updated": list(value.updated_paths),
            "removed": list(value.removed_paths),
        },
    }


def _skill_file_version_response(skill: SkillVersion) -> dict[str, object]:
    return {
        "skill_id": skill.skill_id,
        "version": skill.version,
        "content_format": skill.content_format.value,
        "entry_path": skill.entry_path,
        "file_manifest_sha256": skill.file_manifest_sha256,
        "status": skill.status.value,
        "read_only": skill.status.value != "DRAFT",
        "files": [],
    }


def _file_bytes_from_request(request: SkillFileCreateRequest | SkillFileUpdateRequest) -> bytes:
    if (request.content_text is None) == (request.content_base64 is None):
        raise DomainError("INVALID_ARGUMENT", "content_text 与 content_base64 必须且只能提供一个")
    if request.content_text is not None:
        return request.content_text.encode("utf-8")
    try:
        return base64.b64decode(request.content_base64, validate=True)
    except (binascii.Error, ValueError) as error:
        raise DomainError("INVALID_ARGUMENT", "content_base64 格式非法") from error


def _skill_file_change_response(skill: SkillVersion) -> dict[str, object]:
    return {
        "skill_id": skill.skill_id,
        "version": skill.version,
        "content_format": skill.content_format.value,
        "entry_path": skill.entry_path,
        "file_manifest_sha256": skill.file_manifest_sha256,
        "file_count": len(skill.file_entries),
        "status": skill.status.value,
    }


async def _read_limited_skill_zip(request: Request, max_bytes: int) -> bytes:
    """逐块接收 ZIP，避免在校验压缩包上限前一次性读取任意大请求体。"""

    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > max_bytes:
            raise DomainError("INVALID_ARGUMENT", "ZIP 压缩包超过大小限制", {"max_bytes": max_bytes})
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/skills/{skill_id}/versions/{version}/files/zip/preview")
async def preview_skill_zip(
    skill_id: str,
    version: int,
    request: Request,
    identity: Identity,
    service: SkillFiles,
    strategy: Annotated[str, Query(pattern="^(replace|merge)$")] = "replace",
) -> dict[str, object]:
    """预览 ZIP 文件树与 replace/merge 差异；请求体为 application/zip 原始字节。"""

    content_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
    if content_type not in {"application/zip", "application/x-zip-compressed", "application/octet-stream"}:
        raise DomainError("INVALID_ARGUMENT", "ZIP 请求 Content-Type 必须是 application/zip")
    archive = await _read_limited_skill_zip(request, service.max_archive_bytes)
    return _skill_zip_preview_response(
        service.preview_zip(identity, skill_id, version, archive, strategy=strategy)
    )


@router.post("/skills/{skill_id}/versions/{version}/files/zip/commit/{staging_id}")
async def commit_skill_zip(
    skill_id: str,
    version: int,
    staging_id: str,
    request: Request,
    identity: Identity,
    service: SkillFiles,
) -> dict[str, object]:
    """重新上传预览过的同一 ZIP，再以完整清单事务写入 Skill DRAFT。"""

    content_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
    if content_type not in {"application/zip", "application/x-zip-compressed", "application/octet-stream"}:
        raise DomainError("INVALID_ARGUMENT", "ZIP 请求 Content-Type 必须是 application/zip")
    archive = await _read_limited_skill_zip(request, service.max_archive_bytes)
    skill = service.commit_zip(identity, skill_id, version, staging_id, archive)
    return {
        "skill_id": skill.skill_id,
        "version": skill.version,
        "content_format": skill.content_format.value,
        "entry_path": skill.entry_path,
        "file_manifest_sha256": skill.file_manifest_sha256,
        "file_count": len(skill.file_entries),
        "status": skill.status.value,
    }


@router.get("/skills/{skill_id}/versions/{version}/files")
def list_skill_files(
    skill_id: str, version: int, identity: Identity, service: SkillFiles
) -> dict[str, object]:
    """返回精确 Skill 版本的文件树和摘要；PUBLISHED/REFERENCE 只读。"""

    skill = service.get_skill_version(identity, skill_id, version)
    response = _skill_file_version_response(skill)
    response["files"] = [
        {
            "path": item.file_path,
            "mime_type": item.mime_type,
            "size_bytes": item.size_bytes,
            "sha256": item.content_sha256,
            "virtual": skill.content_format.value == "INLINE",
        }
        for item in service.list_files(identity, skill_id, version)
    ]
    return response


@router.get("/skills/{skill_id}/versions/{version}/files/content")
def read_skill_file(
    skill_id: str,
    version: int,
    identity: Identity,
    service: SkillFiles,
    file_path: Annotated[str, Query(min_length=1, max_length=512)],
) -> dict[str, object]:
    """返回 UTF-8 文件正文或 Base64 二进制正文，不暴露对象引用。"""

    content = service.read_file(identity, skill_id, version, file_path)
    item = next(
        (item for item in service.list_files(identity, skill_id, version) if item.file_path == file_path),
        None,
    )
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        text = None
    return {
        "path": file_path,
        "mime_type": item.mime_type if item is not None else "text/markdown; charset=utf-8",
        "size_bytes": len(content),
        "sha256": item.content_sha256 if item is not None else None,
        "content_text": text,
        "content_base64": None if text is not None else base64.b64encode(content).decode("ascii"),
    }


@router.post("/skills/{skill_id}/versions/{version}/files", status_code=201)
def create_skill_file(
    skill_id: str,
    version: int,
    request: SkillFileCreateRequest,
    identity: Identity,
    service: SkillFiles,
) -> dict[str, object]:
    """在 DRAFT Skill 创建文件；INLINE 转换必须先创建根 SKILL.md。"""

    _check_tenant(identity, request)
    skill = service.create_file(
        identity,
        skill_id,
        version,
        request.file_path,
        _file_bytes_from_request(request),
        mime_type=request.mime_type,
    )
    return _skill_file_change_response(skill)


@router.put("/skills/{skill_id}/versions/{version}/files")
def update_skill_file(
    skill_id: str,
    version: int,
    request: SkillFileUpdateRequest,
    identity: Identity,
    service: SkillFiles,
) -> dict[str, object]:
    """按 expected_sha256 更新一个 DRAFT 文件或兼容 INLINE 根指令。"""

    _check_tenant(identity, request)
    skill = service.update_file(
        identity,
        skill_id,
        version,
        request.file_path,
        _file_bytes_from_request(request),
        expected_sha256=request.expected_sha256,
        mime_type=request.mime_type,
    )
    return _skill_file_change_response(skill)


@router.post("/skills/{skill_id}/versions/{version}/files/rename")
def rename_skill_file(
    skill_id: str,
    version: int,
    request: SkillFileRenameRequest,
    identity: Identity,
    service: SkillFiles,
) -> dict[str, object]:
    """按文件摘要乐观并发重命名 DRAFT 辅助文件。"""

    _check_tenant(identity, request)
    skill = service.rename_file(
        identity,
        skill_id,
        version,
        request.file_path,
        request.new_file_path,
        expected_sha256=request.expected_sha256,
    )
    return _skill_file_change_response(skill)


@router.delete("/skills/{skill_id}/versions/{version}/files")
def delete_skill_file(
    skill_id: str,
    version: int,
    identity: Identity,
    service: SkillFiles,
    file_path: Annotated[str, Query(min_length=1, max_length=512)],
    expected_sha256: Annotated[str, Query(min_length=64, max_length=64)],
) -> dict[str, object]:
    """按 expected_sha256 删除 DRAFT 辅助文件；根入口不可删除。"""

    skill = service.delete_file(
        identity,
        skill_id,
        version,
        file_path,
        expected_sha256=expected_sha256,
    )
    return _skill_file_change_response(skill)


@router.post("/conversations", status_code=201)
def create_conversation(request: ConversationCreateRequest, identity: Identity, service: Conversations) -> dict[str, object]:
    _check_tenant(identity, request)
    conversation = service.create_conversation(identity, request.title, is_group=request.is_group)
    return {"id": conversation.conversation_id, "title": conversation.title, "is_group": conversation.is_group, "status": conversation.status}


@router.post("/conversations/{conversation_id}/bindings", status_code=201)
def bind_conversation_agent(conversation_id: str, request: AgentBindingRequest, identity: Identity, service: Conversations) -> dict[str, object]:
    _check_tenant(identity, request)
    binding = service.bind_agent(identity, conversation_id, request.employee_id, request.employee_name, request.session_id)
    return {"employee_id": binding.employee_id, "employee_name": binding.employee_name, "session_id": binding.session_id, "status": binding.status}


@router.post("/conversations/{conversation_id}/messages", status_code=201)
def ingest_conversation_message(conversation_id: str, request: MessageCreateRequest, identity: Identity, service: Conversations) -> dict[str, object]:
    _check_tenant(identity, request)
    message = service.ingest(identity, conversation_id, request.sender_id, request.text, source=request.source, routing_depth=request.routing_depth)
    return {"id": message.message_id, "conversation_id": message.conversation_id, "source": message.source.value, "routing_depth": message.routing_depth}


@router.get("/conversations/{conversation_id}/messages")
def list_conversation_messages(
    conversation_id: str, identity: Identity, service: Conversations
) -> dict[str, object]:
    """按租户恢复消息正文；持久仓储只保存正文对象引用。"""

    return {
        "items": [
            {
                "id": message.message_id,
                "conversation_id": message.conversation_id,
                "sender_id": message.sender_id,
                "source": message.source.value,
                "text": message.text,
                "routing_depth": message.routing_depth,
            }
            for message in service.list_messages(identity, conversation_id)
        ]
    }


@router.post("/conversations/{conversation_id}/messages/{message_id}/route")
def route_conversation_message(conversation_id: str, message_id: str, identity: Identity, service: Conversations) -> dict[str, object]:
    sessions = service.route(identity, message_id)
    return {"message_id": message_id, "conversation_id": conversation_id, "session_ids": list(sessions)}


@router.post("/conversations/{conversation_id}/deliveries", status_code=201)
def create_delivery(conversation_id: str, request: DeliveryCreateRequest, identity: Identity, service: Conversations) -> dict[str, object]:
    _check_tenant(identity, request)
    delivery = service.deliver(identity, conversation_id, request.body, request.idempotency_key)
    return {"id": delivery.delivery_id, "status": delivery.status.value, "retry_count": delivery.retry_count}


@router.get("/runs/{run_id}/usage")
def get_run_usage(run_id: str, identity: Identity, service: ControlPlane) -> dict[str, object]:
    timeline = service.get_trace(identity, run_id)
    return {"run_id": run_id, "usage": {"input_tokens": timeline.usage.input_tokens, "output_tokens": timeline.usage.output_tokens, "total_tokens": timeline.usage.total_tokens, "source": timeline.usage.source, "estimated": timeline.usage.estimated, "cost_amount": timeline.usage.cost_amount, "currency": timeline.usage.currency, "pricing_version": timeline.usage.pricing_version}}
