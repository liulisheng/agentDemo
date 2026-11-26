from threading import Event
from types import SimpleNamespace

from agent_platform.adapters.identity.fake import FakeIdentityProvider
from agent_platform.application.artifacts import ArtifactService
from agent_platform.application.conversations import ConversationService
from agent_platform.application.orchestration import OrchestrationService
from agent_platform.application.team_conversations import TeamConversationService
from agent_platform.application.team_task_execution import TeamTaskExecutionService
from agent_platform.application.teams import TeamService
from agent_platform.domain.execution import RuntimeStatus
from agent_platform.domain.observability import RunRecord, RunSnapshot
from agent_platform.domain.orchestration import TaskStatus
from agent_platform.domain.teams import TeamMember, TeamMemberRole
from agent_platform.infrastructure.artifact_repository import InMemoryArtifactRepository
from agent_platform.infrastructure.orchestration_repository import InMemoryOrchestrationRepository


def test_approved_ready_task_runs_on_assigned_member_and_persists_output() -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    teams = TeamService()
    draft = teams.create_draft(identity, "分析团队")
    teams.add_member(identity, draft.team_id, draft.version, TeamMember("router", "employee-router", 1, "deployment-router", TeamMemberRole.ROUTER, "fake", capabilities=("reasoning",)))
    teams.add_member(identity, draft.team_id, draft.version, TeamMember("finalizer", "employee-finalizer", 1, "deployment-finalizer", TeamMemberRole.FINALIZER, "fake", capabilities=("reasoning",)))
    teams.publish(identity, draft.team_id, draft.version)
    conversations = ConversationService()
    created = TeamConversationService(conversations, teams).create_team_conversation(
        identity, "分析任务", draft.team_id, draft.version, lambda _, member: f"session-{member.member_id}"
    )
    repository = InMemoryOrchestrationRepository()
    orchestrations = OrchestrationService(repository, teams)
    orchestration = orchestrations.create_from_plan(
        identity,
        created.conversation.conversation_id,
        draft.team_id,
        draft.version,
        "turn-1",
        [{"client_key": "analyze", "title": "分析", "instruction_ref": "分析销售变化", "required_capabilities": ["reasoning"], "preferred_member_id": "router"}],
    )
    orchestrations.approve_plan(identity, orchestration.orchestration_id)

    class FakeControlPlane:
        def __init__(self) -> None:
            self.submissions: list[tuple[str, str]] = []

        def get_session(self, _identity, session_id):
            return SimpleNamespace(engine_name="fake", status="ACTIVE")

        def run_orchestration_task(self, _identity, session_id, _conversation_id, prompt, **kwargs):
            self.submissions.append((session_id, prompt))
            return SimpleNamespace(status=RuntimeStatus.SUCCEEDED, result_text="分析结果", run_id="run-1", error_category=None, snapshot=SimpleNamespace(engine_name="fake"))

    control_plane = FakeControlPlane()
    artifact_repository = InMemoryArtifactRepository()
    artifact_service = ArtifactService(artifact_repository)
    executor = TeamTaskExecutionService(
        teams, orchestrations, conversations, control_plane,
        artifact_service, max_workers=2,
    )
    try:
        futures = executor.start_ready(identity, orchestration.orchestration_id)
        for future in futures:
            future.result(timeout=5)
    finally:
        executor.close()

    task = repository.list_tasks(identity, orchestration.orchestration_id).items[0]
    assert control_plane.submissions == [("session-router", "分析销售变化")]
    assert task.status is TaskStatus.SUCCEEDED
    assert task.output_ref
    assert orchestrations.get(identity, orchestration.orchestration_id).status.value == "SUCCEEDED"
    team_messages = conversations.list_messages(identity, created.conversation.conversation_id)
    assert team_messages[-1].source.value == "agent"
    assert team_messages[-1].text == "分析结果"
    artifact = artifact_repository.get_artifact(identity, task.output_ref)
    assert artifact.filename.endswith(".md")
    assert artifact_service.read_preview(identity, artifact.artifact_id) == "分析结果"


def test_mysql_team_task_is_queued_for_run_worker_without_api_runtime_execution() -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    teams = TeamService()
    draft = teams.create_draft(identity, "Worker 执行团队")
    teams.add_member(identity, draft.team_id, 1, TeamMember("router", "employee-router", 1, "deployment-router", TeamMemberRole.ROUTER, "deepseek_harness", capabilities=("reasoning",)))
    teams.add_member(identity, draft.team_id, 1, TeamMember("finalizer", "employee-finalizer", 1, "deployment-finalizer", TeamMemberRole.FINALIZER, "deepseek_harness", capabilities=("workspace",)))
    teams.publish(identity, draft.team_id, 1)
    conversations = ConversationService()
    created = TeamConversationService(conversations, teams).create_team_conversation(
        identity, "Worker 队列任务", draft.team_id, 1, lambda _, member: f"session-{member.member_id}"
    )
    repository = InMemoryOrchestrationRepository()
    orchestrations = OrchestrationService(repository, teams)
    orchestration = orchestrations.create_from_plan(
        identity, created.conversation.conversation_id, draft.team_id, 1, "worker-task-1",
        [{"client_key": "analyze", "title": "分析", "instruction_ref": "分析用例", "required_capabilities": ["reasoning"], "preferred_member_id": "router"}],
    )
    orchestrations.approve_plan(identity, orchestration.orchestration_id)

    class QueuePersistence:
        def __init__(self):
            self.run = None
            self.input_message = None

        def submit_worker_run(self, _identity, _conversation_id, run, _key, input_message, **_kwargs):
            self.run = run
            self.input_message = input_message
            return run, True

    class FakeControlPlane:
        def __init__(self):
            self.persistence = QueuePersistence()
            self.inline_calls = 0

        def get_session(self, _identity, session_id):
            return SimpleNamespace(engine_name="deepseek_harness", status="ACTIVE", session_id=session_id)

        def prepare_orchestration_run(self, _identity, session_id, conversation_id, prompt, **kwargs):
            return RunRecord(
                "queued-task-run", session_id, identity.platform_id, identity.tenant_id,
                "trace-task", "b" * 64,
                RunSnapshot("employee-router", 1, (), (), "profile-1", engine_name="deepseek_harness"),
                RuntimeStatus.QUEUED, submitted_by_user_id=identity.user_id,
                orchestration_id=kwargs["orchestration_id"], task_id=kwargs["task_id"], attempt_no=kwargs["attempt_no"],
            )

        def run_orchestration_task(self, *_args, **_kwargs):
            self.inline_calls += 1
            raise AssertionError("API must only enqueue team Runtime work")

    control_plane = FakeControlPlane()
    conversations.store_message_body = lambda _identity, message: (
        f"object:{message.message_id}", "c" * 64, len(message.text.encode()), "text/plain; charset=utf-8"
    )
    executor = TeamTaskExecutionService(
        teams, orchestrations, conversations, control_plane,
        ArtifactService(InMemoryArtifactRepository()), max_workers=1,
    )
    futures = executor.start_ready(identity, orchestration.orchestration_id)
    for future in futures:
        future.result(timeout=3)

    task = repository.list_tasks(identity, orchestration.orchestration_id).items[0]
    attempt = repository.list_attempts(identity, task.task_id)[0]
    assert control_plane.inline_calls == 0
    assert task.status is TaskStatus.ASSIGNED
    assert attempt.status == "QUEUED" and attempt.run_id == "queued-task-run"
    assert control_plane.persistence.run.orchestration_id == orchestration.orchestration_id
    assert control_plane.persistence.run.task_id == task.task_id
    assert control_plane.persistence.input_message.source.value == "system"


def test_team_and_member_concurrency_limits_release_dependent_ready_tasks() -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    teams = TeamService()
    draft = teams.create_draft(identity, "串行成员团队")
    teams.add_member(identity, draft.team_id, 1, TeamMember("router", "employee-router", 1, "deployment-router", TeamMemberRole.ROUTER, "fake", capabilities=("reasoning",), max_concurrency=1))
    teams.add_member(identity, draft.team_id, 1, TeamMember("finalizer", "employee-finalizer", 1, "deployment-finalizer", TeamMemberRole.FINALIZER, "fake", capabilities=("reasoning",), max_concurrency=1))
    teams.update_policy(identity, draft.team_id, 1, "HYBRID", "CONFIRM", max_concurrency=1)
    teams.publish(identity, draft.team_id, 1)
    conversations = ConversationService()
    created = TeamConversationService(conversations, teams).create_team_conversation(
        identity, "串行任务", draft.team_id, 1, lambda _, member: f"session-{member.member_id}"
    )
    repository = InMemoryOrchestrationRepository()
    orchestrations = OrchestrationService(repository, teams)
    orchestration = orchestrations.create_from_plan(identity, created.conversation.conversation_id, draft.team_id, 1, "turn-serial", [
        {"client_key": "first", "title": "第一项", "instruction_ref": "first", "required_capabilities": ["reasoning"], "preferred_member_id": "router"},
        {"client_key": "second", "title": "第二项", "instruction_ref": "second", "required_capabilities": ["reasoning"], "preferred_member_id": "router"},
    ])
    orchestrations.approve_plan(identity, orchestration.orchestration_id)
    ordered_tasks = sorted(repository.list_tasks(identity, orchestration.orchestration_id).items, key=lambda task: task.task_id)
    first_prompt = ordered_tasks[0].instruction_ref
    first_started, release_first = Event(), Event()
    second_started, release_second, second_finished = Event(), Event(), Event()

    class BlockingControlPlane:
        def get_session(self, _identity, _session_id):
            return SimpleNamespace(engine_name="fake", status="ACTIVE")

        def run_orchestration_task(self, _identity, _session_id, _conversation_id, prompt, **kwargs):
            if prompt == first_prompt:
                first_started.set()
                assert release_first.wait(timeout=3)
                return SimpleNamespace(status=RuntimeStatus.SUCCEEDED, result_text="first result", run_id="run-first", error_category=None, snapshot=SimpleNamespace(engine_name="fake"))
            second_started.set()
            assert release_second.wait(timeout=3)
            second_finished.set()
            return SimpleNamespace(status=RuntimeStatus.SUCCEEDED, result_text="second result", run_id="run-second", error_category=None, snapshot=SimpleNamespace(engine_name="fake"))

    executor = TeamTaskExecutionService(teams, orchestrations, conversations, BlockingControlPlane(), ArtifactService(InMemoryArtifactRepository()), max_workers=2)
    try:
        futures = executor.start_ready(identity, orchestration.orchestration_id)
        assert len(futures) == 1
        assert first_started.wait(timeout=3)
        assert not second_started.is_set()
        release_first.set()
        assert second_started.wait(timeout=3)
        release_second.set()
        assert second_finished.wait(timeout=3)
        futures[0].result(timeout=3)
        assert executor.wait_for_idle(timeout=3)
    finally:
        release_first.set()
        release_second.set()
        executor.close()

    assert all(task.status is TaskStatus.SUCCEEDED for task in repository.list_tasks(identity, orchestration.orchestration_id).items)
