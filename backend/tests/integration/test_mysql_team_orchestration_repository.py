"""验证 Agent 团队状态可由全新 MySQL 仓储实例恢复。"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

import pytest

mysql = pytest.importorskip("mysql.connector")


class _RedactedMySqlConfig(dict[str, object]):
    """防止 pytest 失败回溯打印本地 MySQL 凭据。"""

    def __repr__(self) -> str:
        return "MySQL test connection config(<redacted>)"


@pytest.fixture
def mysql_team_database():
    if os.environ.get("AGENT_PLATFORM_MYSQL_TEST_ALLOW_CREATE_DATABASE") != "1":
        pytest.skip("set AGENT_PLATFORM_MYSQL_TEST_ALLOW_CREATE_DATABASE=1 for disposable MySQL integration")
    database = f"agent_platform_team_test_{secrets.token_hex(5)}"
    config = _RedactedMySqlConfig({
        "host": os.environ.get("MYSQL_HOST", "127.0.0.1"),
        "port": int(os.environ.get("MYSQL_PORT", "3306")),
        "user": os.environ.get("MYSQL_USER", "root"),
        "password": os.environ.get("MYSQL_PASSWORD", ""),
    })
    connection = mysql.connect(**config)
    cursor = connection.cursor()
    try:
        cursor.execute(f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci")
        connection.commit()
    finally:
        cursor.close()
        connection.close()
    try:
        from agent_platform.infrastructure.migrations import MigrationRunner

        MigrationRunner(
            lambda: mysql.connect(**config, database=database),
            Path(__file__).parents[3] / "docs" / "migrations",
        ).apply()
        connection = mysql.connect(**config, database=database)
        cursor = connection.cursor()
        try:
            cursor.execute("INSERT INTO platforms (platform_id,code,name,status) VALUES ('plt-local','local','本地','ACTIVE')")
            cursor.execute("INSERT INTO tenants (platform_id,tenant_id,code,name,status) VALUES ('plt-local','tenant-a','tenant-a','A','ACTIVE')")
            cursor.execute(
                "INSERT INTO users (platform_id,tenant_id,user_id,external_subject,username,display_name,status) "
                "VALUES ('plt-local','tenant-a','admin-local','subject-local','admin-local','管理员','ACTIVE')"
            )
            connection.commit()
        finally:
            cursor.close()
            connection.close()
        yield database, config
    finally:
        connection = mysql.connect(**config)
        cursor = connection.cursor()
        try:
            cursor.execute(f"DROP DATABASE IF EXISTS `{database}`")
            connection.commit()
        finally:
            cursor.close()
            connection.close()


def test_orchestration_tasks_dependencies_attempts_and_events_survive_repository_restart(mysql_team_database) -> None:
    from agent_platform.adapters.identity.fake import FakeIdentityProvider
    from agent_platform.domain.conversations import Conversation, ConversationMode
    from agent_platform.domain.orchestration import (
        OrchestrationRun,
        TaskAttempt,
        TaskDependency,
        TaskEvent,
        TaskNode,
    )
    from agent_platform.domain.teams import Team, TeamVersion
    from agent_platform.infrastructure.mysql_conversations import MySqlConversationRepository
    from agent_platform.infrastructure.mysql_orchestration import MySqlOrchestrationRepository
    from agent_platform.infrastructure.mysql_teams import MySqlTeamRepository

    database, config = mysql_team_database
    connection_factory = lambda: mysql.connect(**config, database=database)
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    team_id, conversation_id = "team-1", "conversation-1"
    teams = MySqlTeamRepository(connection_factory)
    teams.save_team(identity, Team(team_id, identity.platform_id, identity.tenant_id, "分析团队"))
    teams.save_version(identity, TeamVersion(team_id, identity.platform_id, identity.tenant_id, 1, "分析团队"))
    MySqlConversationRepository(connection_factory).save_conversation(
        identity,
        Conversation(conversation_id, identity.platform_id, identity.tenant_id, "季度分析", True, conversation_mode=ConversationMode.TEAM),
    )

    repository = MySqlOrchestrationRepository(connection_factory)
    orchestration = OrchestrationRun(
        "orchestration-1", identity.platform_id, identity.tenant_id, conversation_id, team_id, 1,
        status="READY", approval_mode="CONFIRM", idempotency_key="turn-1",
    )
    repository.save_orchestration(identity, orchestration)
    first = TaskNode("task-1", orchestration.orchestration_id, "analyze", "需求分析", instruction_ref="分析问题", status="READY")
    second = TaskNode("task-2", orchestration.orchestration_id, "report", "汇总报告", instruction_ref="汇总结果")
    repository.save_task(identity, first)
    repository.save_task(identity, second)
    repository.save_dependency(identity, orchestration.orchestration_id, TaskDependency(second.task_id, first.task_id))
    attempt, created = repository.create_attempt(identity, TaskAttempt("attempt-1", first.task_id, 1, "task-1-attempt-1"))
    first_event = repository.append_event(identity, TaskEvent.new(orchestration.orchestration_id, "plan_created"))
    second_event = repository.append_event(identity, TaskEvent.new(orchestration.orchestration_id, "task_ready", task_id=first.task_id))

    restarted = MySqlOrchestrationRepository(connection_factory)

    assert created is True
    assert restarted.get_orchestration(identity, orchestration.orchestration_id).idempotency_key == "turn-1"
    assert [item.task_id for item in restarted.list_tasks(identity, orchestration.orchestration_id).items] == ["task-1", "task-2"]
    assert restarted.list_dependencies(identity, orchestration.orchestration_id) == (TaskDependency(second.task_id, first.task_id),)
    assert restarted.list_attempts(identity, first.task_id) == (attempt,)
    assert (first_event.sequence_no, second_event.sequence_no) == (1, 2)
    assert [event.event_type for event in restarted.list_events(identity, orchestration.orchestration_id)] == ["plan_created", "task_ready"]


def test_router_and_member_tasks_execute_through_persistent_run_worker(mysql_team_database, tmp_path) -> None:
    import asyncio

    from agent_platform.adapters.identity.fake import FakeIdentityProvider
    from agent_platform.adapters.runtime.fake import FakeRuntimeAdapter
    from agent_platform.application.artifacts import ArtifactService
    from agent_platform.application.catalog_service import CatalogService
    from agent_platform.application.control_plane import ControlPlaneService
    from agent_platform.application.conversations import ConversationService
    from agent_platform.application.engine_providers import EngineProviderRegistry
    from agent_platform.application.model_routes import ModelRouteService
    from agent_platform.application.orchestration import OrchestrationService
    from agent_platform.application.team_conversations import TeamConversationService
    from agent_platform.application.team_task_execution import TeamTaskExecutionService
    from agent_platform.application.team_turns import TeamTurnService
    from agent_platform.application.teams import TeamService
    from agent_platform.domain.catalog import SkillType
    from agent_platform.domain.orchestration import TaskStatus
    from agent_platform.domain.teams import TeamMember, TeamMemberRole
    from agent_platform.infrastructure.mysql_artifacts import MySqlArtifactRepository
    from agent_platform.infrastructure.mysql_catalog import MySqlCatalogRepository
    from agent_platform.infrastructure.mysql_conversations import MySqlConversationRepository
    from agent_platform.infrastructure.mysql_execution import MySqlExecutionRepository
    from agent_platform.infrastructure.mysql_orchestration import MySqlOrchestrationRepository
    from agent_platform.infrastructure.mysql_storage import MySqlMediaAssetRepository
    from agent_platform.infrastructure.mysql_teams import MySqlTeamRepository
    from agent_platform.ports.storage import LocalObjectStore
    from agent_platform.workers.run_worker import RunWorker
    from tests._runtime_config_helpers import configure_in_memory_deployment

    database, config = mysql_team_database
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    connection_factory = lambda: mysql.connect(**config, database=database)
    catalog = CatalogService(MySqlCatalogRepository(connection_factory))
    route = ModelRouteService(catalog.repository).create(
        identity, "team-worker-route", "fake-model", "fake", "http://fake.invalid", "unused-worker-secret"
    )
    profile = catalog.create_profile(identity, "fake", "team-worker-route", model_route_id=route.route_id)
    skill = catalog.create_skill(identity, "Worker 集成 Skill", SkillType.INSTRUCTION, "处理被分配的团队任务。", [])
    catalog.publish_skill(identity, skill.skill_id)
    deployments = {}
    for suffix, name in (("router", "Router 员工"), ("finalizer", "汇总员工")):
        employee = catalog.create_employee(identity, name, "测试", "Worker 任务集成")
        version = catalog.create_employee_version(
            identity, employee.employee_id, "只处理测试任务", [skill.skill_id], [], execution_profile_id=profile.profile_id
        )
        published = catalog.publish_employee_version(identity, employee.employee_id, version.version)
        deployment = catalog.deploy(identity, employee.employee_id, published.version)
        deployments[suffix] = configure_in_memory_deployment(catalog, identity, deployment)

    object_store = LocalObjectStore(
        tmp_path / "team-worker-objects",
        metadata_repository=MySqlMediaAssetRepository(connection_factory),
    )
    execution = MySqlExecutionRepository(connection_factory)
    conversation_repository = MySqlConversationRepository(connection_factory)
    team_repository = MySqlTeamRepository(connection_factory)
    orchestration_repository = MySqlOrchestrationRepository(connection_factory)
    artifact_repository = MySqlArtifactRepository(connection_factory)
    runtime = FakeRuntimeAdapter()
    control_plane = ControlPlaneService(
        catalog, runtime, persistence=execution, object_store=object_store,
        conversation_repository=conversation_repository,
        engine_providers=EngineProviderRegistry.for_runtime(runtime),
    )
    teams = TeamService(team_repository)
    draft = teams.create_draft(identity, "Worker 执行团队")
    teams.add_member(identity, draft.team_id, draft.version, TeamMember(
        "router", deployments["router"].employee_id, deployments["router"].employee_version.version,
        deployments["router"].deployment_id, TeamMemberRole.ROUTER, "fake",
        model_route_id=route.route_id, capabilities=("reasoning",),
    ))
    teams.add_member(identity, draft.team_id, draft.version, TeamMember(
        "finalizer", deployments["finalizer"].employee_id, deployments["finalizer"].employee_version.version,
        deployments["finalizer"].deployment_id, TeamMemberRole.FINALIZER, "fake",
        model_route_id=route.route_id, capabilities=("workspace",),
    ))
    teams.publish(identity, draft.team_id, draft.version)
    conversations = ConversationService(conversation_repository, object_store)
    created = TeamConversationService(conversations, teams).create_team_conversation(
        identity, "Worker 队列验收", draft.team_id, draft.version,
        lambda current_identity, member: control_plane.create_session(
            current_identity, member.deployment_id
        ).session_id,
    )
    single_session = control_plane.create_session(identity, deployments["router"].deployment_id)
    single_conversation = conversations.create_conversation(identity, "单员工验收", is_group=False)
    conversations.bind_agent(
        identity, single_conversation.conversation_id, deployments["router"].employee_id,
        "Router 员工", single_session.session_id,
    )
    assert [item.conversation_id for item in conversations.list_for_employee(identity, deployments["router"].employee_id).items] == [single_conversation.conversation_id]
    orchestrations = OrchestrationService(orchestration_repository, teams)
    artifacts = ArtifactService(artifact_repository, object_store)
    executor = TeamTaskExecutionService(teams, orchestrations, conversations, control_plane, artifacts)
    turns = TeamTurnService(conversations, teams, orchestrations, control_plane, executor)
    worker = RunWorker(
        identity.platform_id, "run-worker-team-integration", execution, control_plane,
        object_store, lease_seconds=30, poll_interval_seconds=0.01, engine_names=("fake",),
    )

    submission = turns.submit(
        identity, created.conversation.conversation_id,
        "端到端检查 Router 消息和成员固定引擎。", "worker-router-turn-1",
    )
    assert submission.orchestration.status.value == "PLANNING"
    assert submission.task_count == 0
    assert conversation_repository.get_conversation_id_for_run(identity, submission.router_run_id) == created.conversation.conversation_id
    assert asyncio.run(worker.run_once()) is True
    turns.reconcile_pending(identity, created.conversation.conversation_id)
    planned = orchestrations.get(identity, submission.orchestration.orchestration_id)
    assert planned.status.value == "AWAITING_PLAN_APPROVAL"
    assert len(orchestration_repository.list_tasks(identity, planned.orchestration_id).items) == 1

    orchestrations.approve_plan(identity, planned.orchestration_id)
    executor.start_ready(identity, planned.orchestration_id)
    queued_task = orchestration_repository.list_tasks(identity, planned.orchestration_id).items[0]
    queued_attempt = orchestration_repository.list_attempts(identity, queued_task.task_id)[0]
    assert queued_task.status is TaskStatus.ASSIGNED
    assert queued_attempt.status == "QUEUED" and queued_attempt.run_id is not None
    queued_metrics = execution.worker_pool_metrics(identity, "fake")
    assert queued_metrics["queue_depth"] == 1 and queued_metrics["active_workers"] == 0
    assert queued_metrics["healthy"] is False
    execution.heartbeat_worker(identity.platform_id, "idle-fake-worker", "fake")
    assert execution.worker_pool_metrics(identity, "fake")["healthy"] is True
    execution.heartbeat_worker(identity.platform_id, "idle-fake-worker", "fake", status="STOPPING")
    assert execution.worker_pool_metrics(identity, "fake")["healthy"] is False
    execution.heartbeat_worker(identity.platform_id, "idle-fake-worker", "fake")
    connection = connection_factory()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "UPDATE runtime_worker_heartbeats SET heartbeat_at=TIMESTAMPADD(SECOND,-31,CURRENT_TIMESTAMP(6)) "
            "WHERE platform_id=%s AND worker_id='idle-fake-worker' AND engine_name='fake'",
            (identity.platform_id,),
        )
        connection.commit()
    finally:
        cursor.close()
        connection.close()
    assert execution.worker_pool_metrics(identity, "fake")["healthy"] is False
    execution.heartbeat_worker(identity.platform_id, "idle-fake-worker", "fake")
    assert asyncio.run(worker.run_once()) is True
    executor.reconcile_conversation(identity, created.conversation.conversation_id)

    finished = orchestration_repository.get_task(identity, queued_task.task_id)
    finished_attempt = orchestration_repository.list_attempts(identity, queued_task.task_id)[0]
    assert finished.status is TaskStatus.SUCCEEDED
    assert finished_attempt.status == "SUCCEEDED"
    assert finished_attempt.worker_id == "run-worker-team-integration"
    assert finished_attempt.fencing_token > 0
    artifact = artifact_repository.list_artifacts(identity, planned.orchestration_id)[0]
    assert artifacts.read_preview(identity, artifact.artifact_id).startswith("fake:")
    visible_messages = [message for message in conversations.list_messages(identity, created.conversation.conversation_id) if message.source.value != "system"]
    assert [message.source.value for message in visible_messages] == ["user", "agent", "agent"]


def test_failed_team_member_write_rolls_back_version_and_member_rows(mysql_team_database) -> None:
    from agent_platform.adapters.identity.fake import FakeIdentityProvider
    from agent_platform.domain.common import DomainError
    from agent_platform.domain.teams import Team, TeamMember, TeamMemberRole, TeamVersion
    from agent_platform.infrastructure.mysql_teams import MySqlTeamRepository

    database, config = mysql_team_database
    connection_factory = lambda: mysql.connect(**config, database=database)
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    repository = MySqlTeamRepository(connection_factory)
    team_id = "team-rollback"
    repository.save_team(identity, Team(team_id, identity.platform_id, identity.tenant_id, "回滚验证团队"))
    invalid_member = TeamMember("router-invalid", "employee-missing", 1, "deployment-missing", TeamMemberRole.ROUTER, "fake")

    with pytest.raises(mysql.Error):
        repository.save_version(identity, TeamVersion(team_id, identity.platform_id, identity.tenant_id, 1, "回滚验证团队", (invalid_member,), router_member_id=invalid_member.member_id))

    with pytest.raises(DomainError):
        repository.get_version(identity, team_id, 1)


def test_artifact_metadata_and_object_body_survive_service_restart(tmp_path: Path, mysql_team_database) -> None:
    from agent_platform.adapters.identity.fake import FakeIdentityProvider
    from agent_platform.application.artifacts import ArtifactService
    from agent_platform.domain.conversations import Conversation
    from agent_platform.infrastructure.mysql_artifacts import MySqlArtifactRepository
    from agent_platform.infrastructure.mysql_conversations import MySqlConversationRepository
    from agent_platform.infrastructure.mysql_storage import MySqlMediaAssetRepository
    from agent_platform.ports.storage import LocalObjectStore

    database, config = mysql_team_database
    connection_factory = lambda: mysql.connect(**config, database=database)
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    conversation_id = "conversation-artifact"
    MySqlConversationRepository(connection_factory).save_conversation(
        identity,
        Conversation(conversation_id, identity.platform_id, identity.tenant_id, "团队交付", True, conversation_mode="TEAM"),
    )
    objects_root = tmp_path / "objects"
    first_repository = MySqlArtifactRepository(connection_factory)
    first_store = LocalObjectStore(objects_root, metadata_repository=MySqlMediaAssetRepository(connection_factory))
    first_process = ArtifactService(first_repository, first_store)
    artifact = first_process.create_text(identity, conversation_id, "orchestration-1", "task-1", "run-1", "report.md", "产物正文")

    restarted_repository = MySqlArtifactRepository(connection_factory)
    restarted_store = LocalObjectStore(objects_root, metadata_repository=MySqlMediaAssetRepository(connection_factory))
    restarted_process = ArtifactService(restarted_repository, restarted_store)
    bundle = restarted_process.bundle(identity, [artifact.artifact_id], "delivery.zip")
    delivery = restarted_process.deliver(identity, artifact.artifact_id, "web", "delivery-key")
    replay = restarted_process.deliver(identity, artifact.artifact_id, "web", "delivery-key")

    assert restarted_repository.get_artifact(identity, artifact.artifact_id).artifact_id == artifact.artifact_id
    assert restarted_process.read_preview(identity, artifact.artifact_id) == "产物正文"
    final_process = ArtifactService(
        MySqlArtifactRepository(connection_factory),
        LocalObjectStore(objects_root, metadata_repository=MySqlMediaAssetRepository(connection_factory)),
    )
    assert final_process.read_bytes(identity, bundle.artifact_id).startswith(b"PK")
    assert delivery.delivery_id == replay.delivery_id


def test_team_policy_revision_and_member_roles_survive_repository_restart(mysql_team_database) -> None:
    from agent_platform.adapters.identity.fake import FakeIdentityProvider
    from agent_platform.application.teams import TeamService
    from agent_platform.domain.common import generate_database_id
    from agent_platform.domain.teams import TeamMember, TeamMemberRole
    from agent_platform.infrastructure.mysql_teams import MySqlTeamRepository

    database, config = mysql_team_database
    connection_factory = lambda: mysql.connect(**config, database=database)
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    ids = [generate_database_id() for _ in range(6)]
    profile_id = generate_database_id()
    connection = mysql.connect(**config, database=database)
    cursor = connection.cursor()
    try:
        cursor.execute(
            "INSERT INTO execution_profiles (platform_id,tenant_id,profile_id,version,engine_name,model_alias,tool_policy_json,approval_policy_json,context_policy_json,status) "
            "VALUES (%s,%s,%s,1,'fake','fake-model','[]','{}','{}','PUBLISHED')",
            (identity.platform_id, identity.tenant_id, profile_id),
        )
        for index, (employee_id, deployment_id) in enumerate(zip(ids[:3], ids[3:])):
            cursor.execute(
                "INSERT INTO digital_employees (platform_id,tenant_id,employee_id,name,role_name,description,status) VALUES (%s,%s,%s,%s,'分析','测试员工','ACTIVE')",
                (identity.platform_id, identity.tenant_id, employee_id, f"团队员工 {index + 1}"),
            )
            cursor.execute(
                "INSERT INTO digital_employee_versions (platform_id,tenant_id,employee_id,version,system_prompt,execution_profile_id,execution_profile_version,approval_policy_json,status) VALUES (%s,%s,%s,1,'系统指令',%s,1,'{}','PUBLISHED')",
                (identity.platform_id, identity.tenant_id, employee_id, profile_id),
            )
            cursor.execute(
                "INSERT INTO deployments (platform_id,tenant_id,deployment_id,employee_id,employee_version,status) VALUES (%s,%s,%s,%s,1,'ACTIVE')",
                (identity.platform_id, identity.tenant_id, deployment_id, employee_id),
            )
        connection.commit()
    finally:
        cursor.close()
        connection.close()

    service = TeamService(MySqlTeamRepository(connection_factory))
    draft = service.create_draft(identity, "可持久化团队")
    service.add_member(identity, draft.team_id, 1, TeamMember("router-member", ids[0], 1, ids[3], TeamMemberRole.ROUTER, "fake", capabilities=("reasoning",)))
    service.add_member(identity, draft.team_id, 1, TeamMember("finalizer-member", ids[1], 1, ids[4], TeamMemberRole.FINALIZER, "fake", capabilities=("reasoning",)))
    service.add_member(identity, draft.team_id, 1, TeamMember("data-member", ids[2], 1, ids[5], TeamMemberRole.MEMBER, "fake", capabilities=("tool_calls",)))
    service.publish(identity, draft.team_id, 1)

    restarted = TeamService(MySqlTeamRepository(connection_factory))
    next_draft = restarted.fork_revision(identity, draft.team_id, 1)
    restarted.update_policy(identity, draft.team_id, next_draft.version, "ROUTER", "AUTO", max_concurrency=5, delivery_policy="ATTACHMENTS")
    restarted.remove_member(identity, draft.team_id, next_draft.version, "data-member")

    current = TeamService(MySqlTeamRepository(connection_factory)).get_current(identity, draft.team_id)
    original = TeamService(MySqlTeamRepository(connection_factory)).get(identity, draft.team_id, 1)
    selectable_versions = TeamService(MySqlTeamRepository(connection_factory)).list_versions(identity)
    assert current.version == 2 and current.status.value == "DRAFT"
    assert current.orchestration_mode == "ROUTER" and current.approval_mode == "AUTO"
    assert current.max_concurrency == 5 and current.delivery_policy == "ATTACHMENTS"
    assert {member.member_id for member in current.members} == {"router-member", "finalizer-member"}
    assert current.router_member_id == "router-member" and current.finalizer_member_id == "finalizer-member"
    assert original.version == 1 and original.status.value == "PUBLISHED" and len(original.members) == 3
    assert [value.version for value in selectable_versions if value.team_id == draft.team_id] == [2, 1]


def test_mysql_team_task_runs_on_member_session_and_delivers_persistent_artifact(tmp_path: Path, mysql_team_database) -> None:
    import asyncio

    from agent_platform.adapters.identity.fake import FakeIdentityProvider
    from agent_platform.adapters.runtime.fake import FakeRuntimeAdapter
    from agent_platform.application.artifacts import ArtifactService
    from agent_platform.application.catalog_service import CatalogService
    from agent_platform.application.control_plane import ControlPlaneService
    from agent_platform.application.conversations import ConversationService
    from agent_platform.application.orchestration import OrchestrationService
    from agent_platform.application.team_conversations import TeamConversationService
    from agent_platform.application.team_task_execution import TeamTaskExecutionService
    from agent_platform.application.teams import TeamService
    from agent_platform.domain.catalog import SkillType
    from agent_platform.domain.orchestration import TaskStatus
    from agent_platform.domain.teams import TeamMember, TeamMemberRole
    from agent_platform.infrastructure.mysql_artifacts import MySqlArtifactRepository
    from agent_platform.infrastructure.mysql_catalog import MySqlCatalogRepository
    from agent_platform.infrastructure.mysql_conversations import MySqlConversationRepository
    from agent_platform.infrastructure.mysql_execution import MySqlExecutionRepository
    from agent_platform.infrastructure.mysql_orchestration import MySqlOrchestrationRepository
    from agent_platform.infrastructure.mysql_storage import MySqlMediaAssetRepository
    from agent_platform.infrastructure.mysql_teams import MySqlTeamRepository
    from agent_platform.ports.storage import LocalObjectStore
    from agent_platform.workers.run_worker import RunWorker
    from tests._runtime_config_helpers import configure_in_memory_deployment

    database, config = mysql_team_database
    connection_factory = lambda: mysql.connect(**config, database=database)
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    catalog_repository = MySqlCatalogRepository(connection_factory)
    catalog = CatalogService(catalog_repository)
    profile = catalog.create_profile(identity, "fake", "fake-model")
    skill = catalog.create_skill(identity, "团队执行技能", SkillType.INSTRUCTION, "遵循任务指令输出结果。", [])
    catalog.publish_skill(identity, skill.skill_id)
    employee = catalog.create_employee(identity, "团队执行员工", "分析", "团队任务执行测试")
    employee_version = catalog.create_employee_version(
        identity, employee.employee_id, "按任务指令执行并输出简明结果。", [skill.skill_id], [],
        execution_profile_id=profile.profile_id,
    )
    catalog.publish_employee_version(identity, employee.employee_id, employee_version.version)
    deployment = catalog.deploy(identity, employee.employee_id, employee_version.version)
    deployment = configure_in_memory_deployment(catalog, identity, deployment)
    execution_repository = MySqlExecutionRepository(connection_factory)
    conversation_repository = MySqlConversationRepository(connection_factory)
    object_root = tmp_path / "team-task-objects"
    object_store = LocalObjectStore(object_root, metadata_repository=MySqlMediaAssetRepository(connection_factory))
    control_plane = ControlPlaneService(
        catalog, FakeRuntimeAdapter(), persistence=execution_repository,
        object_store=object_store, conversation_repository=conversation_repository,
    )
    team_repository = MySqlTeamRepository(connection_factory)
    teams = TeamService(team_repository)
    draft = teams.create_draft(identity, "持久执行团队")
    teams.add_member(identity, draft.team_id, draft.version, TeamMember("router-member", employee.employee_id, employee_version.version, deployment.deployment_id, TeamMemberRole.ROUTER, "fake", capabilities=("reasoning",)))
    teams.add_member(identity, draft.team_id, draft.version, TeamMember("finalizer-member", employee.employee_id, employee_version.version, deployment.deployment_id, TeamMemberRole.FINALIZER, "fake", capabilities=("reasoning",)))
    teams.publish(identity, draft.team_id, draft.version)
    conversations = ConversationService(conversation_repository, object_store)
    created = TeamConversationService(conversations, teams).create_team_conversation(
        identity, "持久执行验证", draft.team_id, draft.version,
        lambda current_identity, member: control_plane.create_session(current_identity, member.deployment_id).session_id,
    )
    orchestration_repository = MySqlOrchestrationRepository(connection_factory)
    orchestrations = OrchestrationService(orchestration_repository, teams)
    orchestration = orchestrations.create_from_plan(
        identity, created.conversation.conversation_id, draft.team_id, draft.version, "persistent-team-turn",
        [{"client_key": "analyze", "title": "销售分析", "instruction_ref": "汇总趋势并说明结果", "required_capabilities": ["reasoning"], "preferred_member_id": "router-member"}],
    )
    orchestrations.approve_plan(identity, orchestration.orchestration_id)
    artifact_repository = MySqlArtifactRepository(connection_factory)
    artifact_service = ArtifactService(artifact_repository, object_store)
    executor = TeamTaskExecutionService(teams, orchestrations, conversations, control_plane, artifact_service)
    try:
        assert executor.start_ready(identity, orchestration.orchestration_id) == ()
        worker = RunWorker(
            identity.platform_id, "run-worker-persistent-team", execution_repository,
            control_plane, object_store, lease_seconds=30, poll_interval_seconds=0.01, engine_names=("fake",),
        )
        assert asyncio.run(worker.run_once()) is True
        executor.reconcile_conversation(identity, created.conversation.conversation_id)
    finally:
        executor.close()

    restarted_orchestrations = MySqlOrchestrationRepository(connection_factory)
    task = restarted_orchestrations.list_tasks(identity, orchestration.orchestration_id).items[0]
    attempt = restarted_orchestrations.list_attempts(identity, task.task_id)[0]
    restarted_control_plane = ControlPlaneService(
        CatalogService(MySqlCatalogRepository(connection_factory)), FakeRuntimeAdapter(),
        persistence=MySqlExecutionRepository(connection_factory), object_store=object_store,
        conversation_repository=conversation_repository,
    )
    run = restarted_control_plane.get_run(identity, attempt.run_id or "")
    restarted_objects = LocalObjectStore(object_root, metadata_repository=MySqlMediaAssetRepository(connection_factory))
    restarted_artifacts = ArtifactService(MySqlArtifactRepository(connection_factory), restarted_objects)
    restarted_conversations = ConversationService(MySqlConversationRepository(connection_factory), restarted_objects)
    messages = restarted_conversations.list_messages(identity, created.conversation.conversation_id)

    assert task.status is TaskStatus.SUCCEEDED, task.error_code
    assert attempt.status == "SUCCEEDED" and attempt.worker_pool == "fake-pool"
    assert run.orchestration_id == orchestration.orchestration_id and run.task_id == task.task_id
    assert task.output_ref
    assert restarted_artifacts.read_preview(identity, task.output_ref)
    assert messages[-1].source.value == "agent" and messages[-1].text
    assert messages[-1].sender_name == "团队执行员工"
