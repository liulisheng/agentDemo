"""租户模型连接、目录、模型测试任务和可选路由查询服务。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from time import monotonic
from urllib.parse import urlsplit
from uuid import uuid4

from agent_platform.application.model_routes import ModelRouteService
from agent_platform.domain.common import DomainError, generate_database_id
from agent_platform.domain.identity import IdentityContext
from agent_platform.domain.model_routes import (
    ModelCatalogItem,
    ModelCatalogMode,
    ModelCatalogSource,
    ModelConnection,
    ModelConnectionStatus,
    ModelRoute,
    ModelRouteSnapshot,
    ModelTestKind,
    ModelTestRecord,
    ModelTestResult,
    ModelTestState,
    SecretRef,
    UsageStatus,
)
from agent_platform.ports.catalog_repository import CatalogRepository
from agent_platform.ports.identity import AuthorizationProvider
from agent_platform.ports.model_provider import (
    ModelAuthorizationProvider,
    ModelCatalogProvider,
    SecretProvider,
)
from agent_platform.ports.runtime import RuntimeAdapter, RuntimeEmployeeContext, RuntimeRequest


class ModelManagementService:
    """租户管理员维护模型入口；终端用户只读取当前身份可使用的路由。"""

    _openai_protocols = frozenset(
        {"openai-compatible", "openai-chat-completions", "openai-completions", "openai-responses"}
    )

    def __init__(
        self,
        repository: CatalogRepository,
        routes: ModelRouteService,
        authorization: AuthorizationProvider,
        model_authorization: ModelAuthorizationProvider,
        *,
        catalog_provider: ModelCatalogProvider | None = None,
        secret_provider: SecretProvider | None = None,
        runtime_adapter: RuntimeAdapter | None = None,
    ) -> None:
        self.repository = repository
        self.routes = routes
        self.authorization = authorization
        self.model_authorization = model_authorization
        self.catalog_provider = catalog_provider
        self.secret_provider = secret_provider
        self.runtime_adapter = runtime_adapter

    @staticmethod
    def _resource(identity: IdentityContext, name: str) -> str:
        return f"{identity.tenant_id}:{name}"

    def _require(self, identity: IdentityContext, action: str, resource: str) -> None:
        if not self.authorization.can(identity, action, self._resource(identity, resource)):
            raise DomainError("AUTHORIZATION_DENIED", "当前用户无权管理此租户的模型资源")

    @classmethod
    def _validate_endpoint(cls, provider_kind: str, protocol: str, endpoint: str) -> None:
        if provider_kind != "openai-compatible":
            raise DomainError("INVALID_ARGUMENT", "当前阶段只支持 OpenAI 兼容模型连接")
        if protocol not in cls._openai_protocols:
            raise DomainError("INVALID_ARGUMENT", "模型连接协议暂不支持")
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise DomainError("INVALID_ARGUMENT", "模型连接地址必须是无内嵌凭据的 HTTP(S) URL")

    @staticmethod
    def _secret_ref(identity: IdentityContext, secret_id: str | None) -> SecretRef | None:
        if secret_id is None:
            return None
        return SecretRef(secret_id=secret_id, scope=identity.tenant_id, provider="environment")

    def create_connection(
        self,
        identity: IdentityContext,
        *,
        name: str,
        provider_kind: str,
        protocol: str,
        endpoint: str,
        secret_id: str | None,
        catalog_mode: str,
    ) -> ModelConnection:
        self._require(identity, "model:manage", "model-connections")
        self._validate_endpoint(provider_kind, protocol, endpoint)
        try:
            resolved_catalog_mode = ModelCatalogMode(catalog_mode)
        except ValueError as error:
            raise DomainError("INVALID_ARGUMENT", "模型目录模式不支持") from error
        connection = ModelConnection(
            connection_id=f"connection-{uuid4().hex}",
            tenant_id=identity.tenant_id,
            name=name,
            provider_kind=provider_kind,
            protocol=protocol,
            endpoint=endpoint,
            secret_ref=self._secret_ref(identity, secret_id),
            catalog_mode=resolved_catalog_mode,
        )
        self.repository.save_model_connection(identity, connection)
        return connection

    def get_connection(self, identity: IdentityContext, connection_id: str) -> ModelConnection:
        self._require(identity, "model:read", "model-connections")
        return self.repository.get_model_connection(identity, connection_id)

    def list_connections(self, identity: IdentityContext) -> tuple[ModelConnection, ...]:
        self._require(identity, "model:read", "model-connections")
        return self.repository.list_model_connections(identity)

    def update_connection(
        self, identity: IdentityContext, connection_id: str, updates: Mapping[str, object]
    ) -> ModelConnection:
        self._require(identity, "model:manage", "model-connections")
        current = self.repository.get_model_connection(identity, connection_id)
        values: dict[str, object] = {}
        for name in ("name", "provider_kind", "protocol", "endpoint", "catalog_mode", "status"):
            if name in updates:
                values[name] = updates[name]
        if "secret_id" in updates:
            secret_id = updates["secret_id"]
            if secret_id is not None and not isinstance(secret_id, str):
                raise DomainError("INVALID_ARGUMENT", "secret_id 格式无效")
            values["secret_ref"] = self._secret_ref(identity, secret_id)
        values.setdefault("name", current.name)
        values.setdefault("provider_kind", current.provider_kind)
        values.setdefault("protocol", current.protocol)
        values.setdefault("endpoint", current.endpoint)
        values.setdefault("secret_ref", current.secret_ref)
        values.setdefault("catalog_mode", current.catalog_mode)
        values.setdefault("status", current.status)
        self._validate_endpoint(
            str(values["provider_kind"]),
            str(values["protocol"]),
            str(values["endpoint"]),
        )
        try:
            updated = replace(current, **values, version=current.version + 1)
        except ValueError as error:
            raise DomainError("INVALID_ARGUMENT", "模型连接配置无效") from error
        self.repository.save_model_connection(identity, updated)
        return updated

    def list_models(
        self, identity: IdentityContext, connection_id: str
    ) -> tuple[ModelCatalogItem, ...]:
        self._require(identity, "model:read", "model-catalog")
        return self.repository.list_model_catalog_items(identity, connection_id)

    def add_manual_model(
        self,
        identity: IdentityContext,
        connection_id: str,
        *,
        model_id: str,
        display_name: str,
        capabilities: Mapping[str, object],
        context_window: int | None,
    ) -> ModelCatalogItem:
        self._require(identity, "model:manage", "model-catalog")
        connection = self.repository.get_model_connection(identity, connection_id)
        if connection.status is not ModelConnectionStatus.ACTIVE:
            raise DomainError("DEPENDENCY_VALIDATION_ERROR", "已停用的连接不能增加模型目录项")
        item = ModelCatalogItem(
            connection_id=connection_id,
            tenant_id=identity.tenant_id,
            model_id=model_id,
            display_name=display_name,
            capabilities=capabilities,
            context_window=context_window,
            catalog_source=ModelCatalogSource.MANUAL,
        )
        self.repository.save_model_catalog_item(identity, item)
        return item

    def queue_catalog_sync(self, identity: IdentityContext, connection_id: str) -> ModelTestRecord:
        self._require(identity, "model:manage", "model-connections")
        connection = self.repository.get_model_connection(identity, connection_id)
        if connection.status is not ModelConnectionStatus.ACTIVE:
            raise DomainError("DEPENDENCY_VALIDATION_ERROR", "已停用的连接不能同步模型目录")
        if connection.catalog_mode is ModelCatalogMode.MANUAL:
            raise DomainError("INVALID_STATE", "此连接使用手工模型目录")
        record = ModelTestRecord(
            test_id=generate_database_id(),
            tenant_id=identity.tenant_id,
            connection_id=connection.connection_id,
            test_kind=ModelTestKind.CONNECTION,
            state=ModelTestState.QUEUED,
            snapshot={
                "operation": "SYNC_MODELS",
                "platform_id": identity.platform_id,
                "connection": connection.to_public_dict(),
            },
            created_by_user_id=identity.user_id,
        )
        self.repository.save_model_test_record(identity, record)
        return record

    def queue_connection_test(
        self, identity: IdentityContext, connection_id: str
    ) -> ModelTestRecord:
        """排入一次只检查连接认证和模型目录的测试，不发送补全请求。"""

        self._require(identity, "model:manage", "model-connections")
        connection = self.repository.get_model_connection(identity, connection_id)
        if connection.status is not ModelConnectionStatus.ACTIVE:
            raise DomainError("DEPENDENCY_VALIDATION_ERROR", "已停用的连接不能测试")
        record = ModelTestRecord(
            test_id=generate_database_id(),
            tenant_id=identity.tenant_id,
            connection_id=connection.connection_id,
            test_kind=ModelTestKind.CONNECTION,
            state=ModelTestState.QUEUED,
            snapshot={
                "operation": "CONNECTION_TEST",
                "platform_id": identity.platform_id,
                "connection": connection.to_public_dict(),
            },
            created_by_user_id=identity.user_id,
        )
        self.repository.save_model_test_record(identity, record)
        return record

    def queue_model_test(
        self, identity: IdentityContext, route_id: str, *, confirmed_cost: bool
    ) -> ModelTestRecord:
        """只接受明确确认的具体路由测试，并在任务中固定当时的连接/模型配置。"""

        self._require(identity, "model:manage", "model-routes")
        if confirmed_cost is not True:
            raise DomainError("INVALID_ARGUMENT", "真实模型测试需要显式确认可能产生费用")
        route = self.routes.get(identity, route_id)
        if route.status != "ACTIVE":
            raise DomainError("DEPENDENCY_VALIDATION_ERROR", "已停用的模型路由不能测试")
        profile = route.profile
        if profile.connection_id is None or profile.catalog_model_id is None:
            raise DomainError("DEPENDENCY_VALIDATION_ERROR", "模型路由缺少连接或目录模型引用")
        connection = self.repository.get_model_connection(identity, profile.connection_id)
        model = self.repository.get_model_catalog_item(
            identity, profile.connection_id, profile.catalog_model_id
        )
        if not self.model_authorization.authorize(identity, connection, model):
            raise DomainError("AUTHORIZATION_DENIED", "当前身份未获得该模型的测试授权")
        model_snapshot = ModelRouteSnapshot(
            tenant_id=identity.tenant_id,
            route_id=route.route_id,
            route_version=route.route_version,
            route_alias=route.alias,
            connection_id=connection.connection_id,
            connection_version=connection.version,
            provider_kind=connection.provider_kind,
            model_id=model.model_id,
            protocol=connection.protocol,
            endpoint=connection.endpoint,
            secret_ref=connection.secret_ref,
            parameters=profile.parameters,
            pricing_snapshot_ref=profile.pricing_snapshot_ref,
        )
        record = ModelTestRecord(
            test_id=generate_database_id(),
            tenant_id=identity.tenant_id,
            connection_id=connection.connection_id,
            route_id=route.route_id,
            model_id=model.model_id,
            test_kind=ModelTestKind.MODEL,
            state=ModelTestState.QUEUED,
            snapshot={
                "operation": "MODEL_TEST",
                "platform_id": identity.platform_id,
                "model_route": model_snapshot.to_snapshot_dict(),
            },
            created_by_user_id=identity.user_id,
        )
        self.repository.save_model_test_record(identity, record)
        return record

    @staticmethod
    def _connection_from_test(record: ModelTestRecord) -> ModelConnection:
        raw = record.snapshot.get("connection")
        if not isinstance(raw, Mapping):
            raise DomainError("INVALID_STATE", "模型测试快照缺少连接配置")
        raw_secret = raw.get("secret_ref")
        secret_ref = None
        if isinstance(raw_secret, Mapping):
            secret_ref = SecretRef(
                str(raw_secret["secret_id"]),
                str(raw_secret.get("scope", "tenant")),
                str(raw_secret.get("provider", "configured")),
            )
        return ModelConnection(
            connection_id=str(raw["connection_id"]),
            tenant_id=str(raw["tenant_id"]),
            name=str(raw["name"]),
            provider_kind=str(raw["provider_kind"]),
            protocol=str(raw["protocol"]),
            endpoint=str(raw["endpoint"]),
            secret_ref=secret_ref,
            catalog_mode=str(raw.get("catalog_mode", "REMOTE")),
            status=str(raw.get("status", "ACTIVE")),
            version=int(raw.get("version", 1)),
        )

    @staticmethod
    def _runtime_failure(error: Exception, *, elapsed_ms: int) -> ModelTestResult:
        """只根据结构化状态映射稳定错误，绝不把异常正文写入测试记录。"""

        response = getattr(error, "response", None)
        status = getattr(error, "status_code", None) or getattr(response, "status_code", None)
        if status in {401, 403}:
            return ModelTestResult(
                ModelTestState.FAILED,
                latency_ms=elapsed_ms,
                error_category="MODEL_CREDENTIAL_INVALID",
            )
        if status == 404:
            return ModelTestResult(
                ModelTestState.FAILED,
                latency_ms=elapsed_ms,
                error_category="MODEL_NOT_FOUND",
            )
        if isinstance(error, TimeoutError) or "timeout" in type(error).__name__.lower():
            return ModelTestResult(
                ModelTestState.UNKNOWN,
                latency_ms=elapsed_ms,
                error_category="MODEL_TEST_TIMEOUT",
            )
        if isinstance(status, int):
            return ModelTestResult(
                ModelTestState.FAILED,
                latency_ms=elapsed_ms,
                error_category="MODEL_PROVIDER_ERROR",
            )
        return ModelTestResult(
            ModelTestState.UNKNOWN,
            latency_ms=elapsed_ms,
            error_category="MODEL_TEST_OUTCOME_UNKNOWN",
        )

    def execute_test_record(self, record: ModelTestRecord) -> ModelTestResult:
        """由 Run Worker 调用；只返回有界状态数据，不保留输入或响应正文。"""

        started = monotonic()
        elapsed = lambda: max(0, int((monotonic() - started) * 1000))
        try:
            platform_id = str(record.snapshot.get("platform_id", ""))
            if not platform_id.strip():
                return ModelTestResult(
                    ModelTestState.FAILED, error_category="MODEL_TEST_SNAPSHOT_INVALID"
                )
            identity = IdentityContext(
                platform_id=platform_id,
                tenant_id=record.tenant_id,
                user_id=record.created_by_user_id,
                roles=frozenset(),
                auth_source="worker",
            )
            operation = str(record.snapshot.get("operation", ""))
            if operation in {"SYNC_MODELS", "CONNECTION_TEST"}:
                connection = self._connection_from_test(record)
                current = self.repository.get_model_connection(identity, connection.connection_id)
                if current.status is not ModelConnectionStatus.ACTIVE:
                    return ModelTestResult(
                        ModelTestState.FAILED,
                        latency_ms=elapsed(),
                        error_category="MODEL_CONNECTION_DISABLED",
                    )
                if current.version != connection.version:
                    return ModelTestResult(
                        ModelTestState.FAILED,
                        latency_ms=elapsed(),
                        error_category="MODEL_CONNECTION_VERSION_CHANGED",
                    )
                if self.catalog_provider is None:
                    return ModelTestResult(
                        ModelTestState.FAILED,
                        latency_ms=elapsed(),
                        error_category="MODEL_CATALOG_UNAVAILABLE",
                    )
                if connection.secret_ref is not None and self.secret_provider is None:
                    return ModelTestResult(
                        ModelTestState.FAILED,
                        latency_ms=elapsed(),
                        error_category="MODEL_SECRET_UNAVAILABLE",
                    )
                credential = (
                    self.secret_provider.resolve(connection.secret_ref)
                    if connection.secret_ref is not None
                    else None
                )
                probe_connection = (
                    replace(connection, catalog_mode=ModelCatalogMode.REMOTE)
                    if operation == "CONNECTION_TEST"
                    else connection
                )
                items = self.catalog_provider.list_models(
                    probe_connection, credential=credential
                )
                if len(items) > 500:
                    return ModelTestResult(
                        ModelTestState.FAILED,
                        latency_ms=elapsed(),
                        error_category="MODEL_CATALOG_TOO_LARGE",
                    )
                if operation == "SYNC_MODELS":
                    for item in items:
                        if item.tenant_id != record.tenant_id or item.connection_id != record.connection_id:
                            return ModelTestResult(
                                ModelTestState.FAILED,
                                latency_ms=elapsed(),
                                error_category="MODEL_CATALOG_INVALID_RESPONSE",
                            )
                        self.repository.save_model_catalog_item(identity, item)
                return ModelTestResult(
                    ModelTestState.SUCCEEDED,
                    latency_ms=elapsed(),
                )

            if operation != "MODEL_TEST":
                return ModelTestResult(
                    ModelTestState.FAILED,
                    error_category="MODEL_TEST_OPERATION_UNSUPPORTED",
                )
            if self.runtime_adapter is None:
                return ModelTestResult(
                    ModelTestState.FAILED,
                    error_category="MODEL_TEST_RUNTIME_UNAVAILABLE",
                )
            raw_model_snapshot = record.snapshot.get("model_route")
            if not isinstance(raw_model_snapshot, Mapping):
                return ModelTestResult(
                    ModelTestState.FAILED,
                    error_category="MODEL_TEST_SNAPSHOT_INVALID",
                )
            model_snapshot = ModelRouteSnapshot.from_snapshot_dict(raw_model_snapshot)
            route = self.repository.get_model_route(identity, model_snapshot.route_id)
            connection = self.repository.get_model_connection(identity, model_snapshot.connection_id)
            model = self.repository.get_model_catalog_item(
                identity, model_snapshot.connection_id, model_snapshot.model_id
            )
            if route.status != "ACTIVE" or connection.status is not ModelConnectionStatus.ACTIVE:
                return ModelTestResult(
                    ModelTestState.FAILED,
                    latency_ms=elapsed(),
                    error_category="MODEL_ROUTE_DISABLED",
                )
            if not self.model_authorization.authorize(identity, connection, model):
                return ModelTestResult(
                    ModelTestState.FAILED,
                    latency_ms=elapsed(),
                    error_category="AUTHORIZATION_DENIED",
                )
            harness_home_ref = "harness-session:" + __import__("hashlib").sha256(
                f"{record.test_id}:{record.fencing_token}".encode()
            ).hexdigest()
            runtime_result = self.runtime_adapter.run(
                RuntimeRequest(
                    run_id=f"model-test-{record.test_id}",
                    session_ref=None,
                    harness_home_ref=harness_home_ref,
                    prompt="Reply with the single token OK.",
                    skill_instructions=(),
                    allowed_tools=(),
                    employee_context=RuntimeEmployeeContext(
                        employee_id=f"model-test-{record.test_id}",
                        employee_version=1,
                        system_prompt="This is a bounded model availability test. Reply only with OK.",
                        model_route_id=model_snapshot.route_id,
                    ),
                    model_route_snapshot=model_snapshot,
                    max_tokens=16,
                    metadata={"execution_mode": "model_test", "tools": "disabled"},
                )
            )
            usage = runtime_result.usage
            usage_status = UsageStatus.ESTIMATED if usage.estimated else UsageStatus.EXACT
            if runtime_result.status.value == "succeeded" and runtime_result.text.strip():
                return ModelTestResult(
                    ModelTestState.SUCCEEDED,
                    latency_ms=elapsed(),
                    provider_request_id=usage.provider_request_id,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    total_tokens=usage.total_tokens,
                    usage_status=usage_status,
                )
            if runtime_result.status.value == "unknown":
                return ModelTestResult(
                    ModelTestState.UNKNOWN,
                    latency_ms=elapsed(),
                    provider_request_id=usage.provider_request_id,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    total_tokens=usage.total_tokens,
                    usage_status=usage_status,
                    error_category="MODEL_TEST_OUTCOME_UNKNOWN",
                )
            return ModelTestResult(
                ModelTestState.FAILED,
                latency_ms=elapsed(),
                provider_request_id=usage.provider_request_id,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                total_tokens=usage.total_tokens,
                usage_status=usage_status,
                error_category="MODEL_TEST_INVALID_RESPONSE",
            )
        except Exception as error:  # noqa: BLE001 — 只写入下方稳定错误分类
            from agent_platform.adapters.model_catalog import ModelCatalogError
            from agent_platform.adapters.model_secrets import ModelSecretError

            if isinstance(error, ModelCatalogError | ModelSecretError):
                return ModelTestResult(
                    ModelTestState.FAILED,
                    latency_ms=elapsed(),
                    error_category=error.category,
                )
            return self._runtime_failure(error, elapsed_ms=elapsed())

    def get_test_record(self, identity: IdentityContext, test_id: str) -> ModelTestRecord:
        self._require(identity, "model:read", "model-tests")
        return self.repository.get_model_test_record(identity, test_id)

    def get_route(self, identity: IdentityContext, route_id: str) -> ModelRoute:
        self._require(identity, "model:read", "model-routes")
        return self.routes.get(identity, route_id)

    def list_routes(self, identity: IdentityContext) -> tuple[ModelRoute, ...]:
        """列出当前租户的所有 ModelRoute，包含停用项供管理员恢复。"""

        self._require(identity, "model:read", "model-routes")
        return self.routes.list(identity)

    def create_route(
        self,
        identity: IdentityContext,
        *,
        alias: str,
        connection_id: str,
        model_id: str,
        parameters: Mapping[str, object] | None = None,
        pricing_snapshot_ref: str | None = None,
    ) -> ModelRoute:
        self._require(identity, "model:manage", "model-routes")
        return self.routes.create_from_catalog(
            identity,
            alias=alias,
            connection_id=connection_id,
            model_id=model_id,
            parameters=parameters,
            pricing_snapshot_ref=pricing_snapshot_ref,
        )

    def create_legacy_route(
        self,
        identity: IdentityContext,
        *,
        alias: str,
        model_name: str,
        provider: str,
        endpoint: str,
        secret_id: str | None,
    ) -> ModelRoute:
        self._require(identity, "model:manage", "model-routes")
        return self.routes.create(identity, alias, model_name, provider, endpoint, secret_id)

    def update_route(
        self,
        identity: IdentityContext,
        route_id: str,
        updates: Mapping[str, object],
        *,
        expected_route_version: int,
    ) -> ModelRoute:
        self._require(identity, "model:manage", "model-routes")
        return self.routes.update(
            identity,
            route_id,
            expected_route_version=expected_route_version,
            **dict(updates),
        )

    def revoke_route(
        self, identity: IdentityContext, route_id: str, expected_route_version: int
    ) -> ModelRoute:
        """只有租户管理员可以撤销路由，仓储负责事务化引用检查。"""

        self._require(identity, "model:manage", "model-routes")
        return self.routes.revoke(identity, route_id, expected_route_version)

    def set_route_status(
        self, identity: IdentityContext, route_id: str, status: str
    ) -> ModelRoute:
        self._require(identity, "model:manage", "model-routes")
        return self.routes.set_status(identity, route_id, status)

    def selectable_routes(self, identity: IdentityContext) -> tuple[ModelRoute, ...]:
        self._require(identity, "model:read", "model-routes")
        result: list[ModelRoute] = []
        for route in self.routes.list(identity):
            if route.status != "ACTIVE" or route.profile.connection_id is None:
                continue
            try:
                connection = self.repository.get_model_connection(
                    identity, route.profile.connection_id
                )
                model = self.repository.get_model_catalog_item(
                    identity,
                    route.profile.connection_id,
                    route.profile.catalog_model_id or "",
                )
            except DomainError as error:
                if error.code == "RESOURCE_NOT_FOUND":
                    continue
                raise
            if self.model_authorization.authorize(identity, connection, model):
                result.append(route)
        return tuple(result)
