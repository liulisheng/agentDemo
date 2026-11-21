"""MySQL Session/Run 持久化与新仓储实例读取集成测试。"""

from __future__ import annotations

import os
import secrets
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Barrier

import pytest

mysql = pytest.importorskip("mysql.connector")


class _RedactedMysqlConfig(dict[str, object]):
    """避免集成失败时由 pytest repr 泄露连接口令。"""

    def __repr__(self) -> str:
        safe = {key: ("<已隐藏>" if key == "password" else value) for key, value in self.items()}
        return repr(safe)


@pytest.fixture
def mysql_execution_database():
    if os.environ.get("AGENT_PLATFORM_MYSQL_TEST_ALLOW_CREATE_DATABASE") != "1":
        pytest.skip("set AGENT_PLATFORM_MYSQL_TEST_ALLOW_CREATE_DATABASE=1 for disposable MySQL integration")
    database = f"agent_platform_execution_test_{secrets.token_hex(5)}"
    config = _RedactedMysqlConfig({
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
            cursor.execute("INSERT INTO users (platform_id,tenant_id,user_id,external_subject,username,display_name,status) VALUES ('plt-local','tenant-a','admin-local','subject-local','admin-local','管理员','ACTIVE')")
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


def test_session_and_run_snapshots_survive_repository_recreation(mysql_execution_database) -> None:
    from agent_platform.adapters.identity.fake import FakeIdentityProvider
    from agent_platform.adapters.runtime.fake import FakeRuntimeAdapter
    from agent_platform.application.catalog_service import CatalogService
    from agent_platform.application.control_plane import ControlPlaneService
    from agent_platform.domain.catalog import SkillType
    from agent_platform.domain.common import DomainError, generate_database_id
    from agent_platform.domain.execution import RuntimeStatus
    from agent_platform.domain.observability import SpanType, TraceSpan
    from agent_platform.domain.usage import (
        ModelCallRecord,
        ToolInvocationRecord,
        UsageAdjustment,
        UsageStatus,
    )
    from agent_platform.infrastructure.mysql_catalog import MySqlCatalogRepository
    from agent_platform.infrastructure.mysql_execution import MySqlExecutionRepository

    database, config = mysql_execution_database
    identity = FakeIdentityProvider().resolve_identity("admin-local")

    def catalog_service() -> CatalogService:
        return CatalogService(MySqlCatalogRepository(lambda: mysql.connect(**config, database=database)))

    execution_repository = MySqlExecutionRepository(lambda: mysql.connect(**config, database=database))
    catalog = catalog_service()
    from agent_platform.application.model_routes import ModelRouteService

    route = ModelRouteService(catalog.repository).create(
        identity, "local-route", "fake-model", "fake", "http://fake.invalid", None
    )
    profile = catalog.create_profile(identity, "fake", "local-route", model_route_id=route.route_id)
    skill = catalog.create_skill(identity, "本地技能", SkillType.INSTRUCTION, "回答用户", [])
    catalog.publish_skill(identity, skill.skill_id)
    employee = catalog.create_employee(identity, "本地助手", "支持", "本地测试")
    version = catalog.create_employee_version(
        identity,
        employee.employee_id,
        "系统指令",
        [skill.skill_id],
        [],
        execution_profile_id=profile.profile_id,
    )
    published = catalog.publish_employee_version(identity, employee.employee_id, version.version)
    deployment = catalog.deploy(identity, employee.employee_id, published.version)

    control_plane = ControlPlaneService(catalog, FakeRuntimeAdapter(), persistence=execution_repository)
    session = control_plane.create_session(identity, deployment.deployment_id)
    run = control_plane.create_run(identity, session.session_id, "你好", "persistent-run-1")

    restarted = ControlPlaneService(catalog_service(), FakeRuntimeAdapter(), persistence=execution_repository)
    loaded_session = restarted.get_session(identity, session.session_id)
    loaded_run = restarted.get_run(identity, run.run_id)
    assert session.harness_home_ref.startswith("harness-session:")
    assert loaded_session.harness_home_ref == session.harness_home_ref
    assert loaded_session.deployment_id == deployment.deployment_id
    assert loaded_session.employee_version == 1
    assert loaded_session.trace_id == run.trace_id
    assert loaded_session.model_route_id == route.route_id
    assert loaded_run.snapshot.employee_id == employee.employee_id
    assert loaded_run.snapshot.employee_version == 1
    assert loaded_run.submitted_by_user_id == identity.user_id
    assert loaded_run.snapshot.model_route_id == route.route_id
    assert loaded_run.snapshot.skill_version_refs == ((skill.skill_id, 1),)
    assert loaded_run.runtime_reference is not None
    assert loaded_run.runtime_reference.runtime_session_ref
    timeline = restarted.get_trace(identity, run.run_id)
    assert timeline.usage.total_tokens > 0
    assert any(span.span_type is SpanType.SESSION for span in timeline.spans)
    assert any(span.span_type is SpanType.RESULT for span in timeline.spans)
    assert execution_repository.list_usage_records(identity, run.run_id)[0].model_route_id == route.route_id
    same_run = restarted.create_run(identity, session.session_id, "你好", "persistent-run-1")
    assert same_run.run_id == run.run_id
    with pytest.raises(DomainError) as api_conflict:
        restarted.create_run(identity, session.session_id, "不同请求", "persistent-run-1")
    assert api_conflict.value.code == "IDEMPOTENCY_CONFLICT"

    same_run, created = execution_repository.submit_run(identity, run, "persistent-run-1")
    assert not created
    assert same_run.run_id == run.run_id
    with pytest.raises(DomainError) as conflict:
        execution_repository.submit_run(identity, replace(run, prompt_digest="different-digest"), "persistent-run-1")
    assert conflict.value.code == "IDEMPOTENCY_CONFLICT"

    barrier = Barrier(6)

    def concurrent_submit(index: int):
        barrier.wait(timeout=5)
        candidate = replace(run, run_id=generate_database_id())
        return execution_repository.submit_run(identity, candidate, f"concurrent-idem-{index // 6}")

    with ThreadPoolExecutor(max_workers=6) as executor:
        concurrent = list(executor.map(concurrent_submit, range(6)))
    assert len({submitted.run_id for submitted, _ in concurrent}) == 1
    assert sum(1 for _, was_created in concurrent if was_created) == 1

    queued = replace(
        run,
        run_id=generate_database_id(),
        status=RuntimeStatus.UNKNOWN,
        runtime_reference=None,
    )
    queued, created = execution_repository.submit_run(identity, queued, "persistent-run-2")
    assert created
    token = execution_repository.acquire_lease(identity, queued.run_id, "worker-old")
    assert token == 1
    assert execution_repository.heartbeat(identity, queued.run_id, token)
    connection = mysql.connect(**config, database=database)
    cursor = connection.cursor()
    try:
        cursor.execute(
            "UPDATE runs SET fencing_token=fencing_token+1 "
            "WHERE platform_id=%s AND tenant_id=%s AND run_id=%s",
            (identity.platform_id, identity.tenant_id, queued.run_id),
        )
        cursor.execute(
            "UPDATE session_runtime_leases SET state='QUARANTINED',worker_id=NULL,"
            "lease_expires_at=NULL,fencing_token=fencing_token+1 "
            "WHERE platform_id=%s AND tenant_id=%s AND run_id=%s AND state='ACTIVE'",
            (identity.platform_id, identity.tenant_id, queued.run_id),
        )
        cursor.execute(
            "UPDATE sessions SET status='INTERRUPTED' WHERE platform_id=%s "
            "AND tenant_id=%s AND session_id=%s",
            (identity.platform_id, identity.tenant_id, session.session_id),
        )
        connection.commit()
    finally:
        cursor.close()
        connection.close()
    stale_span = TraceSpan(
        span_id=generate_database_id(),
        trace_id=queued.trace_id,
        parent_span_id=None,
        run_id=queued.run_id,
        span_type=SpanType.RUNTIME,
        status="succeeded",
        sequence=100,
    )
    assert not execution_repository.heartbeat(identity, queued.run_id, token)
    assert not execution_repository.append_trace_span(identity, stale_span, token)
    assert not execution_repository.finish_run(identity, queued, token)

    usage = ModelCallRecord(
        call_id=f"model-{run.run_id}",
        run_id=run.run_id,
        tenant_id=identity.tenant_id,
        user_id=identity.user_id,
        employee_id=employee.employee_id,
        session_id=session.session_id,
        input_tokens=2,
        output_tokens=3,
        total_tokens=5,
        usage_status=UsageStatus.ESTIMATED,
        provider_request_id=None,
        cost_amount=0.01,
        currency="CNY",
        pricing_version="fake-v1",
    )
    original = execution_repository.get_usage_record(identity, run.run_id)
    manual_usage = replace(usage, call_id=f"zmanual-{generate_database_id()}")
    execution_repository.record(identity, manual_usage)
    with pytest.raises(DomainError) as immutable_usage:
        execution_repository.record(identity, manual_usage)
    assert immutable_usage.value.code == "CONFLICT"
    execution_repository.add_adjustment(
        identity,
        UsageAdjustment(generate_database_id(), usage.call_id, "供应商账单更正", 0.02, 1),
    )
    assert execution_repository.get_usage_record(identity, run.run_id) == original
    assert execution_repository.adjustments(identity, usage.call_id)[0].delta_tokens == 1

    start_sequence = max(
        span.sequence for span in execution_repository.list_session_trace(identity, session.session_id)
    ) + 1
    for sequence in range(start_sequence, start_sequence + 3):
        execution_repository.append(
            identity,
            run.run_id,
            TraceSpan(
                span_id=generate_database_id(),
                trace_id=run.trace_id,
                parent_span_id=None,
                run_id=run.run_id,
                span_type=SpanType.RUNTIME,
                status="succeeded",
                sequence=sequence,
                attributes={"step": sequence},
            ),
        )
    first_page = execution_repository.list_trace_spans(
        identity, run.run_id, after_sequence=start_sequence - 1, limit=2
    )
    second_page = execution_repository.list_trace_spans(
        identity, run.run_id, after_sequence=first_page[-1].sequence, limit=2
    )
    assert [span.sequence for span in first_page] == [start_sequence, start_sequence + 1]
    assert [span.sequence for span in second_page] == [start_sequence + 2]

    tool_session = control_plane.create_session(identity, deployment.deployment_id)
    tool_run, created = execution_repository.submit_run(
        identity,
        replace(
            queued,
            run_id=generate_database_id(),
            session_id=tool_session.session_id,
            prompt_digest="tool-invocation-digest",
        ),
        "persistent-run-3",
    )
    assert created
    tool_token = execution_repository.acquire_lease(identity, tool_run.run_id, "tool-worker")
    assert tool_token == 1
    assert execution_repository.record_tool_invocation(
        identity,
        ToolInvocationRecord(
            invocation_id=generate_database_id(),
            run_id=tool_run.run_id,
            tenant_id=identity.tenant_id,
            tool_name="echo",
            protocol="builtin",
            version="1",
            status="succeeded",
        ),
        tool_token,
    )
    assert execution_repository.list_tool_invocations(identity, tool_run.run_id)[0].tool_name == "echo"


def test_expired_runtime_lease_becomes_unknown_without_replay(mysql_execution_database) -> None:
    from hashlib import sha256

    from agent_platform.adapters.identity.fake import FakeIdentityProvider
    from agent_platform.application.catalog_service import CatalogService
    from agent_platform.application.control_plane import ControlPlaneService
    from agent_platform.domain.catalog import SkillType
    from agent_platform.domain.common import generate_database_id
    from agent_platform.domain.execution import RuntimeStatus
    from agent_platform.domain.observability import RunRecord
    from agent_platform.infrastructure.mysql_catalog import MySqlCatalogRepository
    from agent_platform.infrastructure.mysql_execution import MySqlExecutionRepository

    database, config = mysql_execution_database
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    catalog = CatalogService(MySqlCatalogRepository(lambda: mysql.connect(**config, database=database)))
    skill = catalog.create_skill(identity, "技能", SkillType.INSTRUCTION, "回答", [])
    catalog.publish_skill(identity, skill.skill_id)
    employee = catalog.create_employee(identity, "助手", "支持", "未知结果恢复验证")
    version = catalog.create_employee_version(identity, employee.employee_id, "系统指令", [skill.skill_id], [])
    catalog.publish_employee_version(identity, employee.employee_id, version.version)
    deployment = catalog.deploy(identity, employee.employee_id, version.version)

    repository = MySqlExecutionRepository(lambda: mysql.connect(**config, database=database))
    session = ControlPlaneService(catalog, _CountingRuntime(), persistence=repository).create_session(
        identity, deployment.deployment_id
    )
    template = RunRecord(
        run_id=generate_database_id(),
        session_id=session.session_id,
        platform_id=identity.platform_id,
        tenant_id=identity.tenant_id,
        trace_id=session.trace_id,
        prompt_digest=sha256("相同内容".encode()).hexdigest(),
        snapshot=ControlPlaneService(catalog, _CountingRuntime()).agent_resolver.snapshot(
            identity, catalog.resolve_deployment(identity, deployment.deployment_id)
        ),
        status=RuntimeStatus.UNKNOWN,
    )
    pending, created = repository.submit_run(identity, template, "lost-runtime-result")
    assert created
    old_token = repository.acquire_lease(identity, pending.run_id, "worker-lost", lease_seconds=30)
    assert old_token == 1
    connection = mysql.connect(**config, database=database)
    cursor = connection.cursor()
    try:
        cursor.execute(
            "UPDATE runs SET lease_expires_at=DATE_SUB(CURRENT_TIMESTAMP(6), INTERVAL 1 SECOND) "
            "WHERE platform_id=%s AND tenant_id=%s AND run_id=%s",
            (identity.platform_id, identity.tenant_id, pending.run_id),
        )
        connection.commit()
    finally:
        cursor.close()
        connection.close()

    assert repository.acquire_lease(identity, pending.run_id, "worker-next") is None
    assert repository.get_run(identity, pending.run_id).status is RuntimeStatus.UNKNOWN
    assert not repository.heartbeat(identity, pending.run_id, old_token)
    assert not repository.finish_run(identity, pending, old_token)

    runtime = _CountingRuntime()
    restarted = ControlPlaneService(catalog, runtime, persistence=repository)
    repeated = restarted.create_run(
        identity, session.session_id, "相同内容", "lost-runtime-result"
    )
    assert repeated.run_id == pending.run_id
    assert repeated.status is RuntimeStatus.UNKNOWN
    assert runtime.calls == 0


class _CountingRuntime:
    """测试未知 Runtime 结果不会触发隐式重放。"""

    def __init__(self) -> None:
        self.calls = 0

    def run(self, request):
        self.calls += 1
        raise AssertionError("不确定结果的 Run 不得被自动重放")


@pytest.mark.parametrize("failure_stage", ["after_run_update", "after_trace_spans", "after_usage"])
def test_run_result_transaction_rolls_back_each_write_stage(mysql_execution_database, failure_stage: str) -> None:
    from agent_platform.adapters.identity.fake import FakeIdentityProvider
    from agent_platform.adapters.runtime.fake import FakeRuntimeAdapter
    from agent_platform.application.catalog_service import CatalogService
    from agent_platform.application.control_plane import ControlPlaneService
    from agent_platform.domain.catalog import SkillType
    from agent_platform.domain.common import DomainError
    from agent_platform.domain.execution import RuntimeStatus
    from agent_platform.infrastructure.mysql_catalog import MySqlCatalogRepository
    from agent_platform.infrastructure.mysql_execution import MySqlExecutionRepository

    database, config = mysql_execution_database
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    catalog = CatalogService(MySqlCatalogRepository(lambda: mysql.connect(**config, database=database)))
    skill = catalog.create_skill(identity, "技能", SkillType.INSTRUCTION, "回答", [])
    catalog.publish_skill(identity, skill.skill_id)
    employee = catalog.create_employee(identity, "助手", "支持", "用于事务回滚验证")
    version = catalog.create_employee_version(identity, employee.employee_id, "系统指令", [skill.skill_id], [])
    catalog.publish_employee_version(identity, employee.employee_id, version.version)
    deployment = catalog.deploy(identity, employee.employee_id, version.version)

    def inject(stage: str) -> None:
        if stage == failure_stage:
            raise RuntimeError(f"injected failure: {stage}")

    repository = MySqlExecutionRepository(
        lambda: mysql.connect(**config, database=database), failure_injector=inject
    )
    control_plane = ControlPlaneService(catalog, FakeRuntimeAdapter(), persistence=repository)
    session = control_plane.create_session(identity, deployment.deployment_id)
    with pytest.raises(RuntimeError, match=failure_stage):
        control_plane.create_run(identity, session.session_id, "验证事务", f"fault-{failure_stage}")

    connection = mysql.connect(**config, database=database)
    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT run_id, state FROM runs WHERE platform_id=%s AND tenant_id=%s AND session_id=%s",
            (identity.platform_id, identity.tenant_id, session.session_id),
        )
        run_id, state = cursor.fetchone()
        assert state == "UNKNOWN"
        cursor.execute(
            "SELECT COUNT(*) FROM platform_trace_spans WHERE platform_id=%s AND tenant_id=%s",
            (identity.platform_id, identity.tenant_id),
        )
        assert cursor.fetchone()[0] == 0
        cursor.execute(
            "SELECT COUNT(*) FROM usage_ledger WHERE platform_id=%s AND tenant_id=%s",
            (identity.platform_id, identity.tenant_id),
        )
        assert cursor.fetchone()[0] == 0
        cursor.execute(
            "SELECT state FROM session_runtime_leases WHERE platform_id=%s AND tenant_id=%s "
            "AND session_id=%s",
            (identity.platform_id, identity.tenant_id, session.session_id),
        )
        assert cursor.fetchone()[0] == "QUARANTINED"
        cursor.execute(
            "SELECT status FROM sessions WHERE platform_id=%s AND tenant_id=%s AND session_id=%s",
            (identity.platform_id, identity.tenant_id, session.session_id),
        )
        assert cursor.fetchone()[0] == "INTERRUPTED"
    finally:
        cursor.close()
        connection.close()
    assert repository.get_run(identity, str(run_id)).status is RuntimeStatus.UNKNOWN
    assert control_plane.get_session(identity, session.session_id).status == "INTERRUPTED"
    with pytest.raises(DomainError) as interrupted:
        control_plane.prepare_conversation_turn(
            identity, session.session_id, "conversation-interrupted", "开始新一轮"
        )
    assert interrupted.value.code == "INVALID_STATE"


def test_mysql_mcp_repository_scopes_profiles_and_consumes_one_approval(mysql_execution_database) -> None:
    import json
    from concurrent.futures import ThreadPoolExecutor
    from datetime import UTC, datetime, timedelta
    from threading import Barrier

    from agent_platform.adapters.identity.fake import FakeIdentityProvider
    from agent_platform.domain.catalog import ToolVersion
    from agent_platform.domain.common import DomainError, generate_database_id
    from agent_platform.domain.execution import RuntimeReference
    from agent_platform.domain.identity import IdentityContext
    from agent_platform.domain.mcp import (
        McpProfileStatus,
        McpServerProfile,
        ToolApprovalRequest,
        ToolApprovalState,
    )
    from agent_platform.domain.observability import SpanType, TraceSpan
    from agent_platform.infrastructure.mysql_catalog import MySqlCatalogRepository
    from agent_platform.infrastructure.mysql_mcp import MySqlMcpRepository

    database, config = mysql_execution_database
    requester = FakeIdentityProvider().resolve_identity("admin-local")
    repository = MySqlMcpRepository(lambda: mysql.connect(**config, database=database))
    catalog_repository = MySqlCatalogRepository(
        lambda: mysql.connect(**config, database=database)
    )
    profile = McpServerProfile(
        platform_id=requester.platform_id,
        tenant_id=requester.tenant_id,
        profile_id=generate_database_id(),
        version=1,
        name="DolphinScheduler MCP",
        transport="stdio",
        command="/usr/local/bin/python3",
        args=("-m", "dolphinscheduler_mcp"),
        working_directory_ref="workspace://dolphin-mcp",
        environment_secret_refs={"DOLPHIN_TOKEN": "secret://tenant-a/dolphin"},
        enabled_tool_names=("workflow_preview", "workflow_create"),
        tool_catalog_sha256="a" * 64,
    )
    repository.save_profile(requester, profile)
    repository.set_profile_status(
        requester, profile.profile_id, profile.version, McpProfileStatus.ACTIVE
    )
    assert repository.get_profile(requester, profile.profile_id, profile.version).status is McpProfileStatus.ACTIVE

    schema = {
        "type": "object",
        "properties": {"project_id": {"type": "string"}},
        "required": ["project_id"],
    }
    tool = ToolVersion(
        tool_id=generate_database_id(),
        tenant_id=requester.tenant_id,
        name="create_workflow",
        description="创建 OFFLINE 工作流",
        input_schema=schema,
        protocol="mcp",
        mcp_server_profile_id=profile.profile_id,
        mcp_server_profile_version=profile.version,
        remote_tool_name="workflow_create",
        mcp_risk_level="WRITE",
        preview_tool_name="workflow_preview",
    )
    catalog_repository.save_tool(requester, tool)
    loaded_tool = catalog_repository.get_tool_version(
        requester, tool.tool_id, tool.version
    )
    assert loaded_tool.mcp_server_profile_id == profile.profile_id
    assert loaded_tool.remote_tool_name == "workflow_create"
    assert loaded_tool.input_schema_sha256 == tool.input_schema_sha256

    session_id = generate_database_id()
    run_id = generate_database_id()
    conversation_id = generate_database_id()
    invocation_id = generate_database_id()
    connection = mysql.connect(**config, database=database)
    cursor = connection.cursor()
    try:
        cursor.execute(
            "INSERT INTO users "
            "(platform_id,tenant_id,user_id,external_subject,username,display_name,status) "
            "VALUES (%s,%s,'member-peer','subject-peer','member-peer','同租户成员','ACTIVE')",
            (requester.platform_id, requester.tenant_id),
        )
        cursor.execute(
            "INSERT INTO conversations "
            "(platform_id,tenant_id,conversation_id,title,is_group,status,created_by) "
            "VALUES (%s,%s,%s,'MCP 审批测试',FALSE,'ACTIVE',%s)",
            (requester.platform_id, requester.tenant_id, conversation_id, requester.user_id),
        )
        cursor.execute(
            "INSERT INTO sessions "
            "(platform_id,tenant_id,session_id,user_id,engine_name,engine_session_id,status) "
            "VALUES (%s,%s,%s,%s,'deepseek','runtime-mcp-test','ACTIVE')",
            (requester.platform_id, requester.tenant_id, session_id, requester.user_id),
        )
        cursor.execute(
            "INSERT INTO runs "
            "(platform_id,tenant_id,run_id,session_id,idempotency_key,request_digest,state,"
            "worker_id,fencing_token,lease_expires_at,submitted_by_user_id) "
            "VALUES (%s,%s,%s,%s,%s,%s,'RUNNING','run-worker',7,"
            "DATE_ADD(CURRENT_TIMESTAMP(6), INTERVAL 60 SECOND),%s)",
            (
                requester.platform_id,
                requester.tenant_id,
                run_id,
                session_id,
                "mcp-run",
                "b" * 64,
                requester.user_id,
            ),
        )
        cursor.execute(
            "UPDATE runs SET trace_id='trace-mcp' WHERE platform_id=%s AND tenant_id=%s AND run_id=%s",
            (requester.platform_id, requester.tenant_id, run_id),
        )
        cursor.execute(
            "INSERT INTO session_runtime_leases "
            "(platform_id,tenant_id,session_id,run_id,worker_id,fencing_token,lease_expires_at,state) "
            "VALUES (%s,%s,%s,%s,'run-worker',7,DATE_ADD(CURRENT_TIMESTAMP(6), INTERVAL 60 SECOND),'ACTIVE')",
            (requester.platform_id, requester.tenant_id, session_id, run_id),
        )
        connection.commit()
    finally:
        cursor.close()
        connection.close()

    now = datetime.now(UTC)
    approval = ToolApprovalRequest(
        platform_id=requester.platform_id,
        tenant_id=requester.tenant_id,
        approval_id=generate_database_id(),
        run_id=run_id,
        session_id=session_id,
        conversation_id=conversation_id,
        requested_by=requester.user_id,
        tool_invocation_id=invocation_id,
        tool_id=tool.tool_id,
        tool_version=tool.version,
        mcp_server_profile_id=profile.profile_id,
        mcp_server_profile_version=profile.version,
        arguments_ref="obj://tenant-a/arguments",
        arguments_sha256="c" * 64,
        preview_ref="obj://tenant-a/preview",
        preview_sha256="d" * 64,
        expires_at=now + timedelta(minutes=5),
    )
    worker_a = IdentityContext(
        requester.platform_id, requester.tenant_id, "worker-a", frozenset(), auth_source="worker"
    )
    worker_b = IdentityContext(
        requester.platform_id, requester.tenant_id, "worker-b", frozenset(), auth_source="worker"
    )
    pending = repository.create_tool_approval(
        worker_a,
        approval,
        run_fencing_token=7,
        trace_spans=(
            TraceSpan(
                invocation_id,
                "trace-mcp",
                None,
                run_id,
                SpanType.TOOL,
                "running",
                {"event_type": "tool/call", "call_id": "call-mcp"},
                1,
            ),
        ),
        runtime_reference=RuntimeReference("deepseek", "runtime-session-after-preview", "runtime-result"),
    )
    assert pending.state is ToolApprovalState.PENDING
    connection = mysql.connect(**config, database=database)
    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT status,attributes_json FROM platform_trace_spans "
            "WHERE platform_id=%s AND tenant_id=%s AND run_id=%s AND span_id=%s",
            (requester.platform_id, requester.tenant_id, run_id, invocation_id),
        )
        trace_span = cursor.fetchone()
        attributes = trace_span[1] if isinstance(trace_span[1], dict) else json.loads(trace_span[1])
        assert trace_span[0] == "awaiting_approval"
        assert attributes["approval_ref"] == approval.approval_id
        cursor.execute(
            "SELECT COUNT(*) FROM session_runtime_leases WHERE platform_id=%s AND tenant_id=%s AND run_id=%s",
            (requester.platform_id, requester.tenant_id, run_id),
        )
        assert cursor.fetchone()[0] == 0
        cursor.execute(
            "SELECT runtime_session_ref,runtime_result_ref FROM runs "
            "WHERE platform_id=%s AND tenant_id=%s AND run_id=%s",
            (requester.platform_id, requester.tenant_id, run_id),
        )
        assert cursor.fetchone() == ("runtime-session-after-preview", "runtime-result")
    finally:
        cursor.close()
        connection.close()

    peer = IdentityContext(
        requester.platform_id, requester.tenant_id, "member-peer", frozenset({"member"})
    )
    with pytest.raises(DomainError) as denied:
        repository.approve_tool_approval(
            peer,
            approval.approval_id,
            approval.preview_sha256,
            idempotency_key="confirm-mcp",
            now=now,
        )
    assert denied.value.code == "AUTHORIZATION_DENIED"

    approved = repository.approve_tool_approval(
        requester,
        approval.approval_id,
        approval.preview_sha256,
        idempotency_key="confirm-mcp",
        now=now,
    )
    assert approved.state is ToolApprovalState.APPROVED
    assert repository.approve_tool_approval(
        requester,
        approval.approval_id,
        approval.preview_sha256,
        idempotency_key="confirm-mcp",
        now=now,
    ) == approved

    barrier = Barrier(2)

    def claim(identity: IdentityContext):
        barrier.wait(timeout=5)
        return repository.claim_approved_tool_approval(
            identity, identity.user_id, lease_seconds=30
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(executor.map(claim, (worker_a, worker_b)))
    winners = [claim_result for claim_result in claims if claim_result is not None]
    assert len(winners) == 1
    action = winners[0]
    assert repository.finish_tool_approval(
        worker_a if action.worker_id == "worker-a" else worker_b,
        approval.approval_id,
        action.worker_id or "",
        action.fencing_token,
        ToolApprovalState.SUCCEEDED,
        now=datetime.now(UTC),
        result_ref="obj://tenant/approval-result",
        result_sha256="f" * 64,
    )

    connection = mysql.connect(**config, database=database)
    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT state, result_ref FROM runs WHERE platform_id=%s AND tenant_id=%s AND run_id=%s",
            (requester.platform_id, requester.tenant_id, run_id),
        )
        assert cursor.fetchone() == ("SUCCEEDED", "obj://tenant/approval-result")
        cursor.execute(
            "SELECT state, approved_by, requested_by FROM tool_approval_requests "
            "WHERE platform_id=%s AND tenant_id=%s AND approval_id=%s",
            (requester.platform_id, requester.tenant_id, approval.approval_id),
        )
        state, approved_by, requested_by = cursor.fetchone()
        assert (state, approved_by, requested_by) == (
            "SUCCEEDED",
            requester.user_id,
            requester.user_id,
        )
        cursor.execute(
            "SELECT JSON_UNQUOTE(JSON_EXTRACT(attributes_json,'$.tool_result_ref')), "
            "JSON_UNQUOTE(JSON_EXTRACT(attributes_json,'$.tool_result_sha256')) "
            "FROM platform_trace_spans WHERE platform_id=%s AND tenant_id=%s AND span_id=%s",
            (requester.platform_id, requester.tenant_id, invocation_id),
        )
        assert cursor.fetchone() == ("obj://tenant/approval-result", "f" * 64)
    finally:
        cursor.close()
        connection.close()


def test_mcp_profile_and_approval_are_tenant_scoped_and_single_use(mysql_execution_database) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from datetime import UTC, datetime, timedelta
    from threading import Barrier

    from agent_platform.adapters.identity.fake import FakeIdentityProvider
    from agent_platform.adapters.runtime.fake import FakeRuntimeAdapter
    from agent_platform.application.catalog_service import CatalogService
    from agent_platform.application.control_plane import ControlPlaneService
    from agent_platform.domain.catalog import SkillType
    from agent_platform.domain.common import DomainError, generate_database_id
    from agent_platform.domain.identity import IdentityContext
    from agent_platform.domain.mcp import (
        McpProfileStatus,
        McpServerProfile,
        ToolApprovalRequest,
        ToolApprovalState,
    )
    from agent_platform.infrastructure.mysql_catalog import MySqlCatalogRepository
    from agent_platform.infrastructure.mysql_conversations import MySqlConversationRepository
    from agent_platform.infrastructure.mysql_execution import MySqlExecutionRepository
    from agent_platform.infrastructure.mysql_mcp import MySqlMcpRepository

    database, config = mysql_execution_database
    admin = FakeIdentityProvider().resolve_identity("admin-local")
    worker = IdentityContext(
        platform_id=admin.platform_id,
        tenant_id=admin.tenant_id,
        user_id="worker-local",
        roles=frozenset(),
        auth_source="worker",
    )
    connection_factory = lambda: mysql.connect(**config, database=database)
    catalog = CatalogService(MySqlCatalogRepository(connection_factory))
    skill = catalog.create_skill(
        admin, "审批列表技能", SkillType.INSTRUCTION, "读取工作流信息", []
    )
    catalog.publish_skill(admin, skill.skill_id)
    employee = catalog.create_employee(admin, "审批列表员工", "数仓开发", "审批列表聚合")
    employee_version = catalog.create_employee_version(
        admin, employee.employee_id, "数仓系统指令", [skill.skill_id], []
    )
    catalog.publish_employee_version(admin, employee.employee_id, employee_version.version)
    deployment = catalog.deploy(admin, employee.employee_id, employee_version.version)
    session = ControlPlaneService(
        catalog, FakeRuntimeAdapter(), persistence=MySqlExecutionRepository(connection_factory)
    ).create_session(admin, deployment.deployment_id)
    profile_id = generate_database_id()
    tool_id = generate_database_id()
    session_id = session.session_id
    run_id = generate_database_id()
    conversation_id = generate_database_id()
    invocation_id = generate_database_id()
    profile = McpServerProfile(
        platform_id=admin.platform_id,
        tenant_id=admin.tenant_id,
        profile_id=profile_id,
        version=1,
        name="本地 Dolphin MCP",
        transport="stdio",
        command="/usr/local/bin/python3",
        args=("-m", "dolphinscheduler_mcp"),
        working_directory_ref="workspace://mcp-test",
        enabled_tool_names=("workflow_preview", "workflow_create"),
        tool_catalog_sha256="a" * 64,
    )
    repository = MySqlMcpRepository(lambda: mysql.connect(**config, database=database))
    repository.save_profile(admin, profile)
    active_profile = repository.set_profile_status(
        admin, profile_id, 1, McpProfileStatus.ACTIVE
    )
    assert repository.get_profile(admin, profile_id, 1) == active_profile

    connection = mysql.connect(**config, database=database)
    cursor = connection.cursor()
    try:
        cursor.execute(
            "INSERT INTO users "
            "(platform_id,tenant_id,user_id,external_subject,username,display_name,status) "
            "VALUES (%s,%s,'member-peer','subject-peer','member-peer','同租户成员','ACTIVE')",
            (admin.platform_id, admin.tenant_id),
        )
        cursor.execute(
            "INSERT INTO conversations "
            "(platform_id, tenant_id, conversation_id, title, is_group, status, created_by) "
            "VALUES (%s,%s,%s,'MCP 审批测试',FALSE,'ACTIVE',%s)",
            (admin.platform_id, admin.tenant_id, conversation_id, admin.user_id),
        )
        cursor.execute(
            "INSERT INTO conversation_participants "
            "(platform_id,tenant_id,conversation_id,user_id,role) VALUES (%s,%s,%s,%s,'owner')",
            (admin.platform_id, admin.tenant_id, conversation_id, admin.user_id),
        )
        cursor.execute(
            "INSERT INTO agent_bindings "
            "(platform_id,tenant_id,binding_id,conversation_id,employee_id,employee_version,"
            "deployment_id,session_id,status) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'ACTIVE')",
            (
                admin.platform_id,
                admin.tenant_id,
                generate_database_id(),
                conversation_id,
                employee.employee_id,
                employee_version.version,
                deployment.deployment_id,
                session_id,
            ),
        )
        cursor.execute(
            "INSERT INTO runs "
            "(platform_id,tenant_id,run_id,session_id,idempotency_key,request_digest,state,"
            "worker_id,fencing_token,lease_expires_at,submitted_by_user_id) "
            "VALUES (%s,%s,%s,%s,'mcp-run',%s,'RUNNING','run-worker',7,"
            "DATE_ADD(CURRENT_TIMESTAMP(6), INTERVAL 60 SECOND),%s)",
            (admin.platform_id, admin.tenant_id, run_id, session_id, "b" * 64, admin.user_id),
        )
        cursor.execute(
            "INSERT INTO tool_versions "
            "(platform_id,tenant_id,tool_id,version,name,protocol,description,input_schema_json,"
            "output_schema_json,timeout_ms,retry_policy_json,risk_policy,status,"
            "mcp_server_profile_id,mcp_server_profile_version,remote_tool_name,input_schema_sha256,"
            "mcp_risk_level,preview_tool_name,idempotency_support) "
            "VALUES (%s,%s,%s,1,'workflow_create','mcp','创建离线工作流',"
            "'{\"type\":\"object\"}','{\"type\":\"object\"}',30000,'{\"max_retries\":0}',"
            "'low','PUBLISHED',%s,1,'workflow_create',%s,'WRITE','workflow_preview',FALSE)",
            (
                admin.platform_id,
                admin.tenant_id,
                tool_id,
                profile_id,
                "c" * 64,
            ),
        )
        cursor.execute(
            "INSERT INTO platform_trace_spans "
            "(platform_id,tenant_id,run_id,span_id,trace_id,span_type,status,sequence_no,attributes_json) "
            "VALUES (%s,%s,%s,%s,'trace-mcp','tool','awaiting_approval',1,'{}')",
            (admin.platform_id, admin.tenant_id, run_id, invocation_id),
        )
        connection.commit()
    finally:
        cursor.close()
        connection.close()

    expires_at = datetime.now(UTC) + timedelta(minutes=5)
    approval = ToolApprovalRequest(
        platform_id=admin.platform_id,
        tenant_id=admin.tenant_id,
        approval_id=generate_database_id(),
        run_id=run_id,
        session_id=session_id,
        conversation_id=conversation_id,
        requested_by=admin.user_id,
        tool_invocation_id=invocation_id,
        tool_id=tool_id,
        tool_version=1,
        mcp_server_profile_id=profile_id,
        mcp_server_profile_version=1,
        arguments_ref="obj://tenant/mcp-arguments",
        arguments_sha256="d" * 64,
        preview_ref="obj://tenant/mcp-preview",
        preview_sha256="e" * 64,
        expires_at=expires_at,
    )
    pending = repository.create_tool_approval(
        worker, approval, run_fencing_token=7
    )
    assert pending.state is ToolApprovalState.PENDING
    summary_repository = MySqlConversationRepository(connection_factory)
    pending_summary = summary_repository.list_conversation_summaries(
        admin, employee.employee_id, "ACTIVE", None, 10
    )
    assert pending_summary[0].last_run_status == "AWAITING_APPROVAL"
    assert pending_summary[0].pending_approval_count == 1

    connection = mysql.connect(**config, database=database)
    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT state FROM runs WHERE platform_id=%s AND tenant_id=%s AND run_id=%s",
            (admin.platform_id, admin.tenant_id, run_id),
        )
        assert cursor.fetchone()[0] == "AWAITING_APPROVAL"
    finally:
        cursor.close()
        connection.close()

    with pytest.raises(DomainError) as denied:
        repository.approve_tool_approval(
            IdentityContext(
                admin.platform_id,
                admin.tenant_id,
                "member-peer",
                frozenset({"member"}),
            ),
            approval.approval_id,
            approval.preview_sha256,
            idempotency_key="approve-1",
            now=datetime.now(UTC),
        )
    assert denied.value.code == "AUTHORIZATION_DENIED"

    approved = repository.approve_tool_approval(
        admin,
        approval.approval_id,
        approval.preview_sha256,
        idempotency_key="approve-1",
        now=datetime.now(UTC),
    )
    assert approved.state is ToolApprovalState.APPROVED
    approved_summary = summary_repository.list_conversation_summaries(
        admin, employee.employee_id, "ACTIVE", None, 10
    )
    assert approved_summary[0].pending_approval_count == 0
    assert repository.approve_tool_approval(
        admin,
        approval.approval_id,
        approval.preview_sha256,
        idempotency_key="approve-1",
        now=datetime.now(UTC),
    ) == approved

    barrier = Barrier(2)

    def claim(worker_id: str):
        barrier.wait(timeout=5)
        return repository.claim_approved_tool_approval(
            worker, worker_id, lease_seconds=30
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        claimed = list(executor.map(claim, ("worker-a", "worker-b")))
    winners = [item for item in claimed if item is not None]
    assert len(winners) == 1
    action = winners[0]
    assert action.state is ToolApprovalState.RUNNING
    assert repository.finish_tool_approval(
        worker,
        approval.approval_id,
        action.worker_id or "",
        action.fencing_token,
        ToolApprovalState.SUCCEEDED,
        now=datetime.now(UTC),
    )
    finished_summary = summary_repository.list_conversation_summaries(
        admin, employee.employee_id, "ACTIVE", None, 10
    )
    assert finished_summary[0].last_run_status == "SUCCEEDED"
    assert finished_summary[0].pending_approval_count == 0

    connection = mysql.connect(**config, database=database)
    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT state FROM runs WHERE platform_id=%s AND tenant_id=%s AND run_id=%s",
            (admin.platform_id, admin.tenant_id, run_id),
        )
        assert cursor.fetchone()[0] == "SUCCEEDED"
        cursor.execute(
            "SELECT state, approved_by, requested_by FROM tool_approval_requests "
            "WHERE platform_id=%s AND tenant_id=%s AND approval_id=%s",
            (admin.platform_id, admin.tenant_id, approval.approval_id),
        )
        state, approver, requester = cursor.fetchone()
        assert (state, approver, requester) == ("SUCCEEDED", admin.user_id, admin.user_id)
    finally:
        cursor.close()
        connection.close()

    expired_run_id = generate_database_id()
    expired_approval_id = generate_database_id()
    expired_invocation_id = generate_database_id()
    connection = mysql.connect(**config, database=database)
    cursor = connection.cursor()
    try:
        cursor.execute(
            "INSERT INTO runs "
            "(platform_id,tenant_id,run_id,session_id,idempotency_key,request_digest,state,"
            "submitted_by_user_id,trace_id) "
            "VALUES (%s,%s,%s,%s,'expired-action',%s,'AWAITING_APPROVAL',%s,'trace-expired-action')",
            (
                admin.platform_id,
                admin.tenant_id,
                expired_run_id,
                session_id,
                "e" * 64,
                admin.user_id,
            ),
        )
        cursor.execute(
            "INSERT INTO platform_trace_spans "
            "(platform_id,tenant_id,run_id,span_id,trace_id,span_type,status,sequence_no,attributes_json) "
            "VALUES (%s,%s,%s,%s,'trace-expired-action','tool','approved',1,'{}')",
            (
                admin.platform_id,
                admin.tenant_id,
                expired_run_id,
                expired_invocation_id,
            ),
        )
        cursor.execute(
            "INSERT INTO tool_approval_requests "
            "(platform_id,tenant_id,approval_id,run_id,session_id,conversation_id,requested_by,approved_by,"
            "tool_invocation_id,tool_id,tool_version,mcp_server_profile_id,mcp_server_profile_version,"
            "arguments_ref,arguments_sha256,preview_ref,preview_sha256,confirmation_idempotency_key,"
            "state,approved_at,expires_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1,%s,1,'object:args',%s,'object:preview',%s,"
            "'expired-action-confirm','APPROVED',CURRENT_TIMESTAMP(6),"
            "DATE_ADD(CURRENT_TIMESTAMP(6),INTERVAL 1 HOUR))",
            (
                admin.platform_id,
                admin.tenant_id,
                expired_approval_id,
                expired_run_id,
                session_id,
                conversation_id,
                admin.user_id,
                admin.user_id,
                expired_invocation_id,
                tool_id,
                profile_id,
                "d" * 64,
                "f" * 64,
            ),
        )
        connection.commit()
    finally:
        cursor.close()
        connection.close()

    expired_claim = repository.claim_approved_tool_approval(
        worker, "worker-expired", lease_seconds=30
    )
    assert expired_claim is not None
    connection = mysql.connect(**config, database=database)
    cursor = connection.cursor()
    try:
        cursor.execute(
            "UPDATE tool_approval_requests SET lease_expires_at=DATE_SUB(CURRENT_TIMESTAMP(6),"
            "INTERVAL 1 SECOND) WHERE platform_id=%s AND tenant_id=%s AND approval_id=%s",
            (admin.platform_id, admin.tenant_id, expired_approval_id),
        )
        connection.commit()
    finally:
        cursor.close()
        connection.close()

    assert repository.claim_approved_tool_approval(worker, "worker-recovery") is None
    assert repository.claim_approved_tool_approval(worker, "worker-no-replay") is None
    connection = mysql.connect(**config, database=database)
    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT state,fencing_token,error_code FROM tool_approval_requests "
            "WHERE platform_id=%s AND tenant_id=%s AND approval_id=%s",
            (admin.platform_id, admin.tenant_id, expired_approval_id),
        )
        assert cursor.fetchone() == (
            "UNKNOWN",
            expired_claim.fencing_token + 1,
            "WORKER_LEASE_EXPIRED",
        )
        cursor.execute(
            "SELECT state,error_code FROM runs WHERE platform_id=%s AND tenant_id=%s AND run_id=%s",
            (admin.platform_id, admin.tenant_id, expired_run_id),
        )
        assert cursor.fetchone() == ("UNKNOWN", "APPROVAL_LEASE_EXPIRED")
    finally:
        cursor.close()
        connection.close()


def test_session_harness_lease_serializes_runs_and_quarantines_unknown_home(
    mysql_execution_database,
) -> None:
    from agent_platform.adapters.identity.fake import FakeIdentityProvider
    from agent_platform.domain.common import generate_database_id
    from agent_platform.domain.execution import RuntimeStatus
    from agent_platform.domain.identity import IdentityContext
    from agent_platform.domain.observability import RunRecord, RunSnapshot
    from agent_platform.infrastructure.mysql_execution import MySqlExecutionRepository

    database, config = mysql_execution_database
    admin = FakeIdentityProvider().resolve_identity("admin-local")
    session_id = generate_database_id()
    connection_factory = lambda: mysql.connect(**config, database=database)
    repository = MySqlExecutionRepository(connection_factory)
    tenant_b = "tenant-b"
    user_b = "member-b"
    connection = connection_factory()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "INSERT INTO tenants (platform_id,tenant_id,code,name,status) "
            "VALUES (%s,%s,%s,'B','ACTIVE')",
            (admin.platform_id, tenant_b, tenant_b),
        )
        cursor.execute(
            "INSERT INTO users "
            "(platform_id,tenant_id,user_id,external_subject,username,display_name,status) "
            "VALUES (%s,%s,%s,'subject-b','member-b','租户 B 成员','ACTIVE')",
            (admin.platform_id, tenant_b, user_b),
        )
        for tenant_id, user_id in ((admin.tenant_id, admin.user_id), (tenant_b, user_b)):
            cursor.execute(
                "INSERT INTO sessions "
                "(platform_id,tenant_id,session_id,user_id,engine_name,engine_session_id,status,metadata_json) "
                "VALUES (%s,%s,%s,%s,'deepseek',%s,'ACTIVE','{}')",
                (admin.platform_id, tenant_id, session_id, user_id, session_id),
            )
        connection.commit()
    finally:
        cursor.close()
        connection.close()

    def enqueue(tenant_id: str, sequence: int) -> str:
        run_id = generate_database_id()
        connection = connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                "INSERT INTO runs "
                "(platform_id,tenant_id,run_id,session_id,idempotency_key,request_digest,state) "
                "VALUES (%s,%s,%s,%s,%s,%s,'QUEUED')",
                (
                    admin.platform_id,
                    tenant_id,
                    run_id,
                    session_id,
                    f"lease-run-{tenant_id}-{sequence}",
                    str(sequence) * 64,
                ),
            )
            connection.commit()
        finally:
            cursor.close()
            connection.close()
        return run_id

    already_started: set[str] = set()

    def complete_known(identity: IdentityContext, run_id: str, worker_id: str, token: int) -> None:
        if run_id not in already_started:
            assert repository.start_claimed_run(identity, run_id, worker_id, token)
            already_started.add(run_id)
        run = RunRecord(
            run_id=run_id,
            session_id=session_id,
            platform_id=identity.platform_id,
            tenant_id=identity.tenant_id,
            trace_id="trace-session-lease",
            prompt_digest="a" * 64,
            snapshot=RunSnapshot(
                employee_id="employee-test",
                employee_version=1,
                skill_version_ids=(),
                tool_version_ids=(),
                execution_profile_id="profile-test",
            ),
            status=RuntimeStatus.SUCCEEDED,
        )
        assert repository.complete_run(identity, run, token, (), None)

    worker_a = IdentityContext(
        admin.platform_id, admin.tenant_id, "worker-a", frozenset(), auth_source="worker"
    )
    worker_b = IdentityContext(
        admin.platform_id, tenant_b, "worker-b", frozenset(), auth_source="worker"
    )
    worker_a2 = IdentityContext(
        admin.platform_id, admin.tenant_id, "worker-a2", frozenset(), auth_source="worker"
    )

    run_a_ids = (enqueue(admin.tenant_id, 1), enqueue(admin.tenant_id, 2))
    barrier = Barrier(2)

    def claim_same_session(worker_id: str):
        barrier.wait(timeout=5)
        return worker_id, repository.claim_next_queued_run(admin.platform_id, worker_id)

    with ThreadPoolExecutor(max_workers=2) as executor:
        concurrent_claims = list(
            executor.map(claim_same_session, ("worker-a", "worker-a2"))
        )
    active_claims = [
        (worker_id, claim) for worker_id, claim in concurrent_claims if claim is not None
    ]
    assert len(active_claims) == 1
    active_worker_id, first_claim = active_claims[0]
    assert first_claim.run_id in run_a_ids
    remaining_a = next(run_id for run_id in run_a_ids if run_id != first_claim.run_id)
    active_identity = worker_a if active_worker_id == "worker-a" else worker_a2
    idle_worker_id = "worker-a2" if active_worker_id == "worker-a" else "worker-a"
    idle_identity = worker_a2 if idle_worker_id == "worker-a2" else worker_a
    assert repository.start_claimed_run(
        active_identity, first_claim.run_id, active_worker_id, first_claim.fencing_token
    )
    already_started.add(first_claim.run_id)
    assert repository.is_run_lease_current(
        active_identity, first_claim.run_id, active_worker_id, first_claim.fencing_token
    )
    assert repository.heartbeat(
        active_identity,
        first_claim.run_id,
        first_claim.fencing_token,
        worker_id=active_worker_id,
    )
    assert not repository.heartbeat(
        idle_identity, first_claim.run_id, first_claim.fencing_token, worker_id=idle_worker_id
    )

    first_b = enqueue(tenant_b, 1)
    other_tenant_claim = repository.claim_next_queued_run(admin.platform_id, "worker-b")
    assert other_tenant_claim is not None
    assert (other_tenant_claim.tenant_id, other_tenant_claim.run_id) == (tenant_b, first_b)
    complete_known(worker_b, first_b, "worker-b", other_tenant_claim.fencing_token)

    assert repository.claim_next_queued_run(admin.platform_id, idle_worker_id) is None
    complete_known(
        active_identity,
        first_claim.run_id,
        active_worker_id,
        first_claim.fencing_token,
    )

    second_claim = repository.claim_next_queued_run(admin.platform_id, idle_worker_id)
    assert second_claim is not None and second_claim.run_id == remaining_a
    assert repository.mark_unknown(idle_identity, remaining_a, second_claim.fencing_token)
    connection = connection_factory()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT status FROM sessions WHERE platform_id=%s AND tenant_id=%s AND session_id=%s",
            (admin.platform_id, admin.tenant_id, session_id),
        )
        assert cursor.fetchone()[0] == "INTERRUPTED"
    finally:
        cursor.close()
        connection.close()
    assert repository.claim_next_queued_run(admin.platform_id, "worker-a") is None
