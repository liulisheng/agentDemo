
import pytest

from agent_platform.adapters.identity.fake import FakeIdentityProvider
from agent_platform.domain.common import DomainError
from agent_platform.domain.orchestration import (
    OrchestrationRun,
    TaskAttempt,
    TaskEvent,
    TaskNode,
)
from agent_platform.infrastructure.orchestration_repository import InMemoryOrchestrationRepository


def test_repository_scopes_orchestration_tasks_by_tenant_and_paginates_stably() -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    other = FakeIdentityProvider().resolve_identity("admin-other")
    repository = InMemoryOrchestrationRepository()
    orchestration = OrchestrationRun(
        "orchestration-1", identity.platform_id, identity.tenant_id, "conversation-1", "team-1", 1
    )
    repository.save_orchestration(identity, orchestration)
    repository.save_task(identity, TaskNode("task-1", orchestration.orchestration_id, "one", "任务一"))
    repository.save_task(identity, TaskNode("task-2", orchestration.orchestration_id, "two", "任务二"))

    page = repository.list_tasks(identity, orchestration.orchestration_id, limit=1)
    assert page.items[0].task_id == "task-1"
    assert page.next_cursor == "task-1"
    assert repository.list_tasks(identity, orchestration.orchestration_id, cursor=page.next_cursor).items[0].task_id == "task-2"
    with pytest.raises(DomainError, match="不存在"):
        repository.get_orchestration(other, orchestration.orchestration_id)


def test_repository_assigns_monotonic_event_sequence() -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    repository = InMemoryOrchestrationRepository()
    orchestration = OrchestrationRun(
        "orchestration-1", identity.platform_id, identity.tenant_id, "conversation-1", "team-1", 1
    )
    repository.save_orchestration(identity, orchestration)

    first = repository.append_event(identity, TaskEvent.new(orchestration.orchestration_id, "plan_created"))
    second = repository.append_event(identity, TaskEvent.new(orchestration.orchestration_id, "task_ready"))

    assert (first.sequence_no, second.sequence_no) == (1, 2)
    assert [event.event_type for event in repository.list_events(identity, orchestration.orchestration_id)] == [
        "plan_created",
        "task_ready",
    ]


def test_attempt_is_idempotent_and_old_fencing_token_cannot_complete() -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    repository = InMemoryOrchestrationRepository()
    orchestration = OrchestrationRun("orchestration-1", identity.platform_id, identity.tenant_id, "conversation-1", "team-1", 1)
    repository.save_orchestration(identity, orchestration)
    repository.save_task(identity, TaskNode("task-1", orchestration.orchestration_id, "one", "任务一"))
    attempt = TaskAttempt("attempt-1", "task-1", 1, "attempt-idem")

    first, created = repository.create_attempt(identity, attempt)
    replay, replayed = repository.create_attempt(identity, attempt)
    claimed = repository.claim_attempt(identity, first.attempt_id, "worker-1")

    assert created is True and replayed is False and replay == first
    assert claimed.fencing_token == 1
    with pytest.raises(DomainError, match="租约"):
        repository.complete_attempt(identity, attempt.attempt_id, 0, "SUCCEEDED")
