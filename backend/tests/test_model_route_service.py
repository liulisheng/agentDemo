import pytest

from agent_platform.adapters.identity.fake import FakeIdentityProvider
from agent_platform.application.catalog_service import InMemoryCatalogRepository
from agent_platform.application.model_routes import InMemoryModelRouteRepository, ModelRouteService
from agent_platform.domain.common import DomainError
from agent_platform.domain.model_routes import ModelCatalogItem, ModelConnection, SecretRef


def test_model_route_service_keeps_secret_reference_only() -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    service = ModelRouteService(InMemoryModelRouteRepository())
    route = service.create(identity, "fake-route", "fake-model", "fake", "http://fake", "secret-1")
    assert route.to_public_dict()["profile"]["secret_ref"]["secret_id"] == "secret-1"
    assert "api-key" not in repr(route)
    assert service.list(identity)[0].route_id == route.route_id


def test_model_route_is_created_from_tenant_catalog_and_versioned_on_update() -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    repository = InMemoryCatalogRepository()
    repository.save_model_connection(
        identity,
        ModelConnection(
            connection_id="connection-a",
            tenant_id=identity.tenant_id,
            name="local",
            provider_kind="openai-compatible",
            protocol="openai-chat-completions",
            endpoint="http://host.docker.internal:63072/v1",
            secret_ref=SecretRef("model-key", scope=identity.tenant_id, provider="environment"),
        ),
    )
    repository.save_model_catalog_item(
        identity,
        ModelCatalogItem(
            connection_id="connection-a",
            tenant_id=identity.tenant_id,
            model_id="model-a",
            display_name="Model A",
        ),
    )
    service = ModelRouteService(repository)
    create_from_catalog = getattr(service, "create_from_catalog", None)
    assert callable(create_from_catalog), "ModelRouteService cannot create a catalog-backed route"

    route = create_from_catalog(
        identity,
        alias="assistant",
        connection_id="connection-a",
        model_id="model-a",
    )
    assert route.profile.connection_id == "connection-a"
    assert route.profile.catalog_model_id == "model-a"
    assert route.route_version == 1

    update = getattr(service, "update", None)
    assert callable(update), "ModelRouteService cannot publish a new route version"
    updated = update(identity, route.route_id, alias="assistant-v2")
    assert updated.alias == "assistant-v2"
    assert updated.route_version == 2
    assert updated.profile.endpoint == route.profile.endpoint


def test_model_route_service_rejects_cross_tenant_or_inactive_catalog_items() -> None:
    identity_provider = FakeIdentityProvider()
    identity = identity_provider.resolve_identity("admin-local")
    other = identity_provider.resolve_identity("admin-other")
    repository = InMemoryCatalogRepository()
    service = ModelRouteService(repository)
    create_from_catalog = getattr(service, "create_from_catalog", None)
    assert callable(create_from_catalog), "ModelRouteService cannot create a catalog-backed route"

    with pytest.raises(DomainError) as cross_tenant:
        create_from_catalog(
            identity,
            alias="cross-tenant",
            connection_id="other-connection",
            model_id="model-a",
        )
    assert cross_tenant.value.code == "RESOURCE_NOT_FOUND"

    connection = ModelConnection(
        connection_id="disabled-connection",
        tenant_id=identity.tenant_id,
        name="disabled",
        provider_kind="openai-compatible",
        protocol="openai-chat-completions",
        endpoint="http://host.docker.internal:63072/v1",
        status="DISABLED",
    )
    repository.save_model_connection(identity, connection)
    repository.save_model_catalog_item(
        identity,
        ModelCatalogItem(
            connection_id=connection.connection_id,
            tenant_id=identity.tenant_id,
            model_id="model-a",
            display_name="Model A",
        ),
    )
    with pytest.raises(DomainError) as disabled:
        create_from_catalog(
            identity,
            alias="disabled",
            connection_id=connection.connection_id,
            model_id="model-a",
        )
    assert disabled.value.code == "DEPENDENCY_VALIDATION_ERROR"
    assert other.tenant_id != identity.tenant_id
