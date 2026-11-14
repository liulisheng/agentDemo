from typing import Annotated

from fastapi import APIRouter, Depends

from agent_platform.api.dependencies import (
    catalog_service,
    control_plane_service,
    current_identity,
)
from agent_platform.api.schemas import (
    DeploymentCreateRequest,
    EmployeeCreateRequest,
    EmployeeVersionCreateRequest,
    RunCreateRequest,
    SessionCreateRequest,
    SkillCreateRequest,
    TenantAwareRequest,
    ToolCreateRequest,
)
from agent_platform.application.catalog_service import CatalogService
from agent_platform.application.control_plane import ControlPlaneService
from agent_platform.domain.agents import FakeDigitalEmployeeRepository
from agent_platform.domain.catalog import (
    Deployment,
    DigitalEmployee,
    DigitalEmployeeVersion,
    SkillVersion,
    ToolVersion,
)
from agent_platform.domain.common import DomainError
from agent_platform.domain.identity import IdentityContext
from agent_platform.domain.observability import RunRecord, RunTimeline, SessionRecord, TraceSpan

router = APIRouter(prefix="/api/v1")

# 旧 Gallery 契约保留本地目录；新建资源通过 CatalogService 进入最小闭环。
employee_repository = FakeDigitalEmployeeRepository()

Identity = Annotated[IdentityContext, Depends(current_identity)]
Catalog = Annotated[CatalogService, Depends(catalog_service)]
ControlPlane = Annotated[ControlPlaneService, Depends(control_plane_service)]


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
    }


def _version_response(value: DigitalEmployeeVersion) -> dict[str, object]:
    return {
        "employee_id": value.employee_id,
        "tenant_id": value.tenant_id,
        "version": value.version,
        "system_prompt": value.system_prompt,
        "skill_ids": list(value.skill_version_ids),
        "tool_ids": list(value.tool_version_ids),
        "execution_profile_id": value.execution_profile_id,
        "status": value.status.value,
    }


def _deployment_response(value: Deployment) -> dict[str, object]:
    return {
        "id": value.deployment_id,
        "tenant_id": value.tenant_id,
        "employee_id": value.employee_id,
        "version": value.employee_version.version,
        "status": value.status.value,
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
        "error_category": value.error_category,
        "snapshot": {
            "employee_id": value.snapshot.employee_id,
            "employee_version": value.snapshot.employee_version,
            "skill_ids": list(value.snapshot.skill_version_ids),
            "tool_ids": list(value.snapshot.tool_version_ids),
            "execution_profile_id": value.snapshot.execution_profile_id,
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
def list_digital_employees(identity: Identity) -> dict[str, object]:
    """返回当前租户可在 Gallery 使用的数字员工摘要。"""

    return {
        "items": [
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
    }


@router.post("/tools", status_code=201)
def create_tool(request: ToolCreateRequest, identity: Identity, service: Catalog) -> dict[str, object]:
    _check_tenant(identity, request)
    return _tool_response(service.create_tool(identity, request.name, request.description, request.input_schema))


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
def list_deployments(identity: Identity, service: Catalog) -> dict[str, object]:
    return {"items": [_deployment_response(item) for item in service.list_deployments(identity)]}


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
