"""独立 Run Worker 的租约、输入摘要和取消行为。"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import replace
from threading import Event

import pytest

from agent_platform.domain.execution import RuntimeStatus
from agent_platform.domain.identity import IdentityContext
from agent_platform.domain.observability import RunRecord, RunSnapshot, SessionRecord
from agent_platform.ports.execution_repository import RunClaim, RunInputRef
from agent_platform.ports.runtime import RuntimeCapabilities


class _Repository:
    def __init__(
        self,
        *,
        cancel_before_start: bool = False,
        cancel_during_run: bool = False,
        lose_lease: bool = False,
    ) -> None:
        self.claim = RunClaim("platform-1", "tenant-1", "run-1", "worker-1", 4)
        self.run = RunRecord(
            run_id="run-1",
            session_id="session-1",
            platform_id="platform-1",
            tenant_id="tenant-1",
            trace_id="trace-1",
            prompt_digest="d" * 64,
            snapshot=RunSnapshot(
                employee_id="employee-1",
                employee_version=3,
                skill_version_ids=("skill-1",),
                tool_version_ids=(),
                execution_profile_id="profile-1",
            ),
            status=RuntimeStatus.QUEUED,
            submitted_by_user_id="user-1",
        )
        self.session = SessionRecord(
            session_id="session-1",
            platform_id="platform-1",
            tenant_id="tenant-1",
            deployment_id="deployment-1",
            trace_id="trace-1",
            employee_version=3,
            employee_id="employee-1",
            execution_profile_id="profile-1",
        )
        self.cancel_before_start = cancel_before_start
        self.cancel_during_run = cancel_during_run
        self.lose_lease = lose_lease
        self.claimed = False
        self.started = False
        self.heartbeat_calls = 0
        self.completed: list[tuple[RunRecord, int]] = []
        self.unknown: list[tuple[str, int]] = []
        self.fencing_token = 4

    def claim_next_queued_run(self, platform_id, worker_id, *, lease_seconds):
        assert platform_id == "platform-1"
        assert worker_id == "worker-1"
        if self.claimed:
            return None
        self.claimed = True
        return self.claim

    def get_run(self, identity, run_id):
        return replace(self.run)

    def get_session(self, identity, session_id):
        return self.session

    def get_run_input_ref(self, identity, run_id):
        digest = hashlib.sha256("生成离线工作流".encode()).hexdigest()
        return RunInputRef("message-1", "object:message-1", digest, 21)

    def start_claimed_run(self, identity, run_id, worker_id, fencing_token):
        if self.cancel_before_start:
            self.run.status = RuntimeStatus.CANCELLING
            return False
        self.started = True
        self.run.status = RuntimeStatus.RUNNING
        return True

    def heartbeat(self, identity, run_id, fencing_token, *, worker_id, lease_seconds):
        self.heartbeat_calls += 1
        if self.cancel_during_run:
            self.run.status = RuntimeStatus.CANCELLING
        return not self.lose_lease

    def complete_run(self, identity, run, fencing_token, spans, usage):
        if fencing_token != self.fencing_token:
            return False
        self.completed.append((replace(run), fencing_token))
        self.run = replace(run)
        return True

    def mark_unknown(self, identity, run_id, fencing_token):
        self.unknown.append((run_id, fencing_token))
        self.fencing_token += 1
        self.run.status = RuntimeStatus.UNKNOWN
        return True


class _ObjectStore:
    def __init__(self, content: bytes | None = None) -> None:
        self.content = content if content is not None else "生成离线工作流".encode()

    def read(self, object_ref, platform_id, tenant_id):
        assert (object_ref, platform_id, tenant_id) == (
            "object:message-1", "platform-1", "tenant-1"
        )
        return self.content


class _Runtime:
    capabilities = RuntimeCapabilities(cancellation=True)

    def __init__(self) -> None:
        self.cancelled = Event()
        self.cancel_calls: list[str] = []

    def cancel(self, session_ref):
        self.cancel_calls.append(session_ref)
        self.cancelled.set()
        return True


class _ControlPlane:
    def __init__(self, runtime: _Runtime, *, block_until_cancelled: bool = False) -> None:
        self.runtime = runtime
        self.block_until_cancelled = block_until_cancelled
        self.requests: list[tuple[IdentityContext, str, int]] = []
        self.repository = None

    def execute_claimed_run(self, identity, run, session, prompt, *, worker_id, fencing_token):
        self.requests.append((identity, prompt, fencing_token))
        session.runtime_session_ref = "runtime-session-1"
        if self.block_until_cancelled:
            if not self.runtime.cancelled.wait(timeout=2):
                raise TimeoutError("Runtime cancellation was not delivered")
            run.status = RuntimeStatus.CANCELLED
        else:
            run.status = RuntimeStatus.SUCCEEDED
            run.result_text = "fake: " + prompt
        if self.repository is not None:
            self.repository.complete_run(identity, run, fencing_token, (), None)
        return run


def _worker(repository, store, control_plane):
    from agent_platform.workers.run_worker import RunWorker

    control_plane.repository = repository
    return RunWorker(
        "platform-1",
        "worker-1",
        repository,
        control_plane,
        store,
        lease_seconds=30,
        heartbeat_interval_seconds=0.01,
        poll_interval_seconds=0.01,
        cancellation_grace_seconds=0.2,
    )


def test_run_worker_reads_verified_user_message_and_executes_once() -> None:
    repository = _Repository()
    runtime = _Runtime()
    control_plane = _ControlPlane(runtime)
    worker = _worker(repository, _ObjectStore(), control_plane)

    processed = asyncio.run(worker.run_once())

    assert processed
    assert repository.started
    assert len(control_plane.requests) == 1
    identity, prompt, fencing_token = control_plane.requests[0]
    assert identity.auth_source == "worker"
    assert identity.tenant_id == "tenant-1"
    assert prompt == "生成离线工作流"
    assert fencing_token == 4
    assert repository.run.status is RuntimeStatus.SUCCEEDED


def test_run_worker_rejects_a_changed_input_digest_before_runtime() -> None:
    repository = _Repository()
    runtime = _Runtime()
    control_plane = _ControlPlane(runtime)
    worker = _worker(repository, _ObjectStore("正文摘要不匹配".encode()), control_plane)

    processed = asyncio.run(worker.run_once())

    assert processed
    assert control_plane.requests == []
    assert repository.run.status is RuntimeStatus.FAILED
    assert repository.run.error_category == "RUN_INPUT_DIGEST_MISMATCH"
    assert repository.completed[0][1] == 4


def test_run_worker_completes_a_cancelled_claim_before_calling_runtime() -> None:
    repository = _Repository(cancel_before_start=True)
    runtime = _Runtime()
    control_plane = _ControlPlane(runtime)
    worker = _worker(repository, _ObjectStore(), control_plane)

    processed = asyncio.run(worker.run_once())

    assert processed
    assert control_plane.requests == []
    assert repository.run.status is RuntimeStatus.CANCELLED
    assert repository.completed[0][1] == 4


def test_run_worker_waits_for_runtime_acknowledgement_of_submitter_cancellation() -> None:
    repository = _Repository(cancel_during_run=True)
    runtime = _Runtime()
    control_plane = _ControlPlane(runtime, block_until_cancelled=True)
    worker = _worker(repository, _ObjectStore(), control_plane)

    processed = asyncio.run(worker.run_once())

    assert processed
    assert runtime.cancel_calls == ["runtime-session-1"]
    assert repository.run.status is RuntimeStatus.CANCELLED
    assert repository.unknown == []


def test_run_worker_fences_a_lost_lease_and_requests_runtime_cancellation() -> None:
    repository = _Repository(lose_lease=True)
    runtime = _Runtime()
    control_plane = _ControlPlane(runtime, block_until_cancelled=True)
    worker = _worker(repository, _ObjectStore(), control_plane)

    processed = asyncio.run(worker.run_once())

    assert processed
    assert runtime.cancel_calls == ["runtime-session-1"]
    assert repository.unknown == [("run-1", 4)]
    assert repository.run.status is RuntimeStatus.UNKNOWN


def test_worker_entrypoint_rejects_fake_mode(monkeypatch) -> None:
    from agent_platform.workers.run_worker import main

    monkeypatch.setenv("AGENT_PLATFORM_MODE", "fake")
    with pytest.raises(RuntimeError, match="MySQL"):
        main()
