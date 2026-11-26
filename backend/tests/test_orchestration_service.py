import pytest

from agent_platform.adapters.identity.fake import FakeIdentityProvider
from agent_platform.application.orchestration import OrchestrationService
from agent_platform.application.teams import TeamService
from agent_platform.domain.common import DomainError
from agent_platform.domain.orchestration import (
    AssignmentSource,
    OrchestrationStatus,
    TaskNode,
    TaskStatus,
)
from agent_platform.domain.teams import TeamMember, TeamMemberRole
from agent_platform.infrastructure.orchestration_repository import InMemoryOrchestrationRepository


def _team(identity):
    teams = TeamService()
    draft = teams.create_draft(identity, "分析团队")
    teams.add_member(identity, draft.team_id, draft.version, TeamMember("router", "employee-router", 1, "deployment-router", TeamMemberRole.ROUTER, "deepseek_harness", capabilities=("reasoning",)))
    teams.add_member(identity, draft.team_id, draft.version, TeamMember("finalizer", "employee-finalizer", 1, "deployment-finalizer", TeamMemberRole.FINALIZER, "codex_cli", capabilities=("workspace",)))
    teams.publish(identity, draft.team_id, draft.version)
    return teams, draft


def _plan():
    return [
        {"client_key": "analyze", "title": "分析", "required_capabilities": ["reasoning"]},
        {"client_key": "report", "title": "汇总", "depends_on": ["analyze"], "required_capabilities": ["workspace"]},
    ]


def test_plan_is_validated_and_approval_releases_ready_tasks() -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    teams, team = _team(identity)
    repository = InMemoryOrchestrationRepository()
    service = OrchestrationService(repository, teams)

    orchestration = service.create_from_plan(identity, "conversation-1", team.team_id, team.version, "idem-1", _plan())

    assert orchestration.status is OrchestrationStatus.AWAITING_PLAN_APPROVAL
    service.approve_plan(identity, orchestration.orchestration_id)
    assert service.get(identity, orchestration.orchestration_id).status is OrchestrationStatus.READY
    tasks = sorted(repository.list_tasks(identity, orchestration.orchestration_id).items, key=lambda task: task.client_key)
    assert [task.status for task in tasks] == [TaskStatus.READY, TaskStatus.PROPOSED]


def test_planning_orchestration_is_filled_in_place_after_worker_returns_router_plan() -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    teams, team = _team(identity)
    repository = InMemoryOrchestrationRepository()
    service = OrchestrationService(repository, teams)

    planning = service.create_planning(identity, "conversation-async", team.team_id, team.version, "turn-async")

    assert planning.status is OrchestrationStatus.PLANNING
    assert repository.list_tasks(identity, planning.orchestration_id).items == ()
    planned = service.create_from_plan(
        identity, "conversation-async", team.team_id, team.version, "turn-async", _plan()
    )

    assert planned.orchestration_id == planning.orchestration_id
    assert planned.status is OrchestrationStatus.AWAITING_PLAN_APPROVAL
    assert len(repository.list_tasks(identity, planning.orchestration_id).items) == 2


def test_planning_orchestration_resumes_after_a_partial_task_write() -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    teams, team = _team(identity)
    repository = InMemoryOrchestrationRepository()
    service = OrchestrationService(repository, teams)
    planning = service.create_planning(identity, "conversation-partial", team.team_id, team.version, "turn-partial")
    repository.save_task(
        identity,
        TaskNode(
            "task-analyze", planning.orchestration_id, "analyze", "分析",
            required_capabilities=("reasoning",),
        ),
    )

    resumed = service.create_from_plan(
        identity, "conversation-partial", team.team_id, team.version, "turn-partial", _plan()
    )

    assert resumed.orchestration_id == planning.orchestration_id
    assert resumed.status is OrchestrationStatus.AWAITING_PLAN_APPROVAL
    assert {task.client_key for task in repository.list_tasks(identity, planning.orchestration_id).items} == {"analyze", "report"}


def test_plan_rejects_dependency_cycle() -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    teams, team = _team(identity)
    service = OrchestrationService(InMemoryOrchestrationRepository(), teams)

    with pytest.raises(DomainError, match="环"):
        service.create_from_plan(
            identity,
            "conversation-1",
            team.team_id,
            team.version,
            "idem-cycle",
            [
                {"client_key": "a", "title": "A", "depends_on": ["b"]},
                {"client_key": "b", "title": "B", "depends_on": ["a"]},
            ],
        )


def test_replayed_plan_returns_existing_orchestration_after_service_restart() -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    teams, team = _team(identity)
    repository = InMemoryOrchestrationRepository()
    first_service = OrchestrationService(repository, teams)
    first = first_service.create_from_plan(identity, "conversation-1", team.team_id, team.version, "turn-idem", _plan())

    restarted_service = OrchestrationService(repository, teams)
    replay = restarted_service.create_from_plan(identity, "conversation-1", team.team_id, team.version, "turn-idem", _plan())

    assert replay.orchestration_id == first.orchestration_id
    assert len(repository.list_tasks(identity, first.orchestration_id).items) == 2


def test_downstream_task_releases_after_all_dependencies_succeed() -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    teams, team = _team(identity)
    repository = InMemoryOrchestrationRepository()
    service = OrchestrationService(repository, teams)
    orchestration = service.create_from_plan(
        identity,
        "conversation-1",
        team.team_id,
        team.version,
        "idem-deps",
        [
            {"client_key": "a", "title": "A"},
            {"client_key": "b", "title": "B"},
            {"client_key": "c", "title": "C", "depends_on": ["a", "b"]},
        ],
    )
    service.approve_plan(identity, orchestration.orchestration_id)
    for key in ("a", "b"):
        task = next(item for item in repository.list_tasks(identity, orchestration.orchestration_id).items if item.client_key == key)
        service.transition_task(identity, task.task_id, TaskStatus.SUCCEEDED)
    downstream = next(item for item in repository.list_tasks(identity, orchestration.orchestration_id).items if item.client_key == "c")
    assert downstream.status is TaskStatus.READY


def test_user_assignment_locks_a_ready_task_to_member() -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    teams, team = _team(identity)
    repository = InMemoryOrchestrationRepository()
    service = OrchestrationService(repository, teams)
    orchestration = service.create_from_plan(identity, "conversation-1", team.team_id, team.version, "idem-assign", _plan())
    service.approve_plan(identity, orchestration.orchestration_id)
    task = next(item for item in repository.list_tasks(identity, orchestration.orchestration_id).items if item.client_key == "analyze")

    assigned = service.assign_task(identity, task.task_id, "router", source=AssignmentSource.USER)

    assert assigned.assigned_member_id == "router"
    assert assigned.assignment_source is AssignmentSource.USER
    assert assigned.status is TaskStatus.ASSIGNED


def test_cancel_propagates_to_non_terminal_tasks() -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    teams, team = _team(identity)
    repository = InMemoryOrchestrationRepository()
    service = OrchestrationService(repository, teams)
    orchestration = service.create_from_plan(identity, "conversation-1", team.team_id, team.version, "idem-cancel", _plan())
    service.approve_plan(identity, orchestration.orchestration_id)

    cancelled = service.cancel(identity, orchestration.orchestration_id)

    assert cancelled.status is OrchestrationStatus.CANCELLED
    assert all(task.status is TaskStatus.CANCELLED for task in repository.list_tasks(identity, orchestration.orchestration_id).items)


def test_failed_task_can_retry_with_incremented_attempt() -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    teams, team = _team(identity)
    repository = InMemoryOrchestrationRepository()
    service = OrchestrationService(repository, teams)
    orchestration = service.create_from_plan(identity, "conversation-1", team.team_id, team.version, "idem-retry", _plan())
    service.approve_plan(identity, orchestration.orchestration_id)
    task = next(item for item in repository.list_tasks(identity, orchestration.orchestration_id).items if item.client_key == "analyze")
    service.transition_task(identity, task.task_id, TaskStatus.RUNNING)
    service.transition_task(identity, task.task_id, TaskStatus.FAILED)

    retried = service.retry_task(identity, task.task_id)

    assert retried.status is TaskStatus.READY
    assert retried.attempt == 1
    assert service.get(identity, orchestration.orchestration_id).status is OrchestrationStatus.READY


def test_failed_dependency_blocks_downstream_until_upstream_retry_succeeds() -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    teams, team = _team(identity)
    repository = InMemoryOrchestrationRepository()
    service = OrchestrationService(repository, teams)
    orchestration = service.create_from_plan(identity, "conversation-1", team.team_id, team.version, "idem-blocked", [
        {"client_key": "source", "title": "源任务"},
        {"client_key": "final", "title": "汇总", "depends_on": ["source"]},
    ])
    service.approve_plan(identity, orchestration.orchestration_id)
    source = next(task for task in repository.list_tasks(identity, orchestration.orchestration_id).items if task.client_key == "source")
    downstream = next(task for task in repository.list_tasks(identity, orchestration.orchestration_id).items if task.client_key == "final")
    service.transition_task(identity, source.task_id, TaskStatus.RUNNING)
    service.transition_task(identity, source.task_id, TaskStatus.FAILED)

    blocked = repository.get_task(identity, downstream.task_id)
    assert blocked.status is TaskStatus.BLOCKED
    assert blocked.error_code == "DEPENDENCY_FAILED"

    service.retry_task(identity, source.task_id)
    service.transition_task(identity, source.task_id, TaskStatus.RUNNING)
    service.transition_task(identity, source.task_id, TaskStatus.SUCCEEDED)

    assert repository.get_task(identity, downstream.task_id).status is TaskStatus.READY


def test_running_task_timeout_is_a_stable_failure() -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    teams, team = _team(identity)
    repository = InMemoryOrchestrationRepository()
    service = OrchestrationService(repository, teams)
    orchestration = service.create_from_plan(identity, "conversation-1", team.team_id, team.version, "idem-timeout", _plan())
    service.approve_plan(identity, orchestration.orchestration_id)
    task = next(item for item in repository.list_tasks(identity, orchestration.orchestration_id).items if item.client_key == "analyze")
    service.transition_task(identity, task.task_id, TaskStatus.RUNNING)

    timed_out = service.timeout_task(identity, task.task_id)

    assert timed_out.status is TaskStatus.FAILED
    assert timed_out.error_code == "RUNTIME_TIMEOUT"
