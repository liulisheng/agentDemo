from __future__ import annotations

import pytest

from agent_platform.adapters.identity.fake import FakeAuthorizationProvider, FakeIdentityProvider
from agent_platform.adapters.model_authorization import PlatformModelAuthorizationProvider
from agent_platform.adapters.model_catalog import ModelCatalogError
from agent_platform.application.catalog_service import InMemoryCatalogRepository
from agent_platform.application.model_management import ModelManagementService
from agent_platform.application.model_routes import ModelRouteService
from agent_platform.domain.common import DomainError
from agent_platform.domain.execution import RuntimeStatus, UsageRecord
from agent_platform.domain.model_routes import (
    ModelCatalogItem,
    ModelCatalogMode,
    ModelConnection,
    ModelTestState,
    SecretRef,
    UsageStatus,
)
from agent_platform.ports.runtime import RuntimeCapabilities, RuntimeEvent, RuntimeResult


class _CatalogProvider:
    provider_kind = "openai-compatible"

    def __init__(self, items=(), error: Exception | None = None) -> None:
        self.items = tuple(items)
        self.error = error
        self.credentials: list[str | None] = []

    def list_models(self, connection, *, credential):
        self.credentials.append(credential)
        if self.error is not None:
            raise self.error
        return self.items


class _SecretProvider:
    def __init__(self, value: str = "test-key-value") -> None:
        self.value = value
        self.references = []

    def resolve(self, secret_ref):
        self.references.append(secret_ref)
        return self.value


class _Runtime:
    engine_name = "fake-harness"
    capabilities = RuntimeCapabilities()

    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.requests = []

    def run(self, request):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return RuntimeResult(
            text="OK",
            status=RuntimeStatus.SUCCEEDED,
            runtime_session_ref="fake-session",
            runtime_result_ref="fake-result",
            usage=UsageRecord(
                11,
                2,
                13,
                source="UPSTREAM",
                estimated=False,
                provider_request_id="model-test-request-1",
            ),
            events=(RuntimeEvent("run_finished", "RESULT", {"status": "succeeded"}),),
        )


class _HttpFailure(RuntimeError):
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


def _service(
    *,
    catalog_provider=None,
    secret_provider=None,
    runtime_adapter=None,
    model_authorization=None,
):
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    repository = InMemoryCatalogRepository()
    routes = ModelRouteService(repository)
    service = ModelManagementService(
        repository,
        routes,
        FakeAuthorizationProvider(),
        model_authorization or PlatformModelAuthorizationProvider(),
        catalog_provider=catalog_provider,
        secret_provider=secret_provider,
        runtime_adapter=runtime_adapter,
    )
    return identity, repository, routes, service


def _add_connection_and_route(identity, repository, routes, *, mode=ModelCatalogMode.REMOTE):
    connection = ModelConnection(
        connection_id="connection-tests",
        tenant_id=identity.tenant_id,
        name="测试连接",
        provider_kind="openai-compatible",
        protocol="openai-chat-completions",
        endpoint="http://host.docker.internal:63072/v1",
        secret_ref=SecretRef("test-key", identity.tenant_id, "environment"),
        catalog_mode=mode,
    )
    repository.save_model_connection(identity, connection)
    model = ModelCatalogItem(
        connection_id=connection.connection_id,
        tenant_id=identity.tenant_id,
        model_id="local-model-a",
        display_name="Local A",
    )
    repository.save_model_catalog_item(identity, model)
    route = routes.create_from_catalog(
        identity,
        alias="local-route",
        connection_id=connection.connection_id,
        model_id=model.model_id,
        parameters={"temperature": 0.2},
    )
    return connection, model, route


def _claim(repository, identity, test_id):
    claimed = repository.claim_next_model_test(
        identity.platform_id, "worker-test", lease_seconds=30
    )
    assert claimed is not None and claimed.test_id == test_id
    return claimed


def test_catalog_sync_resolves_secret_in_worker_and_saves_multiple_models() -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    repository = InMemoryCatalogRepository()
    routes = ModelRouteService(repository)
    connection = ModelConnection(
        connection_id="connection-sync",
        tenant_id=identity.tenant_id,
        name="本地连接",
        provider_kind="openai-compatible",
        protocol="openai-chat-completions",
        endpoint="http://localhost:63072/v1",
        secret_ref=SecretRef("catalog-key", identity.tenant_id, "environment"),
    )
    repository.save_model_connection(identity, connection)
    returned = (
        ModelCatalogItem(connection.connection_id, identity.tenant_id, "chat-a", "Chat A"),
        ModelCatalogItem(connection.connection_id, identity.tenant_id, "chat-b", "Chat B"),
    )
    catalog_provider = _CatalogProvider(returned)
    secret_provider = _SecretProvider("catalog-secret-value")
    service = ModelManagementService(
        repository,
        routes,
        FakeAuthorizationProvider(),
        PlatformModelAuthorizationProvider(),
        catalog_provider=catalog_provider,
        secret_provider=secret_provider,
    )
    queued = service.queue_catalog_sync(identity, connection.connection_id)
    claimed = _claim(repository, identity, queued.test_id)

    result = service.execute_test_record(claimed)

    assert result.state is ModelTestState.SUCCEEDED
    assert catalog_provider.credentials == ["catalog-secret-value"]
    assert secret_provider.references == [connection.secret_ref]
    assert [item.model_id for item in repository.list_model_catalog_items(identity, connection.connection_id)] == [
        "chat-a",
        "chat-b",
    ]
    assert "catalog-secret-value" not in repr(queued)
    assert "catalog-secret-value" not in repr(result)


def test_connection_test_maps_auth_and_unsupported_catalog_without_raw_errors() -> None:
    for category in ("MODEL_CREDENTIAL_INVALID", "MODEL_CATALOG_UNAVAILABLE"):
        identity, repository, _routes, _service_unused = _service()
        connection = ModelConnection(
            connection_id=f"connection-{category.lower()}",
            tenant_id=identity.tenant_id,
            name="测试连接",
            provider_kind="openai-compatible",
            protocol="openai-chat-completions",
            endpoint="http://localhost:63072/v1",
            secret_ref=SecretRef("test-key", identity.tenant_id, "environment"),
        )
        repository.save_model_connection(identity, connection)
        catalog_provider = _CatalogProvider(error=ModelCatalogError(category))
        secret_provider = _SecretProvider("private-secret-value")
        service = ModelManagementService(
            repository,
            ModelRouteService(repository),
            FakeAuthorizationProvider(),
            PlatformModelAuthorizationProvider(),
            catalog_provider=catalog_provider,
            secret_provider=secret_provider,
        )
        queued = service.queue_connection_test(identity, connection.connection_id)
        claimed = _claim(repository, identity, queued.test_id)

        result = service.execute_test_record(claimed)

        assert result.state is ModelTestState.FAILED
        assert result.error_category == category
        assert "private-secret-value" not in repr(result)
        assert "private-secret-value" not in repr(queued)


def test_model_test_uses_pinned_route_and_bounded_no_tool_synthetic_request() -> None:
    runtime = _Runtime()
    secret_provider = _SecretProvider("model-test-secret-value")
    identity, repository, routes, service = _service(
        runtime_adapter=runtime,
        secret_provider=secret_provider,
    )
    connection, _model, route = _add_connection_and_route(identity, repository, routes)

    with pytest.raises(DomainError) as not_confirmed:
        service.queue_model_test(identity, route.route_id, confirmed_cost=False)
    assert not_confirmed.value.code == "INVALID_ARGUMENT"
    queued = service.queue_model_test(identity, route.route_id, confirmed_cost=True)
    claimed = _claim(repository, identity, queued.test_id)

    result = service.execute_test_record(claimed)

    request = runtime.requests[0]
    assert request.model_route_snapshot.model_id == "local-model-a"
    assert request.model_route_snapshot.endpoint == connection.endpoint
    assert request.max_tokens == 16
    assert request.allowed_tools == ()
    assert request.tool_definitions == ()
    assert request.prompt == "Reply with the single token OK."
    assert request.metadata["execution_mode"] == "model_test"
    assert result.state is ModelTestState.SUCCEEDED
    assert result.provider_request_id == "model-test-request-1"
    assert result.usage_status is UsageStatus.EXACT
    assert result.input_tokens == 11
    assert result.output_tokens == 2
    assert "model-test-secret-value" not in repr(request)
    assert "model-test-secret-value" not in repr(queued)


def test_model_test_maps_model_not_found_timeout_and_provider_error() -> None:
    cases = (
        (_HttpFailure(404), ModelTestState.FAILED, "MODEL_NOT_FOUND"),
        (TimeoutError("do not persist this"), ModelTestState.UNKNOWN, "MODEL_TEST_TIMEOUT"),
        (_HttpFailure(503), ModelTestState.FAILED, "MODEL_PROVIDER_ERROR"),
    )
    for error, state, category in cases:
        runtime = _Runtime(error=error)
        identity, repository, routes, service = _service(runtime_adapter=runtime)
        _connection, _model, route = _add_connection_and_route(identity, repository, routes)
        queued = service.queue_model_test(identity, route.route_id, confirmed_cost=True)
        claimed = _claim(repository, identity, queued.test_id)

        result = service.execute_test_record(claimed)

        assert result.state is state
        assert result.error_category == category
        assert "do not persist this" not in repr(result)
        assert "do not persist this" not in repr(queued)
