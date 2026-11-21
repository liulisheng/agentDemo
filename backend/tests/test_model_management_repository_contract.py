from __future__ import annotations

from dataclasses import replace

import pytest

from agent_platform.adapters.identity.fake import FakeIdentityProvider
from agent_platform.application.catalog_service import InMemoryCatalogRepository
from agent_platform.domain import model_routes
from agent_platform.domain.common import DomainError


def _connection(connection_id: str, tenant_id: str):
    connection_type = getattr(model_routes, "ModelConnection", None)
    assert connection_type is not None, "tenant model connection domain object is missing"
    return connection_type(
        connection_id=connection_id,
        tenant_id=tenant_id,
        name=connection_id,
        provider_kind="openai-compatible",
        protocol="openai-chat-completions",
        endpoint="http://host.docker.internal:63072/v1",
    )


def _item(connection_id: str, tenant_id: str, model_id: str):
    item_type = getattr(model_routes, "ModelCatalogItem", None)
    assert item_type is not None, "tenant model catalog item domain object is missing"
    return item_type(
        connection_id=connection_id,
        tenant_id=tenant_id,
        model_id=model_id,
        display_name=model_id,
        capabilities={"chat": True},
        catalog_source="MANUAL",
    )


def test_in_memory_repository_scopes_connections_and_models_by_platform_and_tenant() -> None:
    identity_provider = FakeIdentityProvider()
    tenant_a = identity_provider.resolve_identity("admin-local")
    tenant_b = identity_provider.resolve_identity("admin-other")
    repository = InMemoryCatalogRepository()

    save_connection = getattr(repository, "save_model_connection", None)
    list_connections = getattr(repository, "list_model_connections", None)
    save_model = getattr(repository, "save_model_catalog_item", None)
    list_models = getattr(repository, "list_model_catalog_items", None)
    assert callable(save_connection), "catalog repository has no model connection port"
    assert callable(list_connections), "catalog repository has no model connection listing port"
    assert callable(save_model), "catalog repository has no model catalog write port"
    assert callable(list_models), "catalog repository has no model catalog listing port"

    connection = _connection("connection-a", tenant_a.tenant_id)
    save_connection(tenant_a, connection)
    save_model(tenant_a, _item("connection-a", tenant_a.tenant_id, "model-a"))
    save_model(tenant_a, _item("connection-a", tenant_a.tenant_id, "model-b"))

    assert [item.connection_id for item in list_connections(tenant_a)] == ["connection-a"]
    assert [item.model_id for item in list_models(tenant_a, "connection-a")] == ["model-a", "model-b"]
    assert list_connections(tenant_b) == ()
    with pytest.raises(DomainError) as hidden_connection:
        list_models(tenant_b, "connection-a")
    assert hidden_connection.value.code == "RESOURCE_NOT_FOUND"

    same_tenant_other_platform = replace(tenant_a, platform_id="plt-other")
    assert list_connections(same_tenant_other_platform) == ()
    with pytest.raises(DomainError) as hidden_platform_connection:
        list_models(same_tenant_other_platform, "connection-a")
    assert hidden_platform_connection.value.code == "RESOURCE_NOT_FOUND"


def test_in_memory_repository_rejects_cross_tenant_connection_and_orphan_model() -> None:
    identity_provider = FakeIdentityProvider()
    tenant_a = identity_provider.resolve_identity("admin-local")
    tenant_b = identity_provider.resolve_identity("admin-other")
    repository = InMemoryCatalogRepository()
    save_connection = getattr(repository, "save_model_connection", None)
    save_model = getattr(repository, "save_model_catalog_item", None)
    assert callable(save_connection), "catalog repository has no model connection port"
    assert callable(save_model), "catalog repository has no model catalog write port"

    with pytest.raises(DomainError) as cross_tenant:
        save_connection(tenant_a, _connection("connection-b", tenant_b.tenant_id))
    assert cross_tenant.value.code == "AUTHORIZATION_DENIED"

    with pytest.raises(DomainError) as orphan:
        save_model(tenant_a, _item("missing-connection", tenant_a.tenant_id, "model-a"))
    assert orphan.value.code == "RESOURCE_NOT_FOUND"


def test_legacy_model_route_write_creates_a_shared_connection_and_catalog_item() -> None:
    from agent_platform.domain.model_routes import ModelProfile, ModelRoute

    identity = FakeIdentityProvider().resolve_identity("admin-local")
    repository = InMemoryCatalogRepository()
    route = ModelRoute(
        "route-a",
        identity.tenant_id,
        "assistant",
        ModelProfile(
            "profile-a",
            identity.tenant_id,
            "model-a",
            "openai-compatible",
            "http://host.docker.internal:63072/v1",
        ),
    )

    repository.save_model_route(identity, route)
    saved_route = repository.get_model_route(identity, route.route_id)
    assert hasattr(saved_route.profile, "connection_id")
    assert saved_route.profile.connection_id is not None
    assert saved_route.profile.catalog_model_id == "model-a"
    assert len(repository.list_model_connections(identity)) == 1
    assert [item.model_id for item in repository.list_model_catalog_items(
        identity, saved_route.profile.connection_id
    )] == ["model-a"]
