"""模型 Provider 适配器：Fake、直接入口以及未来网关边界。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from agent_platform.domain.model_routes import ModelRoute, normalize_usage
from agent_platform.ports.model_provider import ModelRequest, ModelResponse, SecretProvider


class ModelProviderError(RuntimeError):
    """供应商调用失败；异常消息不包含认证明文或完整响应。"""


class _MappingSecretProvider:
    def __init__(self, values: Mapping[str, str]) -> None:
        self._values = dict(values)

    def resolve(self, secret_ref: object) -> str:
        secret_id = getattr(secret_ref, "secret_id", None)
        if not isinstance(secret_id, str) or secret_id not in self._values:
            raise ModelProviderError("model secret could not be resolved")
        return self._values[secret_id]


class FakeModelProvider:
    """不访问网络的确定性 Provider，用于契约和端到端测试。"""

    provider_name = "fake"

    def __init__(
        self,
        *,
        text: str = "fake response",
        usage: Mapping[str, object] | None = None,
        provider_request_id: str = "fake-request-1",
        error: str | None = None,
    ) -> None:
        self.text = text
        self._usage = dict(usage) if usage is not None else None
        self._provider_request_id = provider_request_id
        self._error = error

    def complete(self, request: ModelRequest) -> ModelResponse:
        if self._error:
            raise ModelProviderError(self._error)
        usage = normalize_usage(
            self._usage,
            estimation_method="fake-unavailable" if self._usage is None else None,
            provider_request_id=self._provider_request_id,
            pricing_snapshot_ref=request.route.profile.pricing_snapshot_ref,
        )
        return ModelResponse(
            text=self.text,
            usage=usage,
            provider_request_id=self._provider_request_id,
            model_name=request.route.model_name,
        )


Transport = Callable[[str, dict[str, object], dict[str, str]], Mapping[str, object]]


class DirectModelProvider:
    """调用已配置的直接模型端点，Secret 仅存在于本次 transport 调用作用域。"""

    provider_name = "direct"

    def __init__(
        self,
        *,
        secret_values: Mapping[str, str] | None = None,
        secret_provider: SecretProvider | None = None,
        transport: Transport | None = None,
    ) -> None:
        self._secret_provider = secret_provider or _MappingSecretProvider(secret_values or {})
        self._transport = transport

    def complete(self, request: ModelRequest) -> ModelResponse:
        if self._transport is None:
            raise ModelProviderError("direct model transport is not configured")
        route = request.route
        secret = ""
        if route.profile.secret_ref is not None:
            secret = self._secret_provider.resolve(route.profile.secret_ref)
        payload: dict[str, object] = {"model": route.model_name, "prompt": request.prompt}
        if request.system_prompt:
            payload["system_prompt"] = request.system_prompt
        headers = {"Authorization": f"Bearer {secret}"} if secret else {}
        try:
            raw = self._transport(route.profile.endpoint, payload, headers)
        except ModelProviderError:
            raise
        except Exception as exc:  # 适配器边界只暴露稳定错误类别
            raise ModelProviderError("direct model request failed") from exc
        if raw.get("error"):
            raise ModelProviderError("model provider returned an error")
        text = raw.get("text", raw.get("content"))
        if not isinstance(text, str):
            raise ModelProviderError("model provider response did not contain text")
        provider_request_id = raw.get("id", raw.get("request_id"))
        provider_request_id = str(provider_request_id) if provider_request_id is not None else None
        usage_raw = raw.get("usage")
        usage = normalize_usage(
            usage_raw if isinstance(usage_raw, Mapping) else None,
            estimation_method="provider-omitted:v1" if not isinstance(usage_raw, Mapping) else None,
            provider_request_id=provider_request_id,
            pricing_snapshot_ref=route.profile.pricing_snapshot_ref,
        )
        return ModelResponse(text=text, usage=usage, provider_request_id=provider_request_id, model_name=route.model_name)


def _static_transport(text: str) -> Transport:
    def transport(_endpoint: str, _payload: dict[str, object], _headers: dict[str, str]) -> Mapping[str, object]:
        return {"text": text, "id": f"{text}-request", "usage": {"input_tokens": 0, "output_tokens": 0}}

    return transport


class _StaticSecretProvider:
    """仅用于不联网的静态契约 Provider，不读取或保存真实凭据。"""

    def resolve(self, _secret_ref: object) -> str:
        return ""


class NewApiModelProvider(DirectModelProvider):
    """new-api 的可替换适配器边界，领域对象无需知道网关协议细节。"""

    provider_name = "new-api"

    def __init__(self, *, text: str | None = None, **kwargs: Any) -> None:
        if text is not None and "transport" not in kwargs:
            kwargs["transport"] = _static_transport(text)
            kwargs.setdefault("secret_provider", _StaticSecretProvider())
        super().__init__(**kwargs)


class DadiModelGatewayProvider(DirectModelProvider):
    """Dadi Model Gateway 的可替换适配器边界。"""

    provider_name = "dadi"

    def __init__(self, *, text: str | None = None, **kwargs: Any) -> None:
        if text is not None and "transport" not in kwargs:
            kwargs["transport"] = _static_transport(text)
            kwargs.setdefault("secret_provider", _StaticSecretProvider())
        super().__init__(**kwargs)


# 常见缩写保留为兼容导出。
NewAPIModelProvider = NewApiModelProvider
DadiGatewayModelProvider = DadiModelGatewayProvider


class ProviderRegistry:
    """按 Run 启动时的 ModelRoute 选择 Provider；已启动请求保留其路由对象。"""

    def __init__(self, providers: Mapping[str, object] | None = None) -> None:
        self._providers = dict(providers or {})

    def register(self, provider: object) -> None:
        name = getattr(provider, "provider_name", None)
        if not isinstance(name, str) or not name.strip():
            raise ValueError("provider_name must not be blank")
        self._providers[name] = provider

    def resolve(self, route: ModelRoute):
        try:
            return self._providers[route.provider]
        except KeyError as exc:
            raise ModelProviderError(f"model provider is not registered: {route.provider}") from exc
