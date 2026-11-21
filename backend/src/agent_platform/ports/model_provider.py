"""与具体模型供应商解耦的调用端口和标准化请求/响应。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol

from agent_platform.domain.identity import IdentityContext
from agent_platform.domain.model_routes import (
    ModelCallUsage,
    ModelCatalogItem,
    ModelConnection,
    ModelRoute,
)


@dataclass(frozen=True, slots=True)
class ModelRequest:
    """平台发送给模型适配器的最小请求，不含 Secret 明文。"""

    route: ModelRoute
    prompt: str
    run_id: str
    system_prompt: str | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("prompt", "run_id"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise ValueError(f"{name} must not be blank")


@dataclass(frozen=True, slots=True, repr=False)
class ModelResponse:
    """供应商响应的统一表示，原始 payload 不跨适配器边界传播。"""

    text: str
    usage: ModelCallUsage
    provider_request_id: str | None = None
    model_name: str | None = None

    def __repr__(self) -> str:
        return (
            f"ModelResponse(text={self.text!r}, usage={self.usage!r}, "
            f"provider_request_id={self.provider_request_id!r})"
        )


class SecretProvider(Protocol):
    """受控 Secret 解析端口；实现方负责权限和审计。"""

    def resolve(self, secret_ref: object) -> str:
        ...


class ModelProvider(Protocol):
    """所有模型入口适配器实现的统一调用协议。"""

    provider_name: str

    def complete(self, request: ModelRequest) -> ModelResponse:
        ...


class ModelCatalogProvider(Protocol):
    """按连接发现或验证模型目录；不执行模型推理。"""

    provider_kind: str

    def list_models(
        self, connection: ModelConnection, *, credential: str | None
    ) -> tuple[ModelCatalogItem, ...]:
        ...


class ModelAuthorizationProvider(Protocol):
    """根据当前平台身份判断该连接下的模型是否允许使用。"""

    authorization_source: str

    def authorize(
        self,
        identity: IdentityContext,
        connection: ModelConnection,
        model: ModelCatalogItem,
    ) -> bool:
        ...
