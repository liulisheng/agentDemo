"""Agent 团队、任务编排和成员活动 API。"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Query, Response

from agent_platform.api.schemas import (
    ArtifactDeliveryRequest,
    ContinueAsTeamRequest,
    OrchestrationCreateRequest,
    TaskAssignRequest,
    TeamConversationCreateRequest,
    TeamCreateRequest,
    TeamMemberCreateRequest,
    TeamPolicyUpdateRequest,
    TeamRolesUpdateRequest,
    TeamTurnRequest,
)
from agent_platform.domain.common import generate_database_id
from agent_platform.domain.teams import TeamMember

from .common import (
    Artifacts,
    ArtifactServices,
    ControlPlane,
    Conversations,
    Identity,
    Orchestrations,
    TeamConversations,
    Teams,
    TeamTaskExecutors,
    TeamTurns,
    WorkerPools,
    _check_tenant,
)

router = APIRouter()


@router.get("/teams")
def list_teams(identity: Identity, service: Teams) -> dict[str, object]:
    return {
        "permissions": {"can_manage": "admin" in identity.roles, "can_operate": bool(identity.roles.intersection({"admin", "member"}))},
        "items": [
            {
                "team_id": value.team_id,
                "version": value.version,
                "name": value.name,
                "status": value.status.value,
                "orchestration_mode": value.orchestration_mode,
                "approval_mode": value.approval_mode,
                "max_concurrency": value.max_concurrency,
                "delivery_policy": value.delivery_policy,
                "router_member_id": value.router_member_id,
                "finalizer_member_id": value.finalizer_member_id,
                "members": [_member_response(item) for item in value.members],
            }
            for value in service.list_versions(identity)
        ]
    }


@router.post("/conversations/team", status_code=201)
def create_team_conversation(
    request: TeamConversationCreateRequest,
    identity: Identity,
    service: TeamConversations,
    control_plane: ControlPlane,
) -> dict[str, object]:
    """创建绑定已发布团队版本的 Conversation，并为成员创建 Session。"""

    _check_tenant(identity, request)
    result = service.create_team_conversation(
        identity,
        request.title,
        request.team_id,
        request.team_version,
        lambda current_identity, member: _create_member_session(control_plane, current_identity, member),
    )
    return {
        "id": result.conversation.conversation_id,
        "title": result.conversation.title,
        "conversation_mode": result.conversation.conversation_mode.value,
        "team_id": result.team_binding.team_id,
        "team_version": result.team_binding.team_version,
        "bindings": [
            {"employee_id": item.employee_id, "member_id": item.employee_name, "session_id": item.session_id}
            for item in result.agent_bindings
        ],
    }


@router.post("/conversations/{conversation_id}/team-turns", status_code=202)
def submit_team_turn(conversation_id: str, request: TeamTurnRequest, identity: Identity, service: TeamTurns) -> dict[str, object]:
    """提交消息给固定 Router Agent 规划，并创建持久化团队编排。"""

    _check_tenant(identity, request)
    _require_operator(identity)
    result = service.submit(identity, conversation_id, request.text, request.idempotency_key)
    return {
        "orchestration_id": result.orchestration.orchestration_id,
        "conversation_id": result.orchestration.conversation_id,
        "status": result.orchestration.status.value,
        "plan_revision": result.orchestration.plan_revision,
        "router_run_id": result.router_run_id,
        "summary": result.summary,
        "task_count": result.task_count,
        "created": result.created,
    }


@router.post("/orchestrations/{orchestration_id}/replan", status_code=202)
def replan_team_orchestration(orchestration_id: str, identity: Identity, service: TeamTurns) -> dict[str, object]:
    """为现有计划创建新的 Router 请求，旧 Orchestration 和其运行中 Task 不变。"""

    _require_operator(identity)
    result = service.replan(identity, orchestration_id)
    return {
        "orchestration_id": result.orchestration.orchestration_id,
        "conversation_id": result.orchestration.conversation_id,
        "status": result.orchestration.status.value,
        "plan_revision": result.orchestration.plan_revision,
        "router_run_id": result.router_run_id,
        "summary": result.summary,
        "task_count": result.task_count,
        "created": result.created,
    }


@router.post("/conversations/{conversation_id}/continue-as-team", status_code=201)
def continue_conversation_as_team(
    conversation_id: str,
    request: ContinueAsTeamRequest,
    identity: Identity,
    service: TeamConversations,
    control_plane: ControlPlane,
) -> dict[str, object]:
    """从单员工 Conversation 创建新的团队 Conversation。"""

    _check_tenant(identity, request)
    result = service.continue_as_team(
        identity,
        conversation_id,
        request.team_id,
        request.team_version,
        lambda current_identity, member: _create_member_session(control_plane, current_identity, member),
    )
    return {
        "id": result.conversation.conversation_id,
        "source_conversation_id": conversation_id,
        "conversation_mode": result.conversation.conversation_mode.value,
        "team_id": result.team_binding.team_id,
        "team_version": result.team_binding.team_version,
    }


@router.post("/teams", status_code=201)
def create_team(request: TeamCreateRequest, identity: Identity, service: Teams) -> dict[str, object]:
    """创建租户范围团队草稿。"""

    _check_tenant(identity, request)
    version = service.create_draft(identity, request.name)
    return {
        "team_id": version.team_id,
        "version": version.version,
        "name": version.name,
        "status": version.status.value,
    }


@router.post("/teams/{team_id}/versions/{source_version}/fork", status_code=201)
def fork_team_revision(team_id: str, source_version: int, identity: Identity, service: Teams) -> dict[str, object]:
    value = service.fork_revision(identity, team_id, source_version)
    return {
        "team_id": value.team_id,
        "version": value.version,
        "name": value.name,
        "status": value.status.value,
        "orchestration_mode": value.orchestration_mode,
        "approval_mode": value.approval_mode,
        "max_concurrency": value.max_concurrency,
        "delivery_policy": value.delivery_policy,
        "router_member_id": value.router_member_id,
        "finalizer_member_id": value.finalizer_member_id,
        "members": [_member_response(item) for item in value.members],
    }


@router.post("/teams/{team_id}/versions/{version}/members")
def add_team_member(
    team_id: str,
    version: int,
    request: TeamMemberCreateRequest,
    identity: Identity,
    service: Teams,
) -> dict[str, object]:
    """向 DRAFT TeamVersion 添加成员及其引擎能力快照。"""

    _check_tenant(identity, request)
    member = service.add_member(
        identity,
        team_id,
        version,
        TeamMember(
            request.member_id or generate_database_id(),
            request.employee_id,
            request.employee_version,
            request.deployment_id,
            request.role,
            request.engine_name,
            request.model_route_id,
            tuple(request.capabilities),
            request.max_concurrency,
        ),
    )
    return {
        "team_id": member.team_id,
        "version": member.version,
        "status": member.status.value,
        "members": [_member_response(item) for item in member.members],
    }


@router.delete("/teams/{team_id}/versions/{version}/members/{member_id}")
def remove_team_member(team_id: str, version: int, member_id: str, identity: Identity, service: Teams) -> dict[str, object]:
    value = service.remove_member(identity, team_id, version, member_id)
    return {
        "team_id": value.team_id,
        "version": value.version,
        "status": value.status.value,
        "router_member_id": value.router_member_id,
        "finalizer_member_id": value.finalizer_member_id,
        "members": [_member_response(item) for item in value.members],
    }


@router.put("/teams/{team_id}/versions/{version}/policy")
def update_team_policy(
    team_id: str,
    version: int,
    request: TeamPolicyUpdateRequest,
    identity: Identity,
    service: Teams,
) -> dict[str, object]:
    """保存当前草稿的编排与计划审批策略。"""

    _check_tenant(identity, request)
    value = service.update_policy(identity, team_id, version, request.orchestration_mode, request.approval_mode, request.max_concurrency, request.delivery_policy)
    return {
        "team_id": value.team_id,
        "version": value.version,
        "name": value.name,
        "status": value.status.value,
        "orchestration_mode": value.orchestration_mode,
        "approval_mode": value.approval_mode,
        "max_concurrency": value.max_concurrency,
        "delivery_policy": value.delivery_policy,
        "router_member_id": value.router_member_id,
        "finalizer_member_id": value.finalizer_member_id,
        "members": [_member_response(item) for item in value.members],
    }


@router.put("/teams/{team_id}/versions/{version}/roles")
def update_team_roles(team_id: str, version: int, request: TeamRolesUpdateRequest, identity: Identity, service: Teams) -> dict[str, object]:
    _check_tenant(identity, request)
    value = service.update_roles(identity, team_id, version, request.router_member_id, request.finalizer_member_id)
    return {
        "team_id": value.team_id,
        "version": value.version,
        "name": value.name,
        "status": value.status.value,
        "router_member_id": value.router_member_id,
        "finalizer_member_id": value.finalizer_member_id,
        "members": [_member_response(item) for item in value.members],
    }


@router.post("/teams/{team_id}/versions/{version}/publish")
def publish_team(
    team_id: str,
    version: int,
    identity: Identity,
    service: Teams,
    expected_version: int | None = Query(default=None, ge=1),
) -> dict[str, object]:
    """发布通过成员角色和依赖校验的 TeamVersion。"""

    published = service.publish(identity, team_id, version, expected_version=expected_version)
    return {
        "team_id": published.team_id,
        "version": published.version,
        "name": published.name,
        "status": published.status.value,
        "orchestration_mode": published.orchestration_mode,
        "approval_mode": published.approval_mode,
        "max_concurrency": published.max_concurrency,
        "delivery_policy": published.delivery_policy,
        "router_member_id": published.router_member_id,
        "finalizer_member_id": published.finalizer_member_id,
        "members": [_member_response(item) for item in published.members],
    }


@router.get("/teams/{team_id}/versions/{version}")
def get_team(
    team_id: str, version: int, identity: Identity, service: Teams
) -> dict[str, object]:
    """读取团队版本和成员运行能力摘要。"""

    value = service.get(identity, team_id, version)
    return {
        "team_id": value.team_id,
        "version": value.version,
        "name": value.name,
        "status": value.status.value,
        "orchestration_mode": value.orchestration_mode,
        "approval_mode": value.approval_mode,
        "max_concurrency": value.max_concurrency,
        "delivery_policy": value.delivery_policy,
        "router_member_id": value.router_member_id,
        "finalizer_member_id": value.finalizer_member_id,
        "members": [_member_response(item) for item in value.members],
    }


@router.get("/teams/{team_id}/worker-pools")
def get_worker_pools(team_id: str, identity: Identity, service: Teams, pools: WorkerPools, control_plane: ControlPlane, version: int | None = Query(default=None, ge=1)) -> dict[str, object]:
    """返回当前团队所引用 EngineProvider 的 Worker Pool 健康摘要。"""

    team = service.get(identity, team_id, version) if version is not None else service.get_current(identity, team_id)
    from agent_platform.application.engine_providers import normalize_engine_provider_name

    engine_names = sorted({normalize_engine_provider_name(member.engine_name) for member in team.members})
    items = []
    for engine_name in engine_names:
        item = pools.health(engine_name)
        persistence = getattr(control_plane, "persistence", None)
        if persistence is not None and hasattr(persistence, "worker_pool_metrics"):
            item.update(persistence.worker_pool_metrics(identity, engine_name))
        items.append(item)
    return {"team_id": team_id, "items": items}


@router.get("/teams/{team_id}/conversations")
def list_team_conversations(team_id: str, identity: Identity, service: Teams, conversations: Conversations) -> dict[str, object]:
    items = []
    for binding in service.list_bindings(identity, team_id):
        conversation = conversations.get_conversation(identity, binding.conversation_id)
        messages = conversations.list_messages(identity, binding.conversation_id)
        last_message = messages[-1] if messages else None
        items.append({
            "conversation_id": conversation.conversation_id,
            "title": conversation.title,
            "conversation_mode": conversation.conversation_mode.value,
            "team_id": binding.team_id,
            "team_version": binding.team_version,
            "status": conversation.status,
            "updated_at": conversation.updated_at,
            "last_message_preview": (last_message.text[:120] if last_message else ""),
        })
    return {"items": items}


@router.get("/teams/{team_id}/audit-events")
def list_team_audit_events(team_id: str, identity: Identity, service: Teams) -> dict[str, object]:
    return {
        "items": [
            {
                "event_id": event.event_id,
                "actor_user_id": event.actor_user_id,
                "event_type": event.event_type,
                "resource_type": event.resource_type,
                "resource_id": event.resource_id,
                "payload": dict(event.payload),
                "created_at": event.created_at,
            }
            for event in service.list_audit_events(identity, team_id)
        ]
    }


@router.post("/conversations/{conversation_id}/orchestrations", status_code=202)
def create_orchestration(
    conversation_id: str,
    request: OrchestrationCreateRequest,
    identity: Identity,
    conversations: Conversations,
    service: Orchestrations,
    teams: Teams,
) -> dict[str, object]:
    """提交已结构化的 Router 计划；计划验证通过后等待确认或自动进入 READY。"""

    _check_tenant(identity, request)
    if conversations.get_conversation(identity, conversation_id).conversation_mode.value != "TEAM":
        from agent_platform.domain.common import DomainError

        raise DomainError("CONVERSATION_MODE_MISMATCH", "只有 TEAM Conversation 可以创建编排")
    binding = teams.get_binding(identity, conversation_id)
    if (binding.team_id, binding.team_version) != (request.team_id, request.team_version):
        from agent_platform.domain.common import DomainError

        raise DomainError("CONVERSATION_MODE_MISMATCH", "任务计划必须使用 Conversation 固定的团队版本")
    value = service.create_from_plan(
        identity,
        conversation_id,
        request.team_id,
        request.team_version,
        request.idempotency_key,
        request.plan,
    )
    return {
        "orchestration_id": value.orchestration_id,
        "conversation_id": value.conversation_id,
        "status": value.status.value,
        "plan_revision": value.plan_revision,
    }


@router.get("/orchestrations/{orchestration_id}")
def get_orchestration(orchestration_id: str, identity: Identity, service: Orchestrations) -> dict[str, object]:
    value = service.get(identity, orchestration_id)
    return {
        "orchestration_id": value.orchestration_id,
        "conversation_id": value.conversation_id,
        "team_id": value.team_id,
        "team_version": value.team_version,
        "status": value.status.value,
        "plan_revision": value.plan_revision,
    }


@router.get("/orchestrations/{orchestration_id}/tasks")
def list_orchestration_tasks(orchestration_id: str, identity: Identity, service: Orchestrations, control_plane: ControlPlane) -> dict[str, object]:
    page = service.repository.list_tasks(identity, orchestration_id, limit=100)
    dependencies = service.repository.list_dependencies(identity, orchestration_id)
    task_keys = {item.task_id: item.client_key for item in page.items}
    return {
        "items": [
            {
                "task_id": item.task_id,
                "client_key": item.client_key,
                "title": item.title,
                "status": item.status.value,
                "assigned_member_id": item.assigned_member_id,
                "assignment_source": item.assignment_source.value if item.assignment_source else None,
                "attempt": item.attempt,
                "error_code": item.error_code,
                "required_capabilities": list(item.required_capabilities),
                "engine_name": item.preferred_engine_name,
                "output_ref": item.output_ref,
                "started_at": item.started_at,
                "finished_at": item.finished_at,
                "depends_on": [task_keys.get(edge.depends_on_task_id, edge.depends_on_task_id) for edge in dependencies if edge.task_id == item.task_id],
                "latest_attempt": _latest_attempt_response(service, control_plane, identity, item.task_id),
                "phase": _task_phase(item.status.value),
            }
            for item in page.items
        ],
        "next_cursor": page.next_cursor,
    }


@router.get("/orchestrations/{orchestration_id}/events")
def list_orchestration_events(orchestration_id: str, identity: Identity, service: Orchestrations) -> dict[str, object]:
    service.get(identity, orchestration_id)
    return {
        "items": [
            {
                "event_id": event.event_id,
                "orchestration_id": event.orchestration_id,
                "task_id": event.task_id,
                "attempt_id": event.attempt_id,
                "event_type": event.event_type,
                "actor_type": event.actor_type,
                "sequence": event.sequence_no,
                "created_at": event.created_at,
                "payload": dict(event.payload),
            }
            for event in service.repository.list_events(identity, orchestration_id)
        ]
    }


@router.get("/conversations/{conversation_id}/team-activity")
def list_team_activity(conversation_id: str, identity: Identity, service: Orchestrations, executor: TeamTaskExecutors, control_plane: ControlPlane, team_turns: TeamTurns) -> dict[str, object]:
    """返回当前 Conversation 的编排、任务和最近事件快照。"""

    team_turns.reconcile_pending(identity, conversation_id)
    executor.reconcile_conversation(identity, conversation_id)
    orchestrations = service.repository.list_orchestrations(identity, conversation_id)
    items: list[dict[str, object]] = []
    for orchestration in orchestrations:
        tasks = service.repository.list_tasks(identity, orchestration.orchestration_id, limit=100).items
        team = service.teams.get(identity, orchestration.team_id, orchestration.team_version)
        members = {member.member_id: member for member in team.members}
        task_keys = {task.task_id: task.client_key for task in tasks}
        dependencies = service.repository.list_dependencies(identity, orchestration.orchestration_id)
        events = service.repository.list_events(identity, orchestration.orchestration_id)
        items.append(
            {
                "orchestration_id": orchestration.orchestration_id,
                "status": orchestration.status.value,
                "plan_revision": orchestration.plan_revision,
                "tasks": [
                    {
                        "task_id": task.task_id,
                        "title": task.title,
                        "status": task.status.value,
                        "assigned_member_id": task.assigned_member_id,
                        "engine_name": (members.get(task.assigned_member_id).engine_name if task.assigned_member_id in members else task.preferred_engine_name),
                        "worker_pool": (service.repository.list_attempts(identity, task.task_id)[-1].worker_pool if service.repository.list_attempts(identity, task.task_id) else None),
                        "required_capabilities": list(task.required_capabilities),
                        "attempt": task.attempt,
                        "error_code": task.error_code,
                        "output_ref": task.output_ref,
                        "started_at": task.started_at,
                        "finished_at": task.finished_at,
                        "depends_on": [task_keys.get(edge.depends_on_task_id, edge.depends_on_task_id) for edge in dependencies if edge.task_id == task.task_id],
                        "latest_attempt": _latest_attempt_response(service, control_plane, identity, task.task_id),
                        "phase": _task_phase(task.status.value),
                    }
                    for task in tasks
                ],
                "recent_events": [
                    {"event_type": event.event_type, "sequence": event.sequence_no, "created_at": event.created_at}
                    for event in events[-10:]
                ],
            }
        )
    return {"conversation_id": conversation_id, "items": items}


@router.get("/orchestrations/{orchestration_id}/artifacts")
def list_orchestration_artifacts(orchestration_id: str, identity: Identity, service: Orchestrations, artifacts: Artifacts) -> dict[str, object]:
    service.get(identity, orchestration_id)
    return {"items": [_artifact_response(item) for item in artifacts.list_artifacts(identity, orchestration_id)]}


@router.get("/artifacts/{artifact_id}")
def get_artifact(artifact_id: str, identity: Identity, artifacts: Artifacts) -> dict[str, object]:
    return _artifact_response(artifacts.get_artifact(identity, artifact_id))


@router.post("/artifacts/{artifact_id}/download-url")
def create_artifact_download_url(artifact_id: str, identity: Identity, artifacts: Artifacts) -> dict[str, object]:
    value = artifacts.get_artifact(identity, artifact_id)
    if value.visibility.value == "INTERNAL":
        from agent_platform.domain.common import DomainError

        raise DomainError("AUTHORIZATION_DENIED", "当前身份不能下载该产出物")
    return {
        "artifact_id": artifact_id,
        "download_url": f"/api/v1/artifacts/{quote(artifact_id, safe='')}/download",
        "auth_required": True,
        "expires_in_seconds": None,
    }


@router.get("/artifacts/{artifact_id}/preview")
def preview_artifact(artifact_id: str, identity: Identity, service: ArtifactServices) -> dict[str, object]:
    artifact = service.repository.get_artifact(identity, artifact_id)
    return {"artifact_id": artifact_id, "filename": artifact.filename, "content": service.read_preview(identity, artifact_id)}


@router.get("/artifacts/{artifact_id}/download")
def download_artifact(artifact_id: str, identity: Identity, service: ArtifactServices) -> Response:
    artifact = service.repository.get_artifact(identity, artifact_id)
    if artifact.visibility.value == "INTERNAL":
        from agent_platform.domain.common import DomainError

        raise DomainError("AUTHORIZATION_DENIED", "当前身份不能下载该产出物")
    return Response(
        content=service.read_bytes(identity, artifact_id),
        media_type=artifact.mime_type,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(artifact.filename, safe='')}"},
    )


@router.post("/orchestrations/{orchestration_id}/bundle")
def bundle_artifacts(
    orchestration_id: str,
    request: ArtifactDeliveryRequest,
    identity: Identity,
    service: Orchestrations,
    artifacts: Artifacts,
    artifact_service: ArtifactServices,
) -> dict[str, object]:
    _check_tenant(identity, request)
    service.get(identity, orchestration_id)
    for artifact_id in request.artifact_ids:
        if artifacts.get_artifact(identity, artifact_id).orchestration_id != orchestration_id:
            from agent_platform.domain.common import DomainError

            raise DomainError("RESOURCE_NOT_FOUND", "产出物不存在")
    bundle = artifact_service.bundle(identity, request.artifact_ids)
    return _artifact_response(bundle)


@router.post("/orchestrations/{orchestration_id}/deliver")
def deliver_artifacts(
    orchestration_id: str,
    request: ArtifactDeliveryRequest,
    identity: Identity,
    service: Orchestrations,
    artifacts: Artifacts,
    artifact_service: ArtifactServices,
) -> dict[str, object]:
    _check_tenant(identity, request)
    if "admin" not in identity.roles:
        from agent_platform.domain.common import DomainError

        raise DomainError("AUTHORIZATION_DENIED", "只有管理员可以发起产出物交付")
    service.get(identity, orchestration_id)
    deliveries = []
    for artifact_id in request.artifact_ids:
        artifact = artifacts.get_artifact(identity, artifact_id)
        if artifact.orchestration_id != orchestration_id:
            from agent_platform.domain.common import DomainError

            raise DomainError("RESOURCE_NOT_FOUND", "产出物不存在")
        delivery = artifact_service.deliver(identity, artifact_id, request.channel_name, request.idempotency_key)
        deliveries.append(delivery)
    return {"items": [{"delivery_id": item.delivery_id, "artifact_id": item.artifact_id, "status": item.status} for item in deliveries]}


@router.post("/orchestrations/{orchestration_id}/plan/approve")
def approve_orchestration_plan(orchestration_id: str, identity: Identity, service: Orchestrations, executor: TeamTaskExecutors) -> dict[str, object]:
    _require_operator(identity)
    value = service.approve_plan(identity, orchestration_id)
    executor.start_ready(identity, orchestration_id)
    return {"orchestration_id": value.orchestration_id, "status": value.status.value}


@router.post("/orchestrations/{orchestration_id}/pause")
def pause_orchestration(orchestration_id: str, identity: Identity, service: Orchestrations) -> dict[str, object]:
    _require_operator(identity)
    value = service.pause(identity, orchestration_id)
    return {"orchestration_id": value.orchestration_id, "status": value.status.value}


@router.post("/orchestrations/{orchestration_id}/resume")
def resume_orchestration(orchestration_id: str, identity: Identity, service: Orchestrations, executor: TeamTaskExecutors) -> dict[str, object]:
    _require_operator(identity)
    value = service.resume(identity, orchestration_id)
    executor.start_ready(identity, orchestration_id)
    return {"orchestration_id": value.orchestration_id, "status": value.status.value}


@router.post("/orchestrations/{orchestration_id}/cancel")
def cancel_orchestration(orchestration_id: str, identity: Identity, service: Orchestrations, executor: TeamTaskExecutors) -> dict[str, object]:
    _require_operator(identity)
    executor.request_cancel_orchestration(identity, orchestration_id)
    value = service.cancel(identity, orchestration_id)
    return {"orchestration_id": value.orchestration_id, "status": value.status.value}


@router.patch("/orchestration-tasks/{task_id}")
def assign_orchestration_task(
    task_id: str, request: TaskAssignRequest, identity: Identity, service: Orchestrations, executor: TeamTaskExecutors
) -> dict[str, object]:
    _check_tenant(identity, request)
    _require_operator(identity)
    value = service.assign_task(identity, task_id, request.member_id, source=request.source)
    executor.start_ready(identity, value.orchestration_id)
    return {
        "task_id": value.task_id,
        "status": value.status.value,
        "assigned_member_id": value.assigned_member_id,
        "assignment_source": value.assignment_source.value if value.assignment_source else None,
    }


@router.post("/orchestration-tasks/{task_id}/retry")
def retry_orchestration_task(task_id: str, identity: Identity, service: Orchestrations, executor: TeamTaskExecutors) -> dict[str, object]:
    _require_operator(identity)
    value = service.retry_task(identity, task_id)
    executor.start_ready(identity, value.orchestration_id)
    return {"task_id": value.task_id, "status": value.status.value, "attempt": value.attempt}


@router.post("/orchestration-tasks/{task_id}/cancel")
def cancel_orchestration_task(task_id: str, identity: Identity, executor: TeamTaskExecutors) -> dict[str, object]:
    _require_operator(identity)
    value = executor.cancel_task(identity, task_id)
    return {"task_id": value.task_id, "status": value.status.value}


def _require_operator(identity: Identity) -> None:
    if not identity.roles.intersection({"admin", "member"}):
        from agent_platform.domain.common import DomainError

        raise DomainError("AUTHORIZATION_DENIED", "当前身份只有只读权限")


def _member_response(value: object) -> dict[str, object]:
    return {
        "member_id": value.member_id,
        "employee_id": value.employee_id,
        "employee_version": value.employee_version,
        "deployment_id": value.deployment_id,
        "role": value.role.value,
        "engine_name": value.engine_name,
        "model_route_id": value.model_route_id,
        "capabilities": list(value.capabilities),
        "max_concurrency": value.max_concurrency,
        "status": value.status,
    }


def _create_member_session(control_plane: object, identity: object, member: object) -> str:
    """创建成员 Session 前校验所选部署仍使用团队版本固定的引擎和路由。"""

    from agent_platform.application.engine_providers import normalize_engine_provider_name
    from agent_platform.domain.common import DomainError

    session = control_plane.create_session(identity, member.deployment_id)
    if normalize_engine_provider_name(session.engine_name) != normalize_engine_provider_name(member.engine_name):
        raise DomainError("ENGINE_MODEL_INCOMPATIBLE", "成员 Deployment 的引擎与团队配置不一致")
    if member.model_route_id and session.model_route_id != member.model_route_id:
        raise DomainError("ENGINE_MODEL_INCOMPATIBLE", "成员 Deployment 的 ModelRoute 与团队配置不一致")
    return session.session_id


def _attempt_response(value: object, trace_id: str | None = None) -> dict[str, object]:
    return {
        "attempt_id": value.attempt_id,
        "attempt_no": value.attempt_no,
        "status": value.status,
        "worker_id": value.worker_id,
        "fencing_token": value.fencing_token,
        "run_id": value.run_id,
        "error_code": value.error_code,
        "worker_pool": value.worker_pool,
        "trace_id": trace_id,
        "current_phase": getattr(value, "current_phase", None),
    }


def _latest_attempt_response(service: Orchestrations, control_plane: ControlPlane, identity: Identity, task_id: str) -> dict[str, object] | None:
    attempts = service.repository.list_attempts(identity, task_id)
    if not attempts:
        return None
    latest = attempts[-1]
    trace_id = None
    current_phase = None
    if latest.run_id:
        try:
            timeline = control_plane.get_trace(identity, latest.run_id)
            trace_id = timeline.run.trace_id
            if timeline.spans:
                span = max(timeline.spans, key=lambda item: item.sequence)
                phase = span.span_type.value
                attributes = span.attributes
                current_phase = f"工具阶段 · {attributes.get('tool_name')}" if phase == "TOOL" and attributes.get("tool_name") else {
                    "RUNTIME": "Runtime 执行",
                    "MODEL": "模型调用",
                    "TOOL": "工具调用",
                    "RESULT": "整理执行结果",
                    "CONTEXT": "准备执行上下文",
                    "SKILL": "加载成员能力",
                }.get(phase, phase)
        except Exception:  # noqa: BLE001 — 缺失 Run 快照不应泄露内部异常或阻断任务面板
            trace_id = None
    return {**_attempt_response(latest, trace_id), "current_phase": current_phase}


def _task_phase(status: str) -> str:
    return {
        "PROPOSED": "等待上游任务依赖",
        "READY": "等待成员或 Worker Pool 领取",
        "ASSIGNED": "已分配，等待引擎执行槽位",
        "CLAIMED": "已领取，准备启动 Runtime",
        "RUNNING": "正在执行固定 EngineProvider Run",
        "WAITING_APPROVAL": "等待用户审批",
        "BLOCKED": "上游依赖未满足",
        "SUCCEEDED": "任务结果已保存",
        "FAILED": "任务失败，可查看错误或重试",
        "CANCELLED": "任务已取消",
    }.get(status, status)


def _artifact_response(value: object) -> dict[str, object]:
    return {
        "artifact_id": value.artifact_id,
        "conversation_id": value.conversation_id,
        "orchestration_id": value.orchestration_id,
        "task_id": value.task_id,
        "run_id": value.run_id,
        "attempt_no": value.attempt_no,
        "filename": value.filename,
        "mime_type": value.mime_type,
        "size_bytes": value.size_bytes,
        "content_sha256": value.content_sha256,
        "source_engine": value.source_engine,
        "visibility": value.visibility.value,
        "status": value.status.value,
        "created_at": value.created_at,
    }
