import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from agent_platform.adapters.identity.fake import FakeIdentityProvider
from agent_platform.application.conversations import ConversationService
from agent_platform.application.orchestration import OrchestrationService
from agent_platform.application.team_conversations import TeamConversationService
from agent_platform.application.team_turns import TeamTurnService
from agent_platform.application.teams import TeamService
from agent_platform.domain.common import DomainError
from agent_platform.domain.execution import RuntimeStatus
from agent_platform.domain.observability import RunRecord, RunSnapshot
from agent_platform.domain.orchestration import TaskEvent
from agent_platform.domain.teams import TeamMember, TeamMemberRole
from agent_platform.infrastructure.orchestration_repository import InMemoryOrchestrationRepository


def test_team_turn_uses_router_output_and_stores_user_and_plan_messages() -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    teams = TeamService()
    draft = teams.create_draft(identity, "分析团队")
    teams.add_member(identity, draft.team_id, draft.version, TeamMember("router", "employee-router", 1, "deployment-router", TeamMemberRole.ROUTER, "fake", capabilities=("reasoning",)))
    teams.add_member(identity, draft.team_id, draft.version, TeamMember("finalizer", "employee-finalizer", 1, "deployment-finalizer", TeamMemberRole.FINALIZER, "fake", capabilities=("reasoning",)))
    teams.publish(identity, draft.team_id, draft.version)
    conversations = ConversationService()
    created = TeamConversationService(conversations, teams).create_team_conversation(
        identity, "销售分析", draft.team_id, draft.version, lambda _, member: f"session-{member.member_id}"
    )
    conversations.ingest(identity, created.conversation.conversation_id, identity.user_id, "前序消息：分析范围是华东和华南。", session_id="session-router")
    repository = InMemoryOrchestrationRepository()
    orchestrations = OrchestrationService(repository, teams)

    class FakeControlPlane:
        def __init__(self) -> None:
            self.calls = 0
            self.result_text = ""
            self.prompts: list[str] = []

        def get_session(self, _identity, session_id):
            assert session_id.startswith("session-router")
            return SimpleNamespace(engine_name="fake")

        def create_run(self, _identity, _session_id, prompt, idempotency_key):
            self.calls += 1
            self.prompts.append(prompt)
            assert "本次请求" in prompt
            assert idempotency_key
            result = {"summary": "已拆成并行数据分析与报告任务。", "tasks": [
                {"client_key": "analyze", "title": "数据分析", "instruction_ref": "分析销售明细", "required_capabilities": ["reasoning"], "preferred_member_id": "finalizer"}
            ]}
            return SimpleNamespace(status=RuntimeStatus.SUCCEEDED, result_text=self.result_text or json.dumps(result, ensure_ascii=False), run_id=f"router-run-{self.calls}", snapshot=SimpleNamespace(engine_name="fake"))

    class FakeTaskExecutor:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def start_ready(self, _identity, orchestration_id):
            self.calls.append(orchestration_id)

    control_plane = FakeControlPlane()
    task_executor = FakeTaskExecutor()
    service = TeamTurnService(conversations, teams, orchestrations, control_plane, task_executor)

    first = service.submit(identity, created.conversation.conversation_id, "分析本季度销售下降原因", "turn-1")
    replay = service.submit(identity, created.conversation.conversation_id, "分析本季度销售下降原因", "turn-1")
    with pytest.raises(DomainError) as conflict:
        service.submit(identity, created.conversation.conversation_id, "分析另一个问题", "turn-1")

    assert first.created is True and replay.created is False
    assert first.orchestration.orchestration_id == replay.orchestration.orchestration_id
    assert first.task_count == 1
    assert conflict.value.code == "IDEMPOTENCY_CONFLICT"
    assert control_plane.calls == 1
    assert "前序消息" in control_plane.prompts[0]
    assert task_executor.calls == []  # 默认 CONFIRM，必须由用户确认计划后执行。
    messages = conversations.list_messages(identity, created.conversation.conversation_id)
    assert [item.source.value for item in messages] == ["user", "user", "agent"]
    task = repository.list_tasks(identity, first.orchestration.orchestration_id).items[0]
    assert task.preferred_member_id == "finalizer"
    assert task.preferred_engine_name == "fake"

    replanned = service.replan(identity, first.orchestration.orchestration_id)
    assert replanned.orchestration.orchestration_id != first.orchestration.orchestration_id
    assert len(repository.list_tasks(identity, first.orchestration.orchestration_id).items) == 1
    assert len(repository.list_orchestrations(identity, created.conversation.conversation_id)) == 2

    second_conversation = TeamConversationService(conversations, teams).create_team_conversation(
        identity, "模拟任务", draft.team_id, draft.version, lambda _, member: f"session-{member.member_id}-second"
    )
    control_plane.result_text = "Received: 这不是结构化计划"
    simulated = service.submit(identity, second_conversation.conversation.conversation_id, "清理本周业务数据", "turn-simulated")
    assert simulated.summary.startswith("模拟模式：")
    assert repository.list_tasks(identity, simulated.orchestration.orchestration_id).items[0].instruction_ref == "清理本周业务数据"


def test_router_plan_parser_rejects_missing_instructions_and_invalid_dependencies() -> None:
    with pytest.raises(DomainError, match="instruction_ref"):
        TeamTurnService._parse_plan(json.dumps({"tasks": [{"client_key": "a", "title": "A"}]}))
    with pytest.raises(DomainError, match="依赖"):
        TeamTurnService._parse_plan(json.dumps({"tasks": [{"client_key": "a", "title": "A", "instruction_ref": "Do it", "depends_on": "not-an-array"}]}))


def test_persistent_team_turn_queues_router_run_for_worker_without_calling_api_runtime() -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    teams = TeamService()
    draft = teams.create_draft(identity, "异步团队")
    teams.add_member(identity, draft.team_id, 1, TeamMember("router", "employee-router", 1, "deployment-router", TeamMemberRole.ROUTER, "deepseek_harness", capabilities=("reasoning",)))
    teams.add_member(identity, draft.team_id, 1, TeamMember("finalizer", "employee-finalizer", 1, "deployment-finalizer", TeamMemberRole.FINALIZER, "deepseek_harness", capabilities=("reasoning",)))
    teams.publish(identity, draft.team_id, 1)
    conversations = ConversationService()
    created = TeamConversationService(conversations, teams).create_team_conversation(
        identity, "异步计划", draft.team_id, 1, lambda _, member: f"session-{member.member_id}"
    )
    run_snapshot = RunSnapshot(
        employee_id="employee-router", employee_version=1, skill_version_ids=(),
        tool_version_ids=(), execution_profile_id="profile-1", engine_name="deepseek_harness",
    )

    class QueuePersistence:
        def __init__(self) -> None:
            self.run = None
            self.input_message = None

        def submit_worker_run(self, _identity, _conversation_id, run, _idempotency_key, input_message, **_kwargs):
            self.run = run
            self.input_message = input_message
            return run, True

    class FakeControlPlane:
        def __init__(self) -> None:
            self.persistence = QueuePersistence()
            self.sync_calls = 0

        def get_session(self, _identity, _session_id):
            return SimpleNamespace(engine_name="deepseek_harness")

        def prepare_conversation_turn(self, _identity, session_id, _conversation_id, prompt):
            return RunRecord(
                "queued-router-run", session_id, identity.platform_id, identity.tenant_id,
                "trace-router", "a" * 64, run_snapshot, RuntimeStatus.QUEUED,
                submitted_by_user_id=identity.user_id,
            )

        def get_run(self, _identity, _run_id):
            return self.persistence.run

        def create_run(self, *_args, **_kwargs):
            self.sync_calls += 1
            raise AssertionError("API must not execute a credentialed Runtime")

    control_plane = FakeControlPlane()
    conversations.store_message_body = lambda _identity, message: (
        f"object:{message.message_id}", "b" * 64, len(message.text.encode()), "text/plain; charset=utf-8"
    )
    repository = InMemoryOrchestrationRepository()
    orchestrations = OrchestrationService(repository, teams)
    service = TeamTurnService(conversations, teams, orchestrations, control_plane, SimpleNamespace(start_ready=lambda *_: ()))

    submission = service.submit(identity, created.conversation.conversation_id, "拆分一次无外部系统的测试任务", "turn-worker-1")
    replay = service.submit(identity, created.conversation.conversation_id, "拆分一次无外部系统的测试任务", "turn-worker-1")

    assert submission.orchestration.status.value == "PLANNING"
    assert replay.created is False and replay.orchestration.orchestration_id == submission.orchestration.orchestration_id
    assert submission.task_count == 0
    assert submission.router_run_id == "queued-router-run"
    assert control_plane.sync_calls == 0
    assert control_plane.persistence.run.status is RuntimeStatus.QUEUED
    assert control_plane.persistence.input_message.source.value == "system"
    assert any(message.source.value == "user" and message.text == "拆分一次无外部系统的测试任务" for message in conversations.list_messages(identity, created.conversation.conversation_id))

    control_plane.persistence.run = replace(
        control_plane.persistence.run,
        status=RuntimeStatus.SUCCEEDED,
        result_text=json.dumps({"summary": "已生成异步计划。", "tasks": [{"client_key": "inspect", "title": "检查绑定", "instruction_ref": "检查固定引擎绑定", "required_capabilities": ["reasoning"], "preferred_member_id": "router"}]}, ensure_ascii=False),
        worker_id="run-worker-test",
        fencing_token=1,
    )
    service.reconcile_pending(identity, created.conversation.conversation_id)
    completed = orchestrations.get(identity, submission.orchestration.orchestration_id)
    assert completed.status.value == "AWAITING_PLAN_APPROVAL"
    assert repository.list_tasks(identity, completed.orchestration_id).items[0].client_key == "inspect"
    assert conversations.list_messages(identity, created.conversation.conversation_id)[-1].text == "已生成异步计划。"


def test_async_router_unknown_member_plan_becomes_failed_state_instead_of_api_error() -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    teams = TeamService()
    draft = teams.create_draft(identity, "计划校验团队")
    teams.add_member(identity, draft.team_id, 1, TeamMember("router", "employee-router", 1, "deployment-router", TeamMemberRole.ROUTER, "deepseek_harness", capabilities=("reasoning",)))
    teams.add_member(identity, draft.team_id, 1, TeamMember("finalizer", "employee-finalizer", 1, "deployment-finalizer", TeamMemberRole.FINALIZER, "deepseek_harness", capabilities=("reasoning",)))
    teams.publish(identity, draft.team_id, 1)
    conversations = ConversationService()
    created = TeamConversationService(conversations, teams).create_team_conversation(
        identity, "无效计划", draft.team_id, 1, lambda _, member: f"session-{member.member_id}"
    )
    repository = InMemoryOrchestrationRepository()
    orchestrations = OrchestrationService(repository, teams)
    planning = orchestrations.create_planning(identity, created.conversation.conversation_id, draft.team_id, 1, "invalid-router-plan")
    repository.append_event(identity, TaskEvent.new(planning.orchestration_id, "router_run_submitted", payload={"router_run_id": "router-run-invalid"}))

    class FinishedControlPlane:
        persistence = object()

        def get_run(self, _identity, _run_id):
            return RunRecord(
                "router-run-invalid", "session-router", identity.platform_id, identity.tenant_id,
                "trace-router", "a" * 64,
                RunSnapshot("employee-router", 1, (), (), "profile-1", engine_name="deepseek_harness"),
                RuntimeStatus.SUCCEEDED,
                result_text=json.dumps({"summary": "bad member", "tasks": [{"client_key": "a", "title": "A", "instruction_ref": "do A", "preferred_member_id": "missing-member"}]}),
            )

    service = TeamTurnService(conversations, teams, orchestrations, FinishedControlPlane(), SimpleNamespace(start_ready=lambda *_: ()))

    service.reconcile_pending(identity, created.conversation.conversation_id)

    failed = orchestrations.get(identity, planning.orchestration_id)
    assert failed.status.value == "FAILED"
    assert repository.list_tasks(identity, planning.orchestration_id).items == ()
    assert repository.list_events(identity, planning.orchestration_id)[-1].event_type == "router_plan_failed"
