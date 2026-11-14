from dataclasses import FrozenInstanceError

import pytest

from agent_platform.adapters.model_provider import (
    DadiModelGatewayProvider,
    DirectModelProvider,
    FakeModelProvider,
    NewApiModelProvider,
    ProviderRegistry,
)
from agent_platform.domain.model_routes import (
    ModelCallUsage,
    ModelProfile,
    ModelRoute,
    SecretRef,
    UsageStatus,
    normalize_usage,
)
from agent_platform.ports.model_provider import ModelRequest


def _route(provider: str = "direct") -> ModelRoute:
    profile = ModelProfile(
        profile_id="profile-1",
        tenant_id="tenant-a",
        model_name="deepseek-chat",
        provider=provider,
        endpoint="https://model.example/v1",
        protocol="openai-compatible",
        secret_ref=SecretRef("secret-1", scope="tenant"),
    )
    return ModelRoute(
        route_id="route-1",
        tenant_id="tenant-a",
        alias="default",
        profile=profile,
    )


def test_route_profile_and_secret_reference_are_immutable_and_secret_safe() -> None:
    route = _route()
    with pytest.raises(FrozenInstanceError):
        route.alias = "changed"  # type: ignore[misc]

    rendered = repr(route)
    assert "secret-1" not in rendered
    assert "api-key-value" not in rendered
    assert "api_key" not in route.to_public_dict()
    assert "secret_ref" in route.to_public_dict()["profile"]
    assert "api-key-value" not in route.to_snapshot_json()


def test_fake_provider_returns_text_exact_usage_and_request_id() -> None:
    provider = FakeModelProvider(
        text="已完成",
        usage={"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
        provider_request_id="fake-request-1",
    )
    result = provider.complete(ModelRequest(route=_route(), prompt="执行任务", run_id="run-1"))

    assert result.text == "已完成"
    assert result.provider_request_id == "fake-request-1"
    assert result.usage.usage_status is UsageStatus.EXACT
    assert result.usage.total_tokens == 5


def test_fake_provider_exposes_model_error_without_provider_payload() -> None:
    provider = FakeModelProvider(error="upstream unavailable")

    with pytest.raises(RuntimeError, match="upstream unavailable"):
        provider.complete(ModelRequest(route=_route(), prompt="执行任务", run_id="run-error"))


def test_direct_provider_resolves_secret_only_for_transport() -> None:
    seen: dict[str, object] = {}

    def transport(endpoint: str, payload: dict[str, object], headers: dict[str, str]):
        seen.update(endpoint=endpoint, payload=payload, headers=headers)
        return {
            "text": "直连结果",
            "id": "provider-1",
            "usage": {"prompt_tokens": 4, "completion_tokens": 1, "total_tokens": 5},
        }

    provider = DirectModelProvider(
        secret_values={"secret-1": "api-key-value"},
        transport=transport,
    )
    result = provider.complete(ModelRequest(route=_route(), prompt="你好", run_id="run-2"))

    assert result.text == "直连结果"
    assert result.usage.input_tokens == 4
    assert seen["headers"] == {"Authorization": "Bearer api-key-value"}
    assert "api-key-value" not in repr(result)


def test_future_gateway_adapters_share_contract_and_registry_selection_is_per_route() -> None:
    request = ModelRequest(route=_route("new-api"), prompt="a", run_id="run-3")
    new_api = NewApiModelProvider(text="new-api")
    dadi = DadiModelGatewayProvider(text="dadi")
    registry = ProviderRegistry({"new-api": new_api, "dadi": dadi})

    assert registry.resolve(request.route).complete(request).text == "new-api"
    assert registry.resolve(_route("dadi")).complete(
        ModelRequest(route=_route("dadi"), prompt="b", run_id="run-4")
    ).text == "dadi"


def test_missing_upstream_usage_is_estimated_with_method_and_pricing_snapshot() -> None:
    usage = normalize_usage(
        None,
        estimation_method="chars_to_tokens:v1",
        estimated_input_tokens=8,
        estimated_output_tokens=2,
        pricing_snapshot_ref="pricing-2026-01",
    )

    assert usage == ModelCallUsage(
        input_tokens=8,
        output_tokens=2,
        total_tokens=10,
        usage_status=UsageStatus.ESTIMATED,
        estimation_method="chars_to_tokens:v1",
        pricing_snapshot_ref="pricing-2026-01",
    )
    assert usage.cost is None


def test_direct_provider_marks_missing_upstream_usage_as_estimated() -> None:
    provider = DirectModelProvider(
        secret_values={"secret-1": "api-key-value"},
        transport=lambda _endpoint, _payload, _headers: {"text": "无 usage", "id": "request-2"},
    )

    result = provider.complete(ModelRequest(route=_route(), prompt="你好", run_id="run-5"))

    assert result.usage.usage_status is UsageStatus.ESTIMATED
    assert result.usage.estimation_method == "provider-omitted:v1"
