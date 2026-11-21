from __future__ import annotations

import pytest

from agent_platform.domain import model_routes


def test_model_connection_is_tenant_scoped_and_redacts_secret_reference() -> None:
    connection_type = getattr(model_routes, "ModelConnection", None)
    assert connection_type is not None, "tenant model connection domain object is missing"

    connection = connection_type(
        connection_id="connection-a",
        tenant_id="tenant-a",
        name="本地模型",
        provider_kind="openai-compatible",
        protocol="openai-chat-completions",
        endpoint="http://host.docker.internal:63072/v1",
        secret_ref=model_routes.SecretRef("local-model-key", scope="tenant-a", provider="environment"),
    )

    assert connection.to_public_dict()["tenant_id"] == "tenant-a"
    assert connection.to_public_dict()["secret_ref"]["secret_id"] == "local-model-key"
    assert "api-key-value" not in repr(connection)
    assert connection.version == 1


def test_model_connection_rejects_invalid_version_and_catalog_mode() -> None:
    connection_type = getattr(model_routes, "ModelConnection", None)
    assert connection_type is not None, "tenant model connection domain object is missing"

    values = {
        "connection_id": "connection-a",
        "tenant_id": "tenant-a",
        "name": "本地模型",
        "provider_kind": "openai-compatible",
        "protocol": "openai-chat-completions",
        "endpoint": "http://localhost/v1",
    }
    with pytest.raises(ValueError):
        connection_type(**values, version=0)
    with pytest.raises(ValueError):
        connection_type(**values, catalog_mode="AUTOMATIC")


def test_catalog_item_freezes_capabilities_and_validates_model_identity() -> None:
    item_type = getattr(model_routes, "ModelCatalogItem", None)
    assert item_type is not None, "tenant model catalog item domain object is missing"

    capabilities = {"chat": True, "vision": False}
    item = item_type(
        connection_id="connection-a",
        tenant_id="tenant-a",
        model_id="model-a",
        display_name="模型 A",
        capabilities=capabilities,
        context_window=4096,
        catalog_source="MANUAL",
    )
    capabilities["chat"] = False

    assert item.capabilities["chat"] is True
    assert item.to_public_dict()["model_id"] == "model-a"
    with pytest.raises(ValueError):
        item_type(
            connection_id="connection-a",
            tenant_id="tenant-a",
            model_id="model-a",
            display_name="模型 A",
            context_window=0,
        )


def test_model_route_carries_connection_model_identity_and_version() -> None:
    profile_type = model_routes.ModelProfile
    route_type = model_routes.ModelRoute
    assert "connection_id" in profile_type.__dataclass_fields__
    assert "catalog_model_id" in profile_type.__dataclass_fields__
    assert "route_version" in route_type.__dataclass_fields__
    profile = profile_type(
        profile_id="profile-a",
        tenant_id="tenant-a",
        model_name="model-a",
        provider="openai-compatible",
        endpoint="http://host.docker.internal:63072/v1",
        connection_id="connection-a",
        catalog_model_id="model-a",
    )
    route = route_type("route-a", "tenant-a", "assistant", profile, route_version=3)

    assert route.to_public_dict()["route_version"] == 3
    assert route.to_public_dict()["profile"]["connection_id"] == "connection-a"
    assert route.to_public_dict()["profile"]["catalog_model_id"] == "model-a"
    with pytest.raises(ValueError):
        profile_type(
            profile_id="profile-b",
            tenant_id="tenant-a",
            model_name="model-a",
            provider="openai-compatible",
            endpoint="http://host.docker.internal:63072/v1",
            connection_id="connection-a",
        )
