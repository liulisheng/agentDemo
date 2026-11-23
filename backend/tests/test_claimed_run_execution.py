"""验证异步 Worker 只能从 RunSnapshot 解析固定员工资源。"""

from dataclasses import replace

from agent_platform.adapters.identity.fake import FakeIdentityProvider
from agent_platform.adapters.runtime.codex_app_server import CodexAppServerRuntimeAdapter
from agent_platform.adapters.runtime.fake import FakeRuntimeAdapter
from agent_platform.application.catalog_service import CatalogService, InMemoryCatalogRepository
from agent_platform.application.control_plane import ControlPlaneService
from agent_platform.application.engine_providers import (
    EngineProviderCapabilities,
    EngineProviderRegistry,
)
from agent_platform.application.model_routes import ModelRouteService
from agent_platform.application.resolvers import AgentResolver
from agent_platform.domain.catalog import SkillType
from agent_platform.domain.execution import RuntimeStatus
from agent_platform.domain.identity import IdentityContext
from agent_platform.domain.model_routes import ModelCatalogItem, ModelConnection, SecretRef
from tests._runtime_config_helpers import configure_in_memory_deployment


def test_resolve_snapshot_uses_the_employee_version_fixed_when_run_was_queued() -> None:
    identity = IdentityContext("platform-1", "tenant-1", "admin-1", frozenset({"admin"}))
    catalog = CatalogService(InMemoryCatalogRepository())
    profile = catalog.create_profile(identity, "deepseek_harness")
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
    deployment_one = configure_in_memory_deployment(catalog, identity, deployment_one)
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


class _UncertainRuntime(FakeRuntimeAdapter):
    """模拟模型 Turn 已出站但响应丢失，Worker 不得自动改用其他引擎。"""

    engine_name = "deepseek_harness"

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def run(self, request):
        self.calls += 1
        raise TimeoutError("模型 Turn 响应丢失")


def test_control_plane_executes_preclaimed_run_with_its_frozen_snapshot() -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    catalog = CatalogService(InMemoryCatalogRepository())
    profile = catalog.create_profile(identity, "deepseek_harness", "fake-model")
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
    deployment_one = configure_in_memory_deployment(catalog, identity, deployment_one)
    persistence = _ClaimedRunPersistence()
    runtime = _RecordingRuntime()
    fallback_runtime = _RecordingRuntime()
    providers = EngineProviderRegistry()
    providers.register(
        "fake",
        fallback_runtime,
        display_name="Fake Runtime",
        model_protocols={"openai-chat-completions"},
        simulation=True,
    )
    providers.register(
        "deepseek_harness",
        runtime,
        display_name="DeepSeek Harness",
        model_protocols={"openai-chat-completions"},
    )
    service = ControlPlaneService(
        catalog,
        fallback_runtime,
        persistence=persistence,
        engine_providers=providers,
    )
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
    assert fallback_runtime.last_request is None
    assert queued.snapshot.engine_name == "deepseek_harness"
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
    deployment = configure_in_memory_deployment(catalog, identity, deployment)
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
    deployment = configure_in_memory_deployment(catalog, identity, deployment)
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
    deployment = configure_in_memory_deployment(catalog, identity, deployment)
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


def test_worker_marks_missing_codex_secret_as_failed_without_fake_fallback(tmp_path) -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    repository = InMemoryCatalogRepository()
    catalog = CatalogService(repository)
    connection = ModelConnection(
        connection_id="codex-connection",
        tenant_id=identity.tenant_id,
        name="Codex Worker Route",
        provider_kind="openai-compatible",
        protocol="openai-responses",
        endpoint="https://model.invalid/v1",
        secret_ref=SecretRef("tenant-key", scope=identity.tenant_id, provider="environment"),
    )
    repository.save_model_connection(identity, connection)
    repository.save_model_catalog_item(
        identity,
        ModelCatalogItem(
            connection_id=connection.connection_id,
            tenant_id=identity.tenant_id,
            model_id="codex-model",
            display_name="Codex Model",
        ),
    )
    route = ModelRouteService(repository).create_from_catalog(
        identity,
        alias="Codex Worker Route",
        connection_id=connection.connection_id,
        model_id="codex-model",
    )
    catalog.create_profile(identity, "codex_cli", model_route_id=route.route_id)
    skill = catalog.create_skill(identity, "Codex Worker Skill", SkillType.INSTRUCTION, "审阅", [])
    catalog.publish_skill(identity, skill.skill_id)
    employee = catalog.create_employee(identity, "Codex Worker 员工", "审查员", "凭据边界")
    version = catalog.create_employee_version(
        identity, employee.employee_id, "只读检查", [skill.skill_id], []
    )
    published = catalog.publish_employee_version(identity, employee.employee_id, version.version)
    deployment = catalog.deploy(identity, employee.employee_id, published.version)
    deployment = configure_in_memory_deployment(catalog, identity, deployment)

    connections = []

    def unavailable_connection(*_args):
        connections.append(True)
        raise AssertionError("SecretRef 失败时不得启动 Codex app-server")

    codex = CodexAppServerRuntimeAdapter(
        command=("codex", "app-server", "--stdio"),
        state_root=tmp_path,
        secret_resolver=lambda _secret: (_ for _ in ()).throw(RuntimeError("secret missing")),
        connection_factory=unavailable_connection,
    )
    runtime_providers = EngineProviderRegistry()
    runtime_providers.register("fake", FakeRuntimeAdapter(), display_name="Fake", model_protocols=())
    runtime_providers.register(
        "codex_cli",
        codex,
        display_name="Codex CLI",
        model_protocols={"openai-responses"},
        capabilities=EngineProviderCapabilities(
            resume=True,
            model_route_required=True,
            route_secret_required=True,
        ),
    )
    persistence = _ClaimedRunPersistence()
    service = ControlPlaneService(
        catalog,
        FakeRuntimeAdapter(),
        persistence=persistence,
        engine_providers=runtime_providers,
    )
    session = service.create_session(identity, deployment.deployment_id)
    queued = service.prepare_conversation_turn(
        identity, session.session_id, "conversation-codex", "检查 route secret"
    )
    persistence.current_run = replace(queued, status=RuntimeStatus.RUNNING)
    worker = IdentityContext(
        identity.platform_id,
        identity.tenant_id,
        "worker-codex",
        frozenset(),
        auth_source="worker",
    )

    completed = service.execute_claimed_run(
        worker,
        replace(queued, status=RuntimeStatus.RUNNING),
        session,
        "检查 route secret",
        worker_id="worker-codex",
        fencing_token=21,
    )

    assert completed.status is RuntimeStatus.FAILED
    assert completed.error_category == "ENGINE_CONFIGURATION_REQUIRED"
    assert persistence.completed is not None
    assert persistence.completed[0].status is RuntimeStatus.FAILED
    assert connections == []
    codex.close()


def test_worker_marks_missing_deepseek_secret_as_failed_without_fake_fallback(
    monkeypatch, tmp_path
) -> None:
    import sys
    from types import SimpleNamespace
    from typing import ClassVar

    from agent_platform.adapters.model_secrets import EnvironmentModelSecretProvider
    from agent_platform.api.app import _build_runtime_adapter

    identity = FakeIdentityProvider().resolve_identity("admin-local")
    secret_ref = SecretRef(
        "missing-model-key", scope=identity.tenant_id, provider="environment"
    )
    monkeypatch.delenv(EnvironmentModelSecretProvider.environment_name(secret_ref), raising=False)
    monkeypatch.delenv("AGENT_PLATFORM_DEEPSEEK_LOCAL_CONFIG", raising=False)

    class UnexpectedHarness:
        instances: ClassVar[list[bool]] = []

        def __init__(self, **_kwargs):
            type(self).instances.append(True)

    monkeypatch.setitem(
        sys.modules,
        "deepseek_harness",
        SimpleNamespace(DeepSeekHarness=UnexpectedHarness),
    )

    repository = InMemoryCatalogRepository()
    catalog = CatalogService(repository)
    connection = ModelConnection(
        connection_id="deepseek-missing-secret-connection",
        tenant_id=identity.tenant_id,
        name="SecretRef 缺失的验收路由",
        provider_kind="openai-compatible",
        protocol="openai-chat-completions",
        endpoint="http://model.invalid/v1",
        secret_ref=secret_ref,
    )
    repository.save_model_connection(identity, connection)
    repository.save_model_catalog_item(
        identity,
        ModelCatalogItem(
            connection_id=connection.connection_id,
            tenant_id=identity.tenant_id,
            model_id="acceptance-model",
            display_name="确定性 Mock",
        ),
    )
    route = ModelRouteService(repository).create_from_catalog(
        identity,
        alias="SecretRef 缺失路由",
        connection_id=connection.connection_id,
        model_id="acceptance-model",
    )
    catalog.create_profile(identity, "deepseek_harness", model_route_id=route.route_id)
    skill = catalog.create_skill(
        identity, "Harness SecretRef Skill", SkillType.INSTRUCTION, "只读验收", []
    )
    catalog.publish_skill(identity, skill.skill_id)
    employee = catalog.create_employee(identity, "SecretRef 验收员工", "验收助手", "验证失败边界")
    version = catalog.create_employee_version(
        identity, employee.employee_id, "只返回配置错误", [skill.skill_id], []
    )
    published = catalog.publish_employee_version(
        identity, employee.employee_id, version.version
    )
    deployment = catalog.deploy(identity, employee.employee_id, published.version)
    deployment = configure_in_memory_deployment(catalog, identity, deployment)

    runtime = _build_runtime_adapter("deepseek", tmp_path / "deepseek-state")
    fake_fallback = _RecordingRuntime()
    providers = EngineProviderRegistry.for_runtime(runtime)
    providers.register(
        "fake",
        fake_fallback,
        display_name="Fake Runtime",
        model_protocols={"openai-chat-completions"},
        simulation=True,
    )
    persistence = _ClaimedRunPersistence()
    service = ControlPlaneService(
        catalog,
        fake_fallback,
        persistence=persistence,
        engine_providers=providers,
    )
    session = service.create_session(identity, deployment.deployment_id)
    queued = service.prepare_conversation_turn(
        identity, session.session_id, "conversation-deepseek-secret", "执行本地模型验收"
    )
    persistence.current_run = replace(queued, status=RuntimeStatus.RUNNING)
    worker = IdentityContext(
        identity.platform_id,
        identity.tenant_id,
        "worker-deepseek-secret",
        frozenset(),
        auth_source="worker",
    )

    completed = service.execute_claimed_run(
        worker,
        replace(queued, status=RuntimeStatus.RUNNING),
        session,
        "执行本地模型验收",
        worker_id="worker-deepseek-secret",
        fencing_token=23,
    )

    assert queued.snapshot.model_route_snapshot is not None
    assert queued.snapshot.model_route_snapshot.secret_ref == secret_ref
    assert completed.status is RuntimeStatus.FAILED
    assert completed.error_category == "ENGINE_CONFIGURATION_REQUIRED"
    assert persistence.completed is not None
    assert persistence.completed[0].status is RuntimeStatus.FAILED
    assert fake_fallback.last_request is None
    assert UnexpectedHarness.instances == []


def test_worker_marks_revoked_snapshot_profile_failed_before_runtime() -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    repository = InMemoryCatalogRepository()
    catalog = CatalogService(repository)
    profile = catalog.create_profile(identity, "deepseek_harness", "deepseek-chat")
    skill = catalog.create_skill(identity, "撤销 Skill", SkillType.INSTRUCTION, "只读分析", [])
    catalog.publish_skill(identity, skill.skill_id)
    employee = catalog.create_employee(identity, "撤销配置员工", "分析", "验证配置撤销")
    version = catalog.create_employee_version(
        identity, employee.employee_id, "分析任务", [skill.skill_id], []
    )
    published = catalog.publish_employee_version(identity, employee.employee_id, version.version)
    deployment = catalog.deploy(identity, employee.employee_id, published.version)
    deployment = configure_in_memory_deployment(catalog, identity, deployment)
    persistence = _ClaimedRunPersistence()
    runtime = _RecordingRuntime()
    service = ControlPlaneService(catalog, runtime, persistence=persistence)
    session = service.create_session(identity, deployment.deployment_id)
    queued = service.prepare_conversation_turn(
        identity, session.session_id, "conversation-revoked-profile", "使用已撤销配置"
    )
    persistence.current_run = replace(queued, status=RuntimeStatus.RUNNING)
    repository.profile_versions.pop(
        (identity.platform_id, identity.tenant_id, profile.profile_id, profile.version)
    )
    worker = IdentityContext(
        identity.platform_id,
        identity.tenant_id,
        "worker-revoked-profile",
        frozenset(),
        auth_source="worker",
    )

    completed = service.execute_claimed_run(
        worker,
        replace(queued, status=RuntimeStatus.RUNNING),
        session,
        "使用已撤销配置",
        worker_id="worker-revoked-profile",
        fencing_token=23,
    )

    assert completed.status is RuntimeStatus.FAILED
    assert completed.error_category == "RESOURCE_NOT_FOUND"
    assert persistence.completed is not None
    assert persistence.completed[0].status is RuntimeStatus.FAILED
    assert runtime.last_request is None


def test_worker_keeps_unknown_after_runtime_turn_without_fake_retry() -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    catalog = CatalogService(InMemoryCatalogRepository())
    catalog.create_profile(identity, "deepseek_harness", "deepseek-chat")
    skill = catalog.create_skill(identity, "超时 Skill", SkillType.INSTRUCTION, "只读分析", [])
    catalog.publish_skill(identity, skill.skill_id)
    employee = catalog.create_employee(identity, "超时员工", "分析", "验证不确定结果")
    version = catalog.create_employee_version(
        identity, employee.employee_id, "分析任务", [skill.skill_id], []
    )
    published = catalog.publish_employee_version(identity, employee.employee_id, version.version)
    deployment = catalog.deploy(identity, employee.employee_id, published.version)
    deployment = configure_in_memory_deployment(catalog, identity, deployment)

    fallback = _RecordingRuntime()
    uncertain = _UncertainRuntime()
    providers = EngineProviderRegistry()
    providers.register("fake", fallback, display_name="Fake", model_protocols=())
    providers.register(
        "deepseek_harness",
        uncertain,
        display_name="DeepSeek Harness",
        model_protocols={"openai-chat-completions"},
    )
    persistence = _ClaimedRunPersistence()
    service = ControlPlaneService(
        catalog, fallback, persistence=persistence, engine_providers=providers
    )
    session = service.create_session(identity, deployment.deployment_id)
    queued = service.prepare_conversation_turn(
        identity, session.session_id, "conversation-timeout", "调用模型并模拟响应丢失"
    )
    persistence.current_run = replace(queued, status=RuntimeStatus.RUNNING)
    worker = IdentityContext(
        identity.platform_id,
        identity.tenant_id,
        "worker-timeout",
        frozenset(),
        auth_source="worker",
    )

    completed = service.execute_claimed_run(
        worker,
        replace(queued, status=RuntimeStatus.RUNNING),
        session,
        "调用模型并模拟响应丢失",
        worker_id="worker-timeout",
        fencing_token=22,
    )

    assert completed.status is RuntimeStatus.UNKNOWN
    assert uncertain.calls == 1
    assert fallback.last_request is None
