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


@pytest.fixture
def mysql_execution_database():
    if os.environ.get("AGENT_PLATFORM_MYSQL_TEST_ALLOW_CREATE_DATABASE") != "1":
        pytest.skip("set AGENT_PLATFORM_MYSQL_TEST_ALLOW_CREATE_DATABASE=1 for disposable MySQL integration")
    database = f"agent_platform_execution_test_{secrets.token_hex(5)}"
    config = {
        "host": os.environ.get("MYSQL_HOST", "127.0.0.1"),
        "port": int(os.environ.get("MYSQL_PORT", "3306")),
        "user": os.environ.get("MYSQL_USER", "root"),
        "password": os.environ.get("MYSQL_PASSWORD", ""),
    }
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
    assert loaded_session.deployment_id == deployment.deployment_id
    assert loaded_session.employee_version == 1
    assert loaded_session.trace_id == run.trace_id
    assert loaded_session.model_route_id == route.route_id
    assert loaded_run.snapshot.employee_id == employee.employee_id
    assert loaded_run.snapshot.employee_version == 1
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

    tool_run, created = execution_repository.submit_run(
        identity,
        replace(queued, run_id=generate_database_id(), prompt_digest="tool-invocation-digest"),
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
        assert state == "RUNNING"
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
    finally:
        cursor.close()
        connection.close()
    assert repository.get_run(identity, str(run_id)).status is RuntimeStatus.UNKNOWN
