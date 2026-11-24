import pytest
from fastapi.testclient import TestClient

from agent_platform.adapters.runtime.codex_app_server import CodexAppServerRuntimeAdapter
from agent_platform.adapters.runtime.fake import FakeRuntimeAdapter
from agent_platform.api.app import create_app
from agent_platform.application.engine_providers import (
    EngineProviderCapabilities,
    EngineProviderRegistry,
)
from agent_platform.domain.catalog import ExecutionProfile
from agent_platform.domain.common import DomainError
from agent_platform.domain.deployment_runtime import RuntimeOptions
from agent_platform.domain.model_routes import ModelRouteSnapshot, SecretRef


def _route(protocol: str) -> ModelRouteSnapshot:
    return ModelRouteSnapshot(
        tenant_id="tenant-a",
        route_id="route-a",
        route_version=2,
        route_alias="OpenAI Route",
        connection_id="connection-a",
        connection_version=3,
        provider_kind="openai-compatible",
        model_id="model-a",
        protocol=protocol,
        endpoint="https://model.invalid/v1",
        secret_ref=SecretRef("tenant-key", scope="tenant-a", provider="environment"),
    )


def test_registry_lists_only_registered_enabled_providers_and_never_falls_back() -> None:
    registry = EngineProviderRegistry()
    registry.register(
        "fake",
        FakeRuntimeAdapter(),
        display_name="Fake Runtime",
        model_protocols={"openai-chat-completions", "openai-responses"},
    )
    registry.register(
        "claude_code",
        FakeRuntimeAdapter(),
        display_name="Claude Code",
        model_protocols={"anthropic-messages"},
        enabled=False,
        validation_note="Adapter 尚未通过生命周期和隔离验证",
    )

    assert [item["engine_name"] for item in registry.available()] == ["fake"]
    with pytest.raises(DomainError) as unavailable:
        registry.resolve("codex_cli")
    assert unavailable.value.code == "ENGINE_PROVIDER_UNAVAILABLE"
    with pytest.raises(DomainError) as not_enabled:
        registry.resolve("claude_code")
    assert not_enabled.value.code == "ENGINE_PROVIDER_UNAVAILABLE"


def test_registry_rejects_incompatible_route_without_replacing_user_choice() -> None:
    registry = EngineProviderRegistry()
    registry.register(
        "deepseek_harness",
        FakeRuntimeAdapter(),
        display_name="DeepSeek Harness",
        model_protocols={"openai-chat-completions"},
    )
    profile = ExecutionProfile(
        profile_id="deepseek-profile",
        tenant_id="tenant-a",
        engine_name="deepseek_harness",
    ).publish()

    with pytest.raises(DomainError) as incompatible:
        registry.validate(profile, _route("openai-responses"))

    assert incompatible.value.code == "ENGINE_MODEL_INCOMPATIBLE"
    assert incompatible.value.details["model_route_id"] == "route-a"


def test_registry_rejects_requested_capabilities_missing_from_adapter() -> None:
    registry = EngineProviderRegistry()
    registry.register(
        "codex_cli",
        FakeRuntimeAdapter(),
        display_name="Codex CLI",
        model_protocols={"openai-responses"},
        capabilities=EngineProviderCapabilities(
            resume=True,
            cancellation=True,
            tool_calls=False,
            mcp=False,
            workspace=True,
            sandbox=False,
            approvals=False,
        ),
    )
    profile = ExecutionProfile(
        profile_id="codex-profile",
        tenant_id="tenant-a",
        engine_name="codex_cli",
        sandbox_profile_id="sandbox-a",
    ).publish()

    with pytest.raises(DomainError) as unsupported:
        registry.validate(profile, _route("openai-responses"), requires_tools=True)

    assert unsupported.value.code == "ENGINE_CAPABILITY_UNSUPPORTED"
    assert unsupported.value.details["engine_name"] == "codex_cli"
    assert unsupported.value.details["capability"] == "tools"


def test_registry_rejects_an_unavailable_sandbox_requirement() -> None:
    registry = EngineProviderRegistry()
    registry.register(
        "codex_cli",
        FakeRuntimeAdapter(),
        display_name="Codex CLI",
        model_protocols={"openai-responses"},
        capabilities=EngineProviderCapabilities(
            resume=True,
            cancellation=True,
            tool_calls=True,
            workspace=True,
            sandbox=False,
        ),
    )
    profile = ExecutionProfile(
        profile_id="codex-profile",
        tenant_id="tenant-a",
        engine_name="codex_cli",
        sandbox_profile_id="sandbox-a",
    ).publish()

    with pytest.raises(DomainError) as unsupported:
        registry.validate(profile, _route("openai-responses"))

    assert unsupported.value.code == "ENGINE_CAPABILITY_UNSUPPORTED"
    assert unsupported.value.details["capability"] == "sandbox"


def test_registry_rejects_deep_reasoning_when_provider_does_not_support_it() -> None:
    registry = EngineProviderRegistry()
    registry.register(
        "codex_cli",
        FakeRuntimeAdapter(),
        display_name="Codex CLI",
        model_protocols={"openai-responses"},
        capabilities=EngineProviderCapabilities(model_route_required=True),
    )
    profile = ExecutionProfile(
        profile_id="codex-profile",
        tenant_id="tenant-a",
        engine_name="codex_cli",
    ).publish()

    with pytest.raises(DomainError) as unsupported:
        registry.validate(
            profile,
            _route("openai-responses"),
            runtime_options=RuntimeOptions.from_mapping({"reasoning_mode": "DEEP"}),
        )

    assert unsupported.value.code == "ENGINE_CAPABILITY_UNSUPPORTED"
    assert unsupported.value.details["capability"] == "reasoning"


def test_fake_provider_can_validate_reasoning_options_for_deterministic_contract_tests() -> None:
    registry = EngineProviderRegistry()
    registry.register(
        "fake",
        FakeRuntimeAdapter(),
        display_name="Fake Runtime",
        model_protocols={"openai-chat-completions"},
    )
    profile = ExecutionProfile(
        profile_id="fake-profile",
        tenant_id="tenant-a",
        engine_name="fake",
    ).publish()

    provider = registry.validate(
        profile,
        _route("openai-chat-completions"),
        runtime_options=RuntimeOptions.from_mapping(
            {"reasoning_mode": "DEEP", "reasoning_effort": "HIGH", "reasoning_budget_tokens": 8192}
        ),
    )

    assert provider.engine_name == "fake"


def test_codex_provider_requires_a_supported_route_with_recipient_credentials(tmp_path) -> None:
    adapter = CodexAppServerRuntimeAdapter(
        command=("codex", "app-server", "--stdio"),
        state_root=tmp_path,
        secret_resolver=lambda _reference: "secret",
        connection_factory=lambda *_args: None,
    )
    registry = EngineProviderRegistry.for_runtime(adapter)
    profile = ExecutionProfile(
        profile_id="codex-profile",
        tenant_id="tenant-a",
        engine_name="codex_cli",
    ).publish()

    with pytest.raises(DomainError) as no_route:
        registry.validate(profile, None)
    assert no_route.value.code == "RUNTIME_CONFIGURATION_REQUIRED"

    no_secret_route = ModelRouteSnapshot(
        tenant_id="tenant-a",
        route_id="route-a",
        route_version=1,
        route_alias="No Secret",
        connection_id="connection-a",
        connection_version=1,
        provider_kind="openai-compatible",
        model_id="model-a",
        protocol="openai-responses",
        endpoint="https://model.invalid/v1",
    )
    with pytest.raises(DomainError) as no_secret:
        registry.validate(profile, no_secret_route)
    assert no_secret.value.code == "ENGINE_CONFIGURATION_REQUIRED"


def test_codex_runtime_is_registered_only_after_explicit_operator_enablement(
    monkeypatch, tmp_path
) -> None:
    import sys

    from agent_platform.adapters.model_secrets import EnvironmentModelSecretProvider
    from agent_platform.api.app import _register_codex_app_server_provider

    registry = EngineProviderRegistry.for_runtime(FakeRuntimeAdapter())
    assert "codex_cli" not in registry.available_names()
    monkeypatch.setenv("AGENT_PLATFORM_CODEX_APP_SERVER_ENABLED", "1")
    monkeypatch.setenv("AGENT_PLATFORM_CODEX_APP_SERVER_COMMAND", sys.executable)
    adapter = _register_codex_app_server_provider(
        registry, tmp_path / "codex-runtime", EnvironmentModelSecretProvider()
    )

    assert isinstance(adapter, CodexAppServerRuntimeAdapter)
    codex = next(item for item in registry.available() if item["engine_name"] == "codex_cli")
    assert codex["execution_mode"] == "NATIVE"
    assert codex["supported_model_protocols"] == ["openai-responses"]
    assert codex["capabilities"]["model_route_required"] is True
    assert codex["capabilities"]["route_secret_required"] is True
    assert "command" not in str(codex)
    adapter.close()


def test_deepseek_runtime_exposes_one_canonical_provider_and_resolves_legacy_alias() -> None:
    class DeepSeekRuntime(FakeRuntimeAdapter):
        engine_name = "deepseek"

    adapter = DeepSeekRuntime()
    registry = EngineProviderRegistry.for_runtime(adapter)

    assert registry.available_names() == ("deepseek_harness",)
    assert [item["engine_name"] for item in registry.available()] == ["deepseek_harness"]
    assert registry.resolve("deepseek").adapter is adapter
    assert registry.resolve("deepseek_harness").adapter is adapter


def test_explicit_fake_runtime_does_not_resolve_deepseek_provider() -> None:
    registry = EngineProviderRegistry.for_runtime(FakeRuntimeAdapter())

    assert registry.available_names() == ("fake",)
    with pytest.raises(DomainError) as unavailable:
        registry.resolve("deepseek")
    assert unavailable.value.code == "ENGINE_PROVIDER_UNAVAILABLE"


def test_engine_provider_api_exposes_only_registered_choices_and_marks_simulation() -> None:
    client = TestClient(create_app())
    admin = {"X-Fake-User": "admin-local"}
    providers = client.get("/api/v1/engine-providers", headers=admin)
    assert providers.status_code == 200
    by_name = {item["engine_name"]: item for item in providers.json()["items"]}
    assert by_name["fake"]["execution_mode"] == "SIMULATION"
    assert by_name["codex_cli"]["execution_mode"] == "SIMULATION"
    assert by_name["deepseek_harness"]["execution_mode"] == "SIMULATION"
    assert "deepseek" not in by_name
    assert "claude_code" not in by_name
    assert all("command" not in item and "secret_ref" not in str(item) for item in by_name.values())

    unavailable = client.post(
        "/api/v1/execution-profiles",
        headers=admin,
        json={"engine_name": "claude_code"},
    )
    assert unavailable.status_code == 409
    assert unavailable.json()["code"] == "ENGINE_PROVIDER_UNAVAILABLE"
    normalized_alias = client.post(
        "/api/v1/execution-profiles",
        headers=admin,
        json={"engine_name": "codex-cli"},
    )
    assert normalized_alias.status_code == 201, normalized_alias.text
    assert normalized_alias.json()["engine_name"] == "codex_cli"
    client.close()


def test_registered_deepseek_profile_can_be_created_and_published_only_by_admin() -> None:
    from agent_platform.adapters.identity.fake import FakeIdentityProvider

    class DeepSeekRuntime(FakeRuntimeAdapter):
        engine_name = "deepseek"

    adapter = DeepSeekRuntime()
    registry = EngineProviderRegistry.for_runtime(adapter)
    client = TestClient(create_app(runtime_adapter=adapter, engine_provider_registry=registry))
    admin = {"X-Fake-User": "admin-local"}
    member = {"X-Fake-User": "member-local"}

    providers = client.get("/api/v1/engine-providers", headers=admin)
    assert providers.status_code == 200
    assert [item["engine_name"] for item in providers.json()["items"]] == ["deepseek_harness"]
    assert providers.json()["items"][0]["execution_mode"] == "NATIVE"

    created = client.post(
        "/api/v1/execution-profiles",
        headers=admin,
        json={"engine_name": "deepseek_harness"},
    )
    assert created.status_code == 201, created.text
    assert created.json()["engine_name"] == "deepseek_harness"
    assert created.json()["status"] == "PUBLISHED"
    assert created.json()["engine_available"] is True

    identity = FakeIdentityProvider().resolve_identity("admin-local")
    legacy_profile = ExecutionProfile(
        profile_id="legacy-deepseek-profile",
        tenant_id=identity.tenant_id,
        engine_name="deepseek",
    ).publish()
    client.app.state.catalog_service.repository.save_profile(identity, legacy_profile)
    legacy_response = client.get(
        f"/api/v1/execution-profiles/{legacy_profile.profile_id}/versions",
        headers=admin,
    )
    assert legacy_response.status_code == 200
    assert legacy_response.json()["items"][0]["engine_name"] == "deepseek_harness"
    assert legacy_response.json()["items"][0]["engine_available"] is True

    denied = client.post(
        "/api/v1/execution-profiles",
        headers=member,
        json={"engine_name": "deepseek_harness"},
    )
    assert denied.status_code == 403

    unavailable = client.post(
        "/api/v1/execution-profiles",
        headers=admin,
        json={"engine_name": "unregistered_harness"},
    )
    assert unavailable.status_code == 409
    assert unavailable.json()["code"] == "ENGINE_PROVIDER_UNAVAILABLE"
    client.close()
