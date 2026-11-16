"""数字员工、Skill、Tool 和执行配置 Catalog API。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request

from agent_platform.api.schemas import (
    EmployeeCreateRequest,
    EmployeeSkillBindingsUpdateRequest,
    EmployeeToolBindingsUpdateRequest,
    EmployeeUpdateRequest,
    EmployeeVersionCreateRequest,
    EmployeeVersionTestRequest,
    EmployeeVersionUpdateRequest,
    ExecutionProfileCreateRequest,
    ModelRouteCreateRequest,
    SkillCreateRequest,
    ToolCreateRequest,
    ToolUpdateRequest,
)
from agent_platform.domain.agents import FakeDigitalEmployeeRepository
from agent_platform.domain.catalog import LifecycleStatus
from agent_platform.domain.common import DomainError

from .common import Catalog, ControlPlane, Identity, ModelRoutes, _check_tenant
from .serializers import _employee_response, _skill_response, _tool_response, _version_response

router = APIRouter()

# 旧 Gallery 列表仅用于兼容遗留路径；新管理台从 Catalog API 查询员工。
employee_repository = FakeDigitalEmployeeRepository()

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


@router.post("/digital-employees/{employee_id}/versions/{source_version}/fork", status_code=201)
def fork_digital_employee_version(
    employee_id: str, source_version: int, identity: Identity, service: Catalog
) -> dict[str, object]:
    """基于固定的已发布或已下线版本创建完整继承配置的新员工草稿。"""

    return _version_response(service.fork_employee_version(identity, employee_id, source_version))


@router.post("/digital-employees/{employee_id}/versions/{version}/deprecate")
def deprecate_digital_employee_version(
    employee_id: str, version: int, identity: Identity, service: Catalog
) -> dict[str, object]:
    """下线指定已发布版本；重复请求返回已有下线状态。"""

    return _version_response(service.deprecate_employee_version(identity, employee_id, version))


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


@router.put("/digital-employees/{employee_id}/versions/{version}/skill-bindings")
def replace_digital_employee_skill_bindings(
    employee_id: str,
    version: int,
    request: EmployeeSkillBindingsUpdateRequest,
    identity: Identity,
    service: Catalog,
) -> dict[str, object]:
    """只替换指定员工 DRAFT 的 Skill 绑定，不保存其他版本配置。"""

    _check_tenant(identity, request)
    updated = service.replace_employee_skill_bindings(
        identity,
        employee_id,
        version,
        tuple((item.resource_id, item.version) for item in request.skill_version_refs),
    )
    return _version_response(updated)


@router.put("/digital-employees/{employee_id}/versions/{version}/tool-bindings")
def replace_digital_employee_tool_bindings(
    employee_id: str,
    version: int,
    request: EmployeeToolBindingsUpdateRequest,
    identity: Identity,
    service: Catalog,
) -> dict[str, object]:
    """只替换指定员工 DRAFT 的 Tool 绑定，不保存其他版本配置。"""

    _check_tenant(identity, request)
    updated = service.replace_employee_tool_bindings(
        identity,
        employee_id,
        version,
        tuple((item.resource_id, item.version) for item in request.tool_version_refs),
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
def list_skills(
    identity: Identity,
    service: Catalog,
    include_versions: bool = False,
) -> dict[str, object]:
    """默认返回每个 Skill 的最新版本；绑定选择器可读取精确版本历史。"""

    values = service.list_all_skill_versions(identity) if include_versions else service.list_skills(identity)
    return {"items": [_skill_response(item) for item in values]}


@router.post("/skills/{skill_id}/publish")
def publish_skill(skill_id: str, identity: Identity, service: Catalog) -> dict[str, object]:
    return _skill_response(service.publish_skill(identity, skill_id))


@router.post("/skills/{skill_id}/versions/{version}/fork", status_code=201)
def fork_skill_version(
    skill_id: str, version: int, identity: Identity, service: Catalog
) -> dict[str, object]:
    """基于本租户 PUBLISHED Skill 创建新版本；REFERENCE 必须先 COPY。"""

    return _skill_response(service.fork_skill_version(identity, skill_id, version))


@router.post("/skills/{skill_id}/versions/{version}/publish")
def publish_skill_version(
    skill_id: str, version: int, identity: Identity, service: Catalog
) -> dict[str, object]:
    """校验并发布指定 Skill DRAFT 版本。"""

    return _skill_response(service.publish_skill_version(identity, skill_id, version))


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
