"""验证异步 Worker 只能从 RunSnapshot 解析固定员工资源。"""

from dataclasses import replace

from agent_platform.adapters.identity.fake import FakeIdentityProvider
from agent_platform.adapters.runtime.fake import FakeRuntimeAdapter
from agent_platform.application.catalog_service import CatalogService, InMemoryCatalogRepository
from agent_platform.application.control_plane import ControlPlaneService
from agent_platform.application.resolvers import AgentResolver
from agent_platform.domain.catalog import SkillType
from agent_platform.domain.execution import RuntimeStatus
from agent_platform.domain.identity import IdentityContext


def test_resolve_snapshot_uses_the_employee_version_fixed_when_run_was_queued() -> None:
    identity = IdentityContext("platform-1", "tenant-1", "admin-1", frozenset({"admin"}))
    catalog = CatalogService(InMemoryCatalogRepository())
    profile = catalog.create_profile(identity, "fake", "fake-model")
    skill = catalog.create_skill(
        identity, "固定 Skill", SkillType.INSTRUCTION, "使用固定字段定义", []
    )
    catalog.publish_skill(identity, skill.skill_id)
    employee = catalog.create_employee(identity, "数仓员工", "开发", "测试员工")
    version_one = catalog.create_employee_version(
        identity,
        employee.employee_id,
        "Run 入队时的系统指令",
        [skill.skill_id],
        [],
        execution_profile_id=profile.profile_id,
    )
    published_one = catalog.publish_employee_version(
        identity, employee.employee_id, version_one.version
    )
    deployment_one = catalog.deploy(identity, employee.employee_id, published_one.version)
    resolver = AgentResolver(catalog)
    queued_snapshot = resolver.resolve(identity, deployment_one).snapshot

    version_two = catalog.create_employee_version(
        identity,
        employee.employee_id,
        "Run 入队后的新系统指令",
        [skill.skill_id],
        [],
        execution_profile_id=profile.profile_id,
    )
    catalog.publish_employee_version(identity, employee.employee_id, version_two.version)
    catalog.deploy(identity, employee.employee_id, version_two.version)

    resolved = resolver.resolve_snapshot(identity, queued_snapshot)

    assert resolved.system_prompt == "Run 入队时的系统指令"
    assert resolved.snapshot.employee_version == version_one.version
    assert resolved.snapshot == queued_snapshot


class _ClaimedRunPersistence:
    def __init__(self) -> None:
        self.completed = None
        self.sessions = {}
        self.current_run = None
        self.cancel_on_session_save = False
        self.lease_current = True

    def save_session(self, identity, session, *, engine_name):
        self.sessions[(identity.tenant_id, session.session_id)] = session

    def save_session_runtime_refs(
        self,
        identity,
        session_id,
        *,
        runtime_session_ref,
        runtime_result_ref,
        harness_home_ref=None,
    ):
        session = self.sessions[(identity.tenant_id, session_id)]
        session.runtime_session_ref = runtime_session_ref
        if harness_home_ref is not None:
            session.harness_home_ref = harness_home_ref
        if self.cancel_on_session_save:
            self.current_run.status = RuntimeStatus.CANCELLING

    def submit_run(self, *args, **kwargs):
        raise AssertionError("Worker 已有 claim，不得再次提交 Run")

    def acquire_lease(self, *args, **kwargs):
        raise AssertionError("Worker 已有 fencing token，不得再次 claim")

    def complete_run(self, identity, run, fencing_token, spans, usage):
        self.completed = (run, fencing_token, spans, usage)
        self.current_run = replace(run)
        return True

    def get_run(self, identity, run_id):
        return replace(self.current_run)

    def is_run_lease_current(self, identity, run_id, worker_id, fencing_token):
        return self.lease_current

    def mark_unknown(self, identity, run_id, fencing_token):
        self.current_run.status = RuntimeStatus.UNKNOWN
        return True


class _RecordingRuntime(FakeRuntimeAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.last_request = None

    def run(self, request):
        self.last_request = request
        return super().run(request)


def test_control_plane_executes_preclaimed_run_with_its_frozen_snapshot() -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    catalog = CatalogService(InMemoryCatalogRepository())
    profile = catalog.create_profile(identity, "fake", "fake-model")
    skill = catalog.create_skill(
        identity, "固定 Skill", SkillType.INSTRUCTION, "入队时 Skill 指令", []
    )
    catalog.publish_skill(identity, skill.skill_id)
    employee = catalog.create_employee(identity, "数仓员工", "开发", "测试员工")
    version_one = catalog.create_employee_version(
        identity,
        employee.employee_id,
        "Run 入队时的系统指令",
        [skill.skill_id],
        [],
        execution_profile_id=profile.profile_id,
    )
    published_one = catalog.publish_employee_version(
        identity, employee.employee_id, version_one.version
    )
    deployment_one = catalog.deploy(identity, employee.employee_id, published_one.version)
    persistence = _ClaimedRunPersistence()
    runtime = _RecordingRuntime()
    service = ControlPlaneService(catalog, runtime, persistence=persistence)
    session = service.create_session(identity, deployment_one.deployment_id)
    queued = service.prepare_conversation_turn(
        identity, session.session_id, "conversation-1", "处理 ODS 到 DWD"
    )
    persistence.current_run = replace(queued, status=RuntimeStatus.RUNNING)

    version_two = catalog.create_employee_version(
        identity,
        employee.employee_id,
        "Run 入队后的新系统指令",
        [skill.skill_id],
        [],
        execution_profile_id=profile.profile_id,
    )
    catalog.publish_employee_version(identity, employee.employee_id, version_two.version)
    catalog.deploy(identity, employee.employee_id, version_two.version)
    worker_identity = IdentityContext(
        identity.platform_id,
        identity.tenant_id,
        "run-worker-1",
        frozenset(),
        auth_source="worker",
    )

    completed = service.execute_claimed_run(
        worker_identity,
        replace(queued, status=RuntimeStatus.RUNNING),
        session,
        "处理 ODS 到 DWD",
        worker_id="run-worker-1",
        fencing_token=11,
    )

    assert runtime.last_request is not None
    assert runtime.last_request.system_prompt == "Run 入队时的系统指令"
    assert runtime.last_request.skill_instructions == ("入队时 Skill 指令",)
    assert completed.snapshot.employee_version == version_one.version
    assert completed.status is RuntimeStatus.SUCCEEDED
    assert persistence.completed is not None
    assert persistence.completed[1] == 11


def test_claimed_run_cancelled_before_runtime_call_completes_without_invoking_engine() -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    catalog = CatalogService(InMemoryCatalogRepository())
    profile = catalog.create_profile(identity, "fake", "fake-model")
    skill = catalog.create_skill(
        identity, "固定 Skill", SkillType.INSTRUCTION, "固定指令", []
    )
    catalog.publish_skill(identity, skill.skill_id)
    employee = catalog.create_employee(identity, "数仓员工", "开发", "测试员工")
    version = catalog.create_employee_version(
        identity,
        employee.employee_id,
        "系统指令",
        [skill.skill_id],
        [],
        execution_profile_id=profile.profile_id,
    )
    catalog.publish_employee_version(identity, employee.employee_id, version.version)
    deployment = catalog.deploy(identity, employee.employee_id, version.version)
    persistence = _ClaimedRunPersistence()
    runtime = _RecordingRuntime()
    service = ControlPlaneService(catalog, runtime, persistence=persistence)
    session = service.create_session(identity, deployment.deployment_id)
    queued = service.prepare_conversation_turn(
        identity, session.session_id, "conversation-1", "取消后不调用 Runtime"
    )
    persistence.current_run = replace(queued, status=RuntimeStatus.CANCELLING)
    worker_identity = IdentityContext(
        identity.platform_id, identity.tenant_id, "run-worker-1", frozenset(), auth_source="worker"
    )

    cancelled = service.execute_claimed_run(
        worker_identity,
        replace(queued, status=RuntimeStatus.RUNNING),
        session,
        "取消后不调用 Runtime",
        worker_id="run-worker-1",
        fencing_token=12,
    )

    assert cancelled.status is RuntimeStatus.CANCELLED
    assert runtime.last_request is None
    assert persistence.completed is not None
    assert persistence.completed[0].status is RuntimeStatus.CANCELLED


def test_cancellation_during_runtime_session_setup_is_checked_before_run_call() -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    catalog = CatalogService(InMemoryCatalogRepository())
    profile = catalog.create_profile(identity, "fake", "fake-model")
    skill = catalog.create_skill(
        identity, "固定 Skill", SkillType.INSTRUCTION, "固定指令", []
    )
    catalog.publish_skill(identity, skill.skill_id)
    employee = catalog.create_employee(identity, "数仓员工", "开发", "测试员工")
    version = catalog.create_employee_version(
        identity,
        employee.employee_id,
        "系统指令",
        [skill.skill_id],
        [],
        execution_profile_id=profile.profile_id,
    )
    catalog.publish_employee_version(identity, employee.employee_id, version.version)
    deployment = catalog.deploy(identity, employee.employee_id, version.version)
    persistence = _ClaimedRunPersistence()
    persistence.cancel_on_session_save = True
    runtime = _RecordingRuntime()
    service = ControlPlaneService(catalog, runtime, persistence=persistence)
    session = service.create_session(identity, deployment.deployment_id)
    queued = service.prepare_conversation_turn(
        identity, session.session_id, "conversation-1", "取消运行中的初始化"
    )
    persistence.current_run = replace(queued, status=RuntimeStatus.RUNNING)
    worker_identity = IdentityContext(
        identity.platform_id, identity.tenant_id, "run-worker-1", frozenset(), auth_source="worker"
    )

    cancelled = service.execute_claimed_run(
        worker_identity,
        replace(queued, status=RuntimeStatus.RUNNING),
        session,
        "取消运行中的初始化",
        worker_id="run-worker-1",
        fencing_token=13,
    )

    assert cancelled.status is RuntimeStatus.CANCELLED
    assert runtime.last_request is None
    assert persistence.completed is not None
    assert persistence.completed[0].status is RuntimeStatus.CANCELLED


def test_expired_worker_lease_is_fenced_before_runtime_session_creation() -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    catalog = CatalogService(InMemoryCatalogRepository())
    profile = catalog.create_profile(identity, "fake", "fake-model")
    skill = catalog.create_skill(identity, "固定 Skill", SkillType.INSTRUCTION, "固定指令", [])
    catalog.publish_skill(identity, skill.skill_id)
    employee = catalog.create_employee(identity, "数仓员工", "开发", "测试员工")
    version = catalog.create_employee_version(
        identity, employee.employee_id, "系统指令", [skill.skill_id], [],
        execution_profile_id=profile.profile_id,
    )
    catalog.publish_employee_version(identity, employee.employee_id, version.version)
    deployment = catalog.deploy(identity, employee.employee_id, version.version)
    persistence = _ClaimedRunPersistence()
    persistence.lease_current = False
    runtime = _RecordingRuntime()
    service = ControlPlaneService(catalog, runtime, persistence=persistence)
    session = service.create_session(identity, deployment.deployment_id)
    queued = service.prepare_conversation_turn(
        identity, session.session_id, "conversation-1", "失租后不允许出站"
    )
    persistence.current_run = replace(queued, status=RuntimeStatus.RUNNING)
    worker_identity = IdentityContext(
        identity.platform_id, identity.tenant_id, "run-worker-1", frozenset(), auth_source="worker"
    )

    recovered = service.execute_claimed_run(
        worker_identity,
        replace(queued, status=RuntimeStatus.RUNNING),
        session,
        "失租后不允许出站",
        worker_id="run-worker-1",
        fencing_token=14,
    )

    assert recovered.status is RuntimeStatus.UNKNOWN
    assert runtime.last_request is None
    assert persistence.completed is None
