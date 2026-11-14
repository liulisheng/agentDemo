"""模型路由、认证引用和标准化模型用量领域对象。

模型配置只保存 SecretRef，不保存密钥明文；Worker 在调用适配器时才通过
受控 SecretProvider 取得短生命周期凭据。这样路由快照可以安全地持久化和审计。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum


class UsageStatus(StrEnum):
    """上游是否提供可核对的精确 Token 用量。"""

    EXACT = "EXACT"
    ESTIMATED = "ESTIMATED"


def _text(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    return value.strip()


@dataclass(frozen=True, slots=True, repr=False)
class SecretRef:
    """受控凭据引用；对象本身永远不携带 API Key 等 Secret 明文。"""

    secret_id: str
    scope: str = "tenant"
    provider: str = "configured"

    def __post_init__(self) -> None:
        _text(self.secret_id, "secret_id")
        _text(self.scope, "scope")
        _text(self.provider, "provider")

    def __repr__(self) -> str:
        return "SecretRef(<redacted>)"

    def to_public_dict(self) -> dict[str, str]:
        return {"secret_id": self.secret_id, "scope": self.scope, "provider": self.provider}


@dataclass(frozen=True, slots=True, repr=False)
class ModelProfile:
    """描述供应商中立的模型入口、协议和认证引用。"""

    profile_id: str
    tenant_id: str
    model_name: str
    provider: str
    endpoint: str
    protocol: str = "openai-compatible"
    secret_ref: SecretRef | None = None
    pricing_snapshot_ref: str | None = None
    parameters: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("profile_id", "tenant_id", "model_name", "provider", "endpoint", "protocol"):
            _text(getattr(self, name), name)
        if self.pricing_snapshot_ref is not None:
            _text(self.pricing_snapshot_ref, "pricing_snapshot_ref")
        if not isinstance(self.parameters, Mapping):
            raise TypeError("parameters must be a mapping")

    def __repr__(self) -> str:
        return (
            f"ModelProfile(profile_id={self.profile_id!r}, tenant_id={self.tenant_id!r}, "
            f"model_name={self.model_name!r}, provider={self.provider!r}, secret_ref=<redacted>)"
        )

    def to_public_dict(self) -> dict[str, object]:
        return {
            "profile_id": self.profile_id,
            "tenant_id": self.tenant_id,
            "model_name": self.model_name,
            "provider": self.provider,
            "endpoint": self.endpoint,
            "protocol": self.protocol,
            "secret_ref": self.secret_ref.to_public_dict() if self.secret_ref else None,
            "pricing_snapshot_ref": self.pricing_snapshot_ref,
            "parameters": dict(self.parameters),
        }

    @property
    def model_alias(self) -> str:
        """兼容调用方把模型名称称为 alias 的只读视图。"""

        return self.model_name


@dataclass(frozen=True, slots=True, repr=False)
class ModelRoute:
    """可绑定到 ExecutionProfile 的模型路由；路由快照用于固定已开始 Run。"""

    route_id: str
    tenant_id: str
    alias: str
    profile: ModelProfile
    status: str = "ACTIVE"

    def __post_init__(self) -> None:
        for name in ("route_id", "tenant_id", "alias", "status"):
            _text(getattr(self, name), name)
        if self.profile.tenant_id != self.tenant_id:
            raise ValueError("model profile and route must share tenant scope")

    @property
    def provider(self) -> str:
        return self.profile.provider

    @property
    def model_name(self) -> str:
        return self.profile.model_name

    @property
    def model_profile(self) -> ModelProfile:
        """显式命名的 Profile 只读别名，便于 API 层表达绑定关系。"""

        return self.profile

    def __repr__(self) -> str:
        return (
            f"ModelRoute(route_id={self.route_id!r}, tenant_id={self.tenant_id!r}, "
            f"alias={self.alias!r}, provider={self.provider!r}, profile=<redacted>)"
        )

    def to_public_dict(self) -> dict[str, object]:
        """构造用户可见响应，显式只暴露 SecretRef 元数据。"""

        return {
            "route_id": self.route_id,
            "tenant_id": self.tenant_id,
            "alias": self.alias,
            "status": self.status,
            "profile": self.profile.to_public_dict(),
        }

    def to_snapshot(self) -> dict[str, object]:
        """构造可持久化 Run 快照，绝不包含认证值。"""

        return self.to_public_dict()

    def to_snapshot_json(self) -> str:
        return json.dumps(self.to_snapshot(), ensure_ascii=False, sort_keys=True)


@dataclass(frozen=True, slots=True, repr=False)
class ModelCallUsage:
    """一次模型调用的标准化 Token、费用和估算状态。"""

    input_tokens: int
    output_tokens: int
    total_tokens: int
    usage_status: UsageStatus = UsageStatus.EXACT
    estimation_method: str | None = None
    provider_request_id: str | None = None
    cost: float | None = None
    currency: str | None = None
    pricing_snapshot_ref: str | None = None

    def __post_init__(self) -> None:
        for name in ("input_tokens", "output_tokens", "total_tokens"):
            value = getattr(self, name)
            if not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.total_tokens != self.input_tokens + self.output_tokens:
            raise ValueError("total_tokens must equal input plus output tokens")
        status = UsageStatus(self.usage_status)
        object.__setattr__(self, "usage_status", status)
        if status is UsageStatus.ESTIMATED:
            if self.estimation_method is None or not self.estimation_method.strip():
                raise ValueError("estimated usage requires estimation_method")
        elif self.estimation_method is not None and not self.estimation_method.strip():
            raise ValueError("estimation_method must not be blank")
        if self.provider_request_id is not None:
            _text(self.provider_request_id, "provider_request_id")
        if self.currency is not None:
            _text(self.currency, "currency")
        if self.pricing_snapshot_ref is not None:
            _text(self.pricing_snapshot_ref, "pricing_snapshot_ref")
        if self.cost is not None and self.cost < 0:
            raise ValueError("cost must not be negative")

    def __repr__(self) -> str:
        return (
            f"ModelCallUsage(input_tokens={self.input_tokens}, output_tokens={self.output_tokens}, "
            f"total_tokens={self.total_tokens}, usage_status={self.usage_status!r})"
        )

    @property
    def estimated(self) -> bool:
        return self.usage_status is UsageStatus.ESTIMATED

    def to_dict(self) -> dict[str, object]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "usage_status": self.usage_status.value,
            "estimation_method": self.estimation_method,
            "provider_request_id": self.provider_request_id,
            "cost": self.cost,
            "currency": self.currency,
            "pricing_snapshot_ref": self.pricing_snapshot_ref,
        }


# Usage 是历史领域命名的兼容别名，统一使用 ModelCallUsage 作为新接口类型。
Usage = ModelCallUsage


def normalize_usage(
    upstream: Mapping[str, object] | None,
    *,
    estimation_method: str | None = None,
    estimated_input_tokens: int = 0,
    estimated_output_tokens: int = 0,
    provider_request_id: str | None = None,
    pricing_snapshot_ref: str | None = None,
) -> ModelCallUsage:
    """把不同供应商的 usage 字段归一化，并明确标记精确或估算。"""

    if upstream is None:
        method = estimation_method or "unavailable"
        return ModelCallUsage(
            input_tokens=estimated_input_tokens,
            output_tokens=estimated_output_tokens,
            total_tokens=estimated_input_tokens + estimated_output_tokens,
            usage_status=UsageStatus.ESTIMATED,
            estimation_method=method,
            provider_request_id=provider_request_id,
            pricing_snapshot_ref=pricing_snapshot_ref,
        )

    input_tokens = int(upstream.get("input_tokens", upstream.get("prompt_tokens", 0)))
    output_tokens = int(upstream.get("output_tokens", upstream.get("completion_tokens", 0)))
    total_tokens = int(upstream.get("total_tokens", input_tokens + output_tokens))
    if total_tokens != input_tokens + output_tokens:
        total_tokens = input_tokens + output_tokens
    return ModelCallUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        usage_status=UsageStatus.EXACT,
        provider_request_id=provider_request_id,
        cost=float(upstream["cost"]) if upstream.get("cost") is not None else None,
        currency=str(upstream["currency"]) if upstream.get("currency") is not None else None,
        pricing_snapshot_ref=pricing_snapshot_ref,
    )
