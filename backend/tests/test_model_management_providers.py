from __future__ import annotations

import importlib
import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

import pytest

from agent_platform.adapters.identity.fake import FakeIdentityProvider
from agent_platform.application.catalog_service import InMemoryCatalogRepository
from agent_platform.domain.model_routes import ModelCatalogItem, ModelConnection, SecretRef


def _load_new_module(module_name: str):
    assert importlib.util.find_spec(module_name) is not None, f"{module_name} adapter is missing"
    return importlib.import_module(module_name)


def test_environment_secret_provider_uses_tenant_scoped_reference_and_redacts_value(monkeypatch) -> None:
    module = _load_new_module("agent_platform.adapters.model_secrets")
    provider_type = getattr(module, "EnvironmentModelSecretProvider", None)
    assert provider_type is not None, "environment model SecretProvider is missing"
    secret_value = "local-api-key-test-value"
    monkeypatch.setenv("AGENT_PLATFORM_MODEL_SECRET_TENANT_A_LOCAL_MODEL_KEY", secret_value)
    provider = provider_type()
    secret_ref = SecretRef("local-model-key", scope="tenant-a", provider="environment")

    resolved = provider.resolve(secret_ref)

    assert resolved == secret_value
    assert secret_value not in repr(provider)
    assert secret_value not in repr(secret_ref)
    connection = ModelConnection(
        connection_id="connection-a",
        tenant_id="tenant-a",
        name="local",
        provider_kind="openai-compatible",
        protocol="openai-chat-completions",
        endpoint="http://host.docker.internal:63072/v1",
        secret_ref=secret_ref,
    )
    assert secret_value not in repr(connection)
    assert secret_value not in json.dumps(connection.to_public_dict())


def test_environment_secret_provider_rejects_missing_or_unconfigured_reference(monkeypatch) -> None:
    module = _load_new_module("agent_platform.adapters.model_secrets")
    provider_type = getattr(module, "EnvironmentModelSecretProvider", None)
    error_type = getattr(module, "ModelSecretError", None)
    assert provider_type is not None and error_type is not None
    provider = provider_type()

    with pytest.raises(error_type) as missing:
        provider.resolve(SecretRef("missing-key", scope="tenant-a", provider="environment"))
    assert missing.value.category == "MODEL_SECRET_UNAVAILABLE"

    monkeypatch.setenv("AGENT_PLATFORM_MODEL_SECRET_TENANT_A_KEY", "not-used")
    with pytest.raises(error_type) as unsupported:
        provider.resolve(SecretRef("key", scope="tenant-a", provider="vault"))
    assert unsupported.value.category == "MODEL_SECRET_PROVIDER_UNSUPPORTED"


def test_openai_catalog_provider_reads_multiple_models_with_bearer_auth() -> None:
    module = _load_new_module("agent_platform.adapters.model_catalog")
    provider_type = getattr(module, "OpenAICompatibleModelCatalogProvider", None)
    assert provider_type is not None, "OpenAI-compatible model catalog provider is missing"

    class Handler(BaseHTTPRequestHandler):
        request_paths: ClassVar[list[str]] = []
        authorization_headers: ClassVar[list[str | None]] = []

        def do_GET(self) -> None:
            type(self).request_paths.append(self.path)
            type(self).authorization_headers.append(self.headers.get("Authorization"))
            body = json.dumps(
                {
                    "object": "list",
                    "data": [
                        {
                            "id": "chat-model-a",
                            "name": "Chat A",
                            "context_window": 8192,
                            "capabilities": {"chat": True, "vision": False},
                        },
                        {"id": "reasoning-model-b", "name": "Reasoning B"},
                    ],
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    Handler.request_paths.clear()
    Handler.authorization_headers.clear()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = ModelConnection(
            connection_id="connection-a",
            tenant_id="tenant-a",
            name="local",
            provider_kind="openai-compatible",
            protocol="openai-chat-completions",
            endpoint=f"http://127.0.0.1:{server.server_port}/v1",
        )
        provider = provider_type(timeout_seconds=2)

        items = provider.list_models(connection, credential="provider-key-test")

        assert [item.model_id for item in items] == ["chat-model-a", "reasoning-model-b"]
        assert items[0].display_name == "Chat A"
        assert items[0].context_window == 8192
        assert items[0].capabilities["chat"] is True
        assert Handler.request_paths == ["/v1/models"]
        assert Handler.authorization_headers == ["Bearer provider-key-test"]
        assert all(isinstance(item, ModelCatalogItem) for item in items)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_openai_catalog_provider_returns_sanitized_auth_error() -> None:
    module = _load_new_module("agent_platform.adapters.model_catalog")
    provider_type = getattr(module, "OpenAICompatibleModelCatalogProvider", None)
    error_type = getattr(module, "ModelCatalogError", None)
    assert provider_type is not None and error_type is not None

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            body = b'{"error":{"message":"raw provider detail"}}'
            self.send_response(401)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = ModelConnection(
            connection_id="connection-a",
            tenant_id="tenant-a",
            name="local",
            provider_kind="openai-compatible",
            protocol="openai-chat-completions",
            endpoint=f"http://127.0.0.1:{server.server_port}/v1",
        )
        with pytest.raises(error_type) as error:
            provider_type(timeout_seconds=2).list_models(connection, credential="secret-key")
        assert error.value.category == "MODEL_CREDENTIAL_INVALID"
        assert "raw provider detail" not in str(error.value)
        assert "secret-key" not in repr(error.value)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_manual_catalog_mode_allows_manual_entry_when_discovery_is_unavailable() -> None:
    module = _load_new_module("agent_platform.adapters.model_catalog")
    provider_type = getattr(module, "OpenAICompatibleModelCatalogProvider", None)
    error_type = getattr(module, "ModelCatalogError", None)
    assert provider_type is not None and error_type is not None
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    connection = ModelConnection(
        connection_id="manual-connection",
        tenant_id=identity.tenant_id,
        name="manual",
        provider_kind="openai-compatible",
        protocol="openai-chat-completions",
        endpoint="http://manual.example/v1",
        catalog_mode="MANUAL",
    )
    with pytest.raises(error_type) as discovery:
        provider_type().list_models(connection, credential=None)
    assert discovery.value.category == "MODEL_CATALOG_MANUAL"

    repository = InMemoryCatalogRepository()
    repository.save_model_connection(identity, connection)
    repository.save_model_catalog_item(
        identity,
        ModelCatalogItem(
            connection_id=connection.connection_id,
            tenant_id=identity.tenant_id,
            model_id="manually-entered-model",
            display_name="手工模型",
        ),
    )
    assert [
        item.model_id
        for item in repository.list_model_catalog_items(identity, connection.connection_id)
    ] == ["manually-entered-model"]


def test_platform_model_authorization_requires_active_same_tenant_connection_and_model() -> None:
    module = _load_new_module("agent_platform.adapters.model_authorization")
    authorizer_type = getattr(module, "PlatformModelAuthorizationProvider", None)
    assert authorizer_type is not None, "platform model authorization provider is missing"
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    connection = ModelConnection(
        connection_id="connection-a",
        tenant_id=identity.tenant_id,
        name="local",
        provider_kind="openai-compatible",
        protocol="openai-chat-completions",
        endpoint="http://host.docker.internal:63072/v1",
    )
    model = ModelCatalogItem(
        connection_id=connection.connection_id,
        tenant_id=connection.tenant_id,
        model_id="model-a",
        display_name="Model A",
    )
    authorizer = authorizer_type()

    assert authorizer.authorize(identity, connection, model) is True
    assert authorizer.authorize(identity, ModelConnection(
        connection_id="disabled",
        tenant_id=identity.tenant_id,
        name="disabled",
        provider_kind="openai-compatible",
        protocol="openai-chat-completions",
        endpoint="http://disabled/v1",
        status="DISABLED",
    ), model) is False
    assert authorizer.authorize(identity, connection, ModelCatalogItem(
        connection_id=connection.connection_id,
        tenant_id="tenant-other",
        model_id="model-a",
        display_name="Model A",
    )) is False
