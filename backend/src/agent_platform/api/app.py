from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections.abc import Mapping, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from agent_platform.adapters.identity.fake import FakeAuthorizationProvider
from agent_platform.adapters.model_authorization import PlatformModelAuthorizationProvider
from agent_platform.adapters.model_catalog import OpenAICompatibleModelCatalogProvider
from agent_platform.adapters.model_secrets import (
    EnvironmentModelSecretProvider,
    ModelSecretError,
)
from agent_platform.adapters.runtime.codex_app_server import (
    CodexAppServerConfig,
    CodexAppServerRuntimeAdapter,
)
from agent_platform.adapters.runtime.fake import FakeRuntimeAdapter
from agent_platform.adapters.secrets import EnvironmentSecretProvider
from agent_platform.api.routes import router
from agent_platform.application.artifacts import ArtifactService
from agent_platform.application.catalog_service import CatalogService, InMemoryCatalogRepository
from agent_platform.application.control_plane import ControlPlaneService
from agent_platform.application.conversation_turns import ConversationTurnService
from agent_platform.application.conversations import ConversationService
from agent_platform.application.deployment_runtime import DeploymentRuntimeConfigService
from agent_platform.application.engine_providers import (
    EngineProviderCapabilities,
    EngineProviderRegistry,
)
from agent_platform.application.model_management import ModelManagementService
from agent_platform.application.model_routes import ModelRouteService
from agent_platform.application.orchestration import OrchestrationService
from agent_platform.application.resource_center import ResourceCenterService
from agent_platform.application.runtime_defaults import RuntimeDefaultsService
from agent_platform.application.skill_files import SkillFilePackageService
from agent_platform.application.team_conversations import TeamConversationService
from agent_platform.application.team_task_execution import TeamTaskExecutionService
from agent_platform.application.team_turns import TeamTurnService
from agent_platform.application.teams import TeamService
from agent_platform.application.tool_approval import ToolApprovalService
from agent_platform.application.worker_pools import WorkerPoolRegistry
from agent_platform.domain.common import DomainError
from agent_platform.infrastructure.artifact_repository import InMemoryArtifactRepository
from agent_platform.infrastructure.migrations import (
    MigrationPreflight,
    MySqlMigrationStateReader,
    PreflightAction,
    load_migration_checksums,
)
from agent_platform.infrastructure.mysql_artifacts import MySqlArtifactRepository
from agent_platform.infrastructure.mysql_catalog import MySqlCatalogRepository
from agent_platform.infrastructure.mysql_conversations import MySqlConversationRepository
from agent_platform.infrastructure.mysql_database import MySqlConfig, MySqlConnectionPool
from agent_platform.infrastructure.mysql_execution import MySqlExecutionRepository
from agent_platform.infrastructure.mysql_mcp import MySqlMcpRepository
from agent_platform.infrastructure.mysql_orchestration import MySqlOrchestrationRepository
from agent_platform.infrastructure.mysql_storage import MySqlMediaAssetRepository
from agent_platform.infrastructure.mysql_teams import MySqlTeamRepository
from agent_platform.infrastructure.orchestration_repository import InMemoryOrchestrationRepository
from agent_platform.ports.catalog_repository import CatalogRepository
from agent_platform.ports.conversation_repository import ConversationRepository
from agent_platform.ports.execution_repository import ExecutionRepository
from agent_platform.ports.mcp import McpRepository
from agent_platform.ports.model_provider import (
    ModelAuthorizationProvider,
    ModelCatalogProvider,
    SecretProvider,
)
from agent_platform.ports.resource_center import ResourceCenterRepository
from agent_platform.ports.runtime import RuntimeAdapter, RuntimeRequest
from agent_platform.ports.storage import LocalObjectStore, ObjectStore, SecretRef


def create_app(
    *,
    catalog_repository: CatalogRepository | None = None,
    execution_repository: ExecutionRepository | None = None,
    conversation_repository: ConversationRepository | None = None,
    team_repository=None,
    orchestration_repository=None,
    artifact_repository=None,
    mcp_repository: McpRepository | None = None,
    resource_center_repository: ResourceCenterRepository | None = None,
    object_store: ObjectStore | None = None,
    runtime_adapter: RuntimeAdapter | None = None,
    engine_provider_registry: EngineProviderRegistry | None = None,
    model_authorization_provider: ModelAuthorizationProvider | None = None,
    model_catalog_provider: ModelCatalogProvider | None = None,
    model_secret_provider: SecretProvider | None = None,
    mode: str | None = None,
    lifecycle_resources: Sequence[object] = (),
) -> FastAPI:
    """建立应用依赖图；无仓储时只构造 Fake 测试/演示模式。"""

    resolved_mode = mode or ("mysql" if catalog_repository is not None else "fake")
    if resolved_mode not in {"fake", "mysql"}:
        raise ValueError("AGENT_PLATFORM_MODE must be 'fake' or 'mysql'")
    if resolved_mode == "mysql" and any(
        value is None
        for value in (
            catalog_repository,
            execution_repository,
            conversation_repository,
            mcp_repository,
            resource_center_repository,
            object_store,
        )
    ):
        raise ValueError("MySQL 模式必须完整注入目录、执行、Conversation 和对象仓储")

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            yield
        finally:
            task_executor = getattr(app.state, "team_task_execution_service", None)
            if task_executor is not None:
                task_executor.close()
            for resource in reversed(tuple(lifecycle_resources)):
                close = getattr(resource, "close", None)
                if close is not None:
                    close()

    app = FastAPI(title="Agent Platform API", version="0.1.0", lifespan=lifespan)
    use_fake_gallery = resolved_mode == "fake" and catalog_repository is None
    resolved_catalog_repository = (
        catalog_repository if catalog_repository is not None else InMemoryCatalogRepository()
    )
    catalog = CatalogService(
        resolved_catalog_repository,
        resource_center_repository=resource_center_repository,
    )
    app.state.catalog_service = catalog
    app.state.use_fake_gallery = use_fake_gallery
    resolved_model_authorization = (
        model_authorization_provider or PlatformModelAuthorizationProvider()
    )
    resolved_model_catalog_provider = (
        model_catalog_provider or OpenAICompatibleModelCatalogProvider()
    )
    resolved_model_secret_provider = model_secret_provider or EnvironmentModelSecretProvider()
    resolved_runtime_adapter = runtime_adapter or FakeRuntimeAdapter()
    resolved_engine_providers = engine_provider_registry or EngineProviderRegistry.for_runtime(
        resolved_runtime_adapter,
        simulate_known_engines=runtime_adapter is None,
    )
    app.state.engine_provider_registry = resolved_engine_providers
    app.state.conversation_service = ConversationService(conversation_repository, object_store)
    app.state.team_service = TeamService(team_repository)
    app.state.team_conversation_service = TeamConversationService(
        app.state.conversation_service, app.state.team_service
    )
    app.state.orchestration_repository = orchestration_repository or InMemoryOrchestrationRepository()
    app.state.orchestration_service = OrchestrationService(
        app.state.orchestration_repository, app.state.team_service
    )
    app.state.artifact_repository = artifact_repository or InMemoryArtifactRepository()
    app.state.artifact_service = ArtifactService(app.state.artifact_repository, object_store)
    app.state.worker_pool_registry = WorkerPoolRegistry()
    team_max_workers = int(os.environ.get("AGENT_PLATFORM_TEAM_MAX_WORKERS", "8"))
    from agent_platform.application.engine_providers import normalize_engine_provider_name

    for engine_name in sorted({normalize_engine_provider_name(name) for name in resolved_engine_providers.available_names()}):
        app.state.worker_pool_registry.register(engine_name, max_concurrency=team_max_workers)
    app.state.tool_approval_service = (
        ToolApprovalService(mcp_repository, object_store)
        if mcp_repository is not None and object_store is not None
        else None
    )
    model_route_repository = resolved_catalog_repository
    app.state.model_route_service = ModelRouteService(model_route_repository)
    app.state.model_management_service = ModelManagementService(
        resolved_catalog_repository,
        app.state.model_route_service,
        FakeAuthorizationProvider(),
        resolved_model_authorization,
        catalog_provider=resolved_model_catalog_provider,
        secret_provider=resolved_model_secret_provider,
        runtime_adapter=resolved_runtime_adapter,
    )
    app.state.runtime_defaults_service = RuntimeDefaultsService(
        resolved_catalog_repository,
        app.state.model_management_service,
        resolved_engine_providers,
    )
    app.state.control_plane_service = ControlPlaneService(
        catalog,
        resolved_runtime_adapter,
        persistence=execution_repository,
        object_store=object_store,
        resource_center_repository=resource_center_repository,
        mcp_repository=mcp_repository,
        conversation_repository=conversation_repository,
        model_authorization_provider=resolved_model_authorization,
        runtime_defaults=app.state.runtime_defaults_service,
        engine_providers=resolved_engine_providers,
    )
    app.state.conversation_turn_service = ConversationTurnService(
        app.state.conversation_service,
        app.state.control_plane_service,
    )
    app.state.team_task_execution_service = TeamTaskExecutionService(
        app.state.team_service,
        app.state.orchestration_service,
        app.state.conversation_service,
        app.state.control_plane_service,
        app.state.artifact_service,
        max_workers=team_max_workers,
        worker_pools=app.state.worker_pool_registry,
    )
    app.state.team_turn_service = TeamTurnService(
        app.state.conversation_service,
        app.state.team_service,
        app.state.orchestration_service,
        app.state.control_plane_service,
        app.state.team_task_execution_service,
    )
    app.state.deployment_runtime_config_service = DeploymentRuntimeConfigService(
        resolved_catalog_repository,
        app.state.model_management_service,
        resolved_engine_providers,
    )
    app.state.model_test_executor = app.state.model_management_service.execute_test_record
    app.state.resource_center_service = (
        ResourceCenterService(catalog, resource_center_repository, object_store=object_store)
        if resource_center_repository is not None
        else None
    )
    app.state.skill_file_package_service = (
        SkillFilePackageService(
            catalog, object_store, resource_center_repository=resource_center_repository
        )
        if object_store is not None
        else None
    )
    app.state.mode = resolved_mode
    app.include_router(router)

    @app.exception_handler(DomainError)
    async def domain_error_handler(request: Request, error: DomainError) -> JSONResponse:
        status_by_code = {
            "RESOURCE_NOT_FOUND": 404,
            "RESOURCE_CONFLICT": 409,
            "AUTHORIZATION_DENIED": 403,
            "DEPENDENCY_VALIDATION_ERROR": 409,
            "ENGINE_MODEL_INCOMPATIBLE": 409,
            "ENGINE_PROVIDER_UNAVAILABLE": 409,
            "ENGINE_CAPABILITY_UNSUPPORTED": 409,
            "ENGINE_CONFIGURATION_REQUIRED": 409,
            "DEPLOYMENT_DISABLED": 409,
            "IMMUTABLE_VERSION": 409,
            "IDEMPOTENCY_CONFLICT": 409,
            "MODEL_ROUTE_IN_USE": 409,
            "MODEL_ROUTE_VERSION_CONFLICT": 409,
            "RUNTIME_CONFIGURATION_REQUIRED": 409,
            "RUNTIME_CONFIG_REVISION_CONFLICT": 409,
            "TEAM_VERSION_CONFLICT": 409,
            "INVALID_STATE": 409,
            "CONVERSATION_MODE_MISMATCH": 409,
            "PLAN_INVALID": 409,
            "TASK_GRAPH_CYCLE": 409,
            "ENGINE_POOL_UNAVAILABLE": 409,
            "ENGINE_POOL_BUSY": 409,
            "LEASE_FENCING_CONFLICT": 409,
            "RETRY_EXHAUSTED": 409,
            "INVALID_ARGUMENT": 422,
        }
        request_id = request.headers.get("X-Request-ID", f"req-{uuid4().hex}")
        return JSONResponse(
            status_code=status_by_code.get(error.code, 400),
            content={
                "code": error.code,
                "message": error.message,
                "request_id": request_id,
                "details": error.details,
            },
        )

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


def create_app_from_environment() -> FastAPI:
    """读取 Fake/MySQL 部署配置；MySQL 默认 DeepSeek 并启动时只读检查 schema。"""

    mode = os.environ.get("AGENT_PLATFORM_MODE")
    runtime_engine = _runtime_engine_from_environment(mode)
    if mode == "fake":
        return create_app(mode="fake")
    if mode != "mysql":
        raise RuntimeError("必须显式设置 AGENT_PLATFORM_MODE=mysql 或 AGENT_PLATFORM_MODE=fake")
    if os.environ.get("AGENT_PLATFORM_IDENTITY_PROVIDER") != "fake":
        raise RuntimeError(
            "本地控制面只实现 Fake identity；设置 AGENT_PLATFORM_IDENTITY_PROVIDER=fake 并显式运行 seed 命令"
        )

    required = ("MYSQL_HOST", "MYSQL_DATABASE", "MYSQL_USER", "MYSQL_PASSWORD")
    missing = [name for name in required if name not in os.environ]
    if missing:
        raise RuntimeError("MySQL 模式缺少环境变量：" + ", ".join(missing))
    config = MySqlConfig(
        host=os.environ["MYSQL_HOST"],
        port=int(os.environ.get("MYSQL_PORT", "3306")),
        database=os.environ["MYSQL_DATABASE"],
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
        pool_size=int(os.environ.get("MYSQL_POOL_SIZE", "8")),
    )
    pool = MySqlConnectionPool(config)
    try:
        root = Path(__file__).resolve().parents[4]
        migrations_dir = root / "docs" / "migrations"
        preflight = MigrationPreflight(load_migration_checksums(migrations_dir)).inspect(
            MySqlMigrationStateReader(pool.connection)
        )
        if preflight.action is not PreflightAction.UP_TO_DATE:
            details = "; ".join(preflight.errors or preflight.warnings) or preflight.action.value
            raise RuntimeError(
                f"MySQL schema 尚未就绪：{details}；请显式运行 agent-platform-migration preflight/apply"
            )

        encryption_required = os.environ.get("AGENT_PLATFORM_OBJECT_ENCRYPTION_REQUIRED", "0") == "1"
        secret_ref_text = os.environ.get("AGENT_PLATFORM_STORAGE_SECRET_REF")
        key_version = os.environ.get("AGENT_PLATFORM_STORAGE_KEY_VERSION")
        key_b64 = os.environ.get("AGENT_PLATFORM_STORAGE_KEY_B64")
        key_provider = None
        secret_ref = None
        if encryption_required:
            if not secret_ref_text or not key_version or not key_b64:
                raise RuntimeError("对象加密已启用，但 SecretRef、密钥版本或密钥值未配置")
            secret_ref = SecretRef(secret_ref_text)
            key_provider = EnvironmentSecretProvider(
                secret_ref_text,
                current_key_version=key_version,
                environment_variable="AGENT_PLATFORM_STORAGE_KEY_B64",
            )

        data_root = Path(
            os.environ.get(
                "AGENT_PLATFORM_DATA_ROOT",
                str(Path.home() / ".local" / "share" / "agent-platform"),
            )
        ).expanduser().resolve()
        if root == data_root or root in data_root.parents:
            raise RuntimeError("AGENT_PLATFORM_DATA_ROOT 必须位于代码仓库和 Web root 之外")
        object_store = LocalObjectStore(
            data_root / "objects",
            metadata_repository=MySqlMediaAssetRepository(pool.connection),
            require_encryption=encryption_required,
            encryption_secret_ref=secret_ref,
            encryption_key_version=key_version,
            key_provider=key_provider,
        )
        runtime_adapter = _build_runtime_adapter(runtime_engine, data_root)
        engine_provider_registry = EngineProviderRegistry.for_runtime(runtime_adapter)
        codex_adapter = _register_codex_app_server_provider(
            engine_provider_registry,
            data_root / "codex-runtime",
            EnvironmentModelSecretProvider(),
        )
        from agent_platform.infrastructure.mysql_resource_center import (
            MySqlResourceCenterRepository,
        )

        return create_app(
            mode="mysql",
            catalog_repository=MySqlCatalogRepository(pool.connection),
            execution_repository=MySqlExecutionRepository(pool.connection),
            conversation_repository=MySqlConversationRepository(pool.connection),
            team_repository=MySqlTeamRepository(pool.connection),
            orchestration_repository=MySqlOrchestrationRepository(pool.connection),
            artifact_repository=MySqlArtifactRepository(pool.connection),
            mcp_repository=MySqlMcpRepository(pool.connection),
            resource_center_repository=MySqlResourceCenterRepository(pool.connection),
            object_store=object_store,
            runtime_adapter=runtime_adapter,
            engine_provider_registry=engine_provider_registry,
            lifecycle_resources=((pool, codex_adapter) if codex_adapter else (pool,)),
        )
    except Exception:
        pool.close()
        raise


def _runtime_engine_from_environment(
    mode: str | None,
    environment: Mapping[str, str] | None = None,
) -> str:
    """为 MySQL 和 Fake 入口选择一致、可显式覆盖的默认引擎。"""

    configured_environment = os.environ if environment is None else environment
    default_engine = "fake" if mode == "fake" else "deepseek"
    runtime_engine = configured_environment.get(
        "AGENT_PLATFORM_RUNTIME_ENGINE", default_engine
    ).strip().lower()
    if runtime_engine not in {"fake", "deepseek"}:
        raise RuntimeError("AGENT_PLATFORM_RUNTIME_ENGINE 只支持 fake 或 deepseek")
    if mode == "fake" and runtime_engine != "fake":
        raise RuntimeError("DeepSeek Runtime 需要 MySQL Run Worker 模式")
    return runtime_engine


def _register_codex_app_server_provider(
    registry: EngineProviderRegistry,
    state_root: Path,
    secret_provider: SecretProvider,
) -> CodexAppServerRuntimeAdapter | None:
    """仅在运维显式开启并配置可执行命令时注册隔离 Codex Provider。"""

    if os.environ.get("AGENT_PLATFORM_CODEX_APP_SERVER_ENABLED", "0") != "1":
        return None
    config = CodexAppServerConfig.from_environment(state_root)
    command_path = config.command[0]
    if not (Path(command_path).is_file() and os.access(command_path, os.X_OK)) and shutil.which(command_path) is None:
        raise RuntimeError("Codex app-server 已启用，但配置的 codex 命令不可执行")
    adapter = CodexAppServerRuntimeAdapter(
        command=config.command,
        state_root=config.state_root,
        secret_resolver=secret_provider.resolve,
        timeout_seconds=config.timeout_seconds,
        startup_timeout_seconds=config.startup_timeout_seconds,
    )
    runtime_capabilities = EngineProviderCapabilities.from_runtime(adapter.capabilities)
    registry.register(
        "codex_cli",
        adapter,
        display_name="Codex CLI",
        model_protocols={"openai-responses"},
        capabilities=EngineProviderCapabilities(
            resume=runtime_capabilities.resume,
            cancellation=runtime_capabilities.cancellation,
            tool_calls=runtime_capabilities.tool_calls,
            mcp=runtime_capabilities.mcp,
            workspace=runtime_capabilities.workspace,
            sandbox=runtime_capabilities.sandbox,
            approvals=runtime_capabilities.approvals,
            reasoning=runtime_capabilities.reasoning,
            reasoning_effort=runtime_capabilities.reasoning_effort,
            reasoning_budget=runtime_capabilities.reasoning_budget,
            max_output_tokens=runtime_capabilities.max_output_tokens,
            model_route_required=True,
            route_secret_required=True,
        ),
    )
    return adapter


def _build_runtime_adapter(runtime_engine: str, state_root: Path) -> RuntimeAdapter:
    """根据明确配置构造 Worker Runtime；缺省保留 Fake，不在 API 启动时猜测凭据。"""

    if runtime_engine == "fake":
        return FakeRuntimeAdapter()
    if runtime_engine != "deepseek":
        raise RuntimeError("AGENT_PLATFORM_RUNTIME_ENGINE 只支持 fake 或 deepseek")
    from harness_engine.core import EngineRequest
    from harness_engine.engines.deepseek import DeepSeekHarnessEngine
    from harness_engine.mcp_proxy import McpProxyConfig, McpProxyLaunch

    from agent_platform.adapters.mcp.environment import (
        EnvironmentMcpSecretResolver,
        EnvironmentMcpWorkingDirectoryResolver,
    )
    from agent_platform.adapters.mcp.stdio import McpStdioHost
    from agent_platform.adapters.runtime.harness import HarnessRuntimeAdapter

    harness_profile = os.environ.get("AGENT_PLATFORM_DEEPSEEK_PROFILE", "sdk-minimal")
    provider = os.environ.get("AGENT_PLATFORM_DEEPSEEK_PROVIDER", "deepseek-official")
    model = os.environ.get("AGENT_PLATFORM_DEEPSEEK_MODEL", "deepseek-v4-flash")
    local_config_text = os.environ.get("AGENT_PLATFORM_DEEPSEEK_LOCAL_CONFIG", "").strip()
    local_config = None
    if local_config_text:
        local_config = Path(local_config_text).expanduser().resolve(strict=True)
        if not local_config.is_file():
            raise RuntimeError("AGENT_PLATFORM_DEEPSEEK_LOCAL_CONFIG 必须指向配置文件")

    def path_allowlist(environment_name: str) -> tuple[str, ...]:
        try:
            values = json.loads(os.environ.get(environment_name, "[]"))
        except json.JSONDecodeError as error:
            raise RuntimeError(f"{environment_name} 必须是 JSON 路径数组") from error
        if not isinstance(values, list) or any(
            not isinstance(value, str) or not Path(value).is_absolute() for value in values
        ):
            raise RuntimeError(f"{environment_name} 必须是绝对路径 JSON 数组")
        return tuple(values)

    allowed_commands = path_allowlist("AGENT_PLATFORM_MCP_ALLOWED_COMMANDS")
    allowed_cwd_roots = path_allowlist(
        "AGENT_PLATFORM_MCP_ALLOWED_WORKING_DIRECTORY_ROOTS"
    )
    secret_resolver = EnvironmentMcpSecretResolver()
    model_secret_resolver = EnvironmentModelSecretProvider()
    directory_resolver = EnvironmentMcpWorkingDirectoryResolver()

    def create_engine(engine_request: EngineRequest, runtime_request: RuntimeRequest) -> object:
        mcp_servers = runtime_request.mcp_servers
        model_environment: dict[str, str] = {}
        model_snapshot = runtime_request.model_route_snapshot
        if model_snapshot is not None and model_snapshot.secret_ref is not None:
            # SecretRef 仅在 Worker 创建本次 Engine 时解析，不进入持久化快照或 EngineRequest。
            try:
                model_environment["AGENT_PLATFORM_MODEL_API_KEY"] = model_secret_resolver.resolve(
                    model_snapshot.secret_ref
                )
            except ModelSecretError as error:
                # 凭据缺失发生在模型请求出站前，稳定归类且不把 SecretRef 写进错误上下文。
                raise DomainError(
                    "ENGINE_CONFIGURATION_REQUIRED",
                    "接收方 ModelRoute 凭据无法解析",
                    {"model_route_id": model_snapshot.route_id},
                ) from error
        proxy_launches = []
        for runtime_server in mcp_servers:
            server_profile = runtime_server.profile
            host = McpStdioHost(
                server_profile,
                directory_resolver=directory_resolver,
                secret_resolver=secret_resolver,
                allowed_commands=allowed_commands,
                allowed_working_directory_roots=allowed_cwd_roots,
            )
            launch_config = host.resolve_launch_config()
            server_digest = hashlib.sha256(
                f"{server_profile.profile_id}:{server_profile.version}".encode()
            ).hexdigest()[:20]
            proxy_config = McpProxyConfig(
                command=str(launch_config.command),
                args=launch_config.args,
                cwd=str(launch_config.cwd),
                profile_tool_names=server_profile.enabled_tool_names,
                run_allowed_tool_names=runtime_server.run_allowed_tool_names,
                profile_catalog_sha256=server_profile.tool_catalog_sha256,
                expected_tool_schema_sha256=runtime_server.expected_tool_schema_sha256,
                environment_names=tuple(sorted(launch_config.environment)),
                startup_timeout_ms=server_profile.startup_timeout_ms,
                tool_timeout_ms=server_profile.tool_timeout_ms,
            )
            proxy_launches.append(
                McpProxyLaunch(
                    server_name=f"mcp-{server_digest}",
                    config=proxy_config,
                    environment=launch_config.environment,
                )
            )
        return DeepSeekHarnessEngine.for_run_request(
            engine_request,
            harness_state_root=state_root,
            profile=harness_profile,
            provider=provider,
            model=(
                runtime_request.employee_context.model_alias
                if runtime_request.model_route_snapshot is None
                and runtime_request.employee_context is not None
                and runtime_request.employee_context.model_alias
                else model
            ),
            env=model_environment,
            mcp_proxy_launches=tuple(proxy_launches),
            local_config=local_config,
        )

    return HarnessRuntimeAdapter(
        create_engine,
        engine_name="deepseek",
    )
