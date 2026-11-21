"""Deployment、审计、Session、Run 和 Trace API。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from agent_platform.api.schemas import (
    DeploymentCreateRequest,
    DeploymentRollbackRequest,
    RunCreateRequest,
    SessionCreateRequest,
)
from agent_platform.domain.catalog import LifecycleStatus

from .common import Catalog, ControlPlane, Identity, _check_tenant
from .serializers import (
    _deployment_response,
    _run_response,
    _session_response,
    _span_response,
    _timeline_response,
)

router = APIRouter()

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


@router.post("/deployments/{deployment_id}/disable")
def disable_deployment(deployment_id: str, identity: Identity, service: Catalog) -> dict[str, object]:
    """租户管理员下线指定部署，停止该入口接收新会话和运行。"""

    return _deployment_response(service.disable_deployment(identity, deployment_id))


@router.post("/deployments/{deployment_id}/enable")
def enable_deployment(deployment_id: str, identity: Identity, service: Catalog) -> dict[str, object]:
    """租户管理员重新上线仍符合准入条件的指定部署。"""

    return _deployment_response(service.enable_deployment(identity, deployment_id))


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


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: str, identity: Identity, service: ControlPlane) -> dict[str, object]:
    """由 Run 提交者请求取消；已领取任务由持有租约的 Worker 执行取消。"""

    return _run_response(service.request_run_cancellation(identity, run_id))


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


@router.get("/runs/{run_id}/usage")
def get_run_usage(run_id: str, identity: Identity, service: ControlPlane) -> dict[str, object]:
    timeline = service.get_trace(identity, run_id)
    return {"run_id": run_id, "usage": {"input_tokens": timeline.usage.input_tokens, "output_tokens": timeline.usage.output_tokens, "total_tokens": timeline.usage.total_tokens, "source": timeline.usage.source, "estimated": timeline.usage.estimated, "cost_amount": timeline.usage.cost_amount, "currency": timeline.usage.currency, "pricing_version": timeline.usage.pricing_version}}
