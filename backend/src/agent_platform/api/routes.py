from typing import Annotated

from fastapi import APIRouter, Depends, Request

from agent_platform.api.dependencies import (
    catalog_service,
    control_plane_service,
    conversation_service,
    current_identity,
    model_route_service,
)
from agent_platform.api.schemas import (
    AgentBindingRequest,
    ConversationCreateRequest,
    DeliveryCreateRequest,
    DeploymentCreateRequest,
    DeploymentRollbackRequest,
    EmployeeCreateRequest,
    EmployeeVersionCreateRequest,
    ExecutionProfileCreateRequest,
    MessageCreateRequest,
    ModelRouteCreateRequest,
    RunCreateRequest,
    SessionCreateRequest,
    SkillCreateRequest,
    TenantAwareRequest,
    ToolCreateRequest,
)
from agent_platform.application.catalog_service import CatalogService
from agent_platform.application.control_plane import ControlPlaneService
from agent_platform.application.conversations import ConversationService
from agent_platform.application.model_routes import ModelRouteService
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
Conversations = Annotated[ConversationService, Depends(conversation_service)]
ModelRoutes = Annotated[ModelRouteService, Depends(model_route_service)]


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
    identity: Identity, service: Catalog, request: Request
) -> dict[str, object]:
    """开发 Fake Gallery 保持旧 fixture；注入持久仓储时返回真实租户目录。"""

    if request.app.state.use_fake_gallery:
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

    items = []
    for employee in service.list_employees(identity):
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
    return {"items": items}


@router.post("/tools", status_code=201)
def create_tool(request: ToolCreateRequest, identity: Identity, service: Catalog) -> dict[str, object]:
    _check_tenant(identity, request)
    return _tool_response(service.create_tool(identity, request.name, request.description, request.input_schema))


@router.get("/tools")
def list_tools(identity: Identity, service: Catalog) -> dict[str, object]:
    return {"items": [_tool_response(item) for item in service.list_tools(identity)]}


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
def list_deployments(identity: Identity, service: Catalog) -> dict[str, object]:
    return {"items": [_deployment_response(item) for item in service.list_deployments(identity)]}


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
    return {"id": profile.profile_id, "tenant_id": profile.tenant_id, "engine_name": profile.engine_name, "model_alias": profile.model_alias, "status": profile.status.value}


@router.get("/execution-profiles")
def list_execution_profiles(identity: Identity, service: Catalog) -> dict[str, object]:
    return {"items": [{"id": profile.profile_id, "engine_name": profile.engine_name, "model_alias": profile.model_alias, "status": profile.status.value} for profile in service.list_profiles(identity)]}


@router.post("/model-routes", status_code=201)
def create_model_route(request: ModelRouteCreateRequest, identity: Identity, service: ModelRoutes) -> dict[str, object]:
    _check_tenant(identity, request)
    return service.create(identity, request.alias, request.model_name, request.provider, request.endpoint, request.secret_id).to_public_dict()


@router.get("/model-routes")
def list_model_routes(identity: Identity, service: ModelRoutes) -> dict[str, object]:
    return {"items": [item.to_public_dict() for item in service.list(identity)]}


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
