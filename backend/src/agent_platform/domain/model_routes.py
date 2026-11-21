"""模型路由、认证引用和标准化模型用量领域对象。

模型配置只保存 SecretRef，不保存密钥明文；Worker 在调用适配器时才通过
受控 SecretProvider 取得短生命周期凭据。这样路由快照可以安全地持久化和审计。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from types import MappingProxyType


class UsageStatus(StrEnum):
    """上游是否提供可核对的精确 Token 用量。"""

    EXACT = "EXACT"
    ESTIMATED = "ESTIMATED"


class ModelConnectionStatus(StrEnum):
    """租户模型连接的管理状态。"""

    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class ModelCatalogMode(StrEnum):
    """模型连接读取目录或手工维护目录的方式。"""

    REMOTE = "REMOTE"
    MANUAL = "MANUAL"


class ModelCatalogSource(StrEnum):
    """模型目录项的创建来源。"""

    REMOTE = "REMOTE"
    MANUAL = "MANUAL"
    BACKFILL = "BACKFILL"


class ModelCatalogStatus(StrEnum):
    """租户模型目录项是否可供员工路由选择。"""

    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class ModelTestKind(StrEnum):
    """模型连接目录测试或真实模型推理测试。"""

    CONNECTION = "CONNECTION"
    MODEL = "MODEL"


class ModelTestState(StrEnum):
    """由 Worker 管理的异步模型测试状态。"""

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


def _text(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    return value.strip()


@dataclass(frozen=True, slots=True, repr=False)
class ModelConnection:
    """租户范围的模型服务连接；共享 endpoint 和 SecretRef，不绑定某个模型 ID。"""

    connection_id: str
    tenant_id: str
    name: str
    provider_kind: str
    protocol: str
    endpoint: str
    secret_ref: SecretRef | None = None
    catalog_mode: ModelCatalogMode = ModelCatalogMode.REMOTE
    status: ModelConnectionStatus = ModelConnectionStatus.ACTIVE
    version: int = 1

    def __post_init__(self) -> None:
        for name in (
            "connection_id",
            "tenant_id",
            "name",
            "provider_kind",
            "protocol",
            "endpoint",
        ):
            _text(getattr(self, name), name)
        if self.secret_ref is not None and not isinstance(self.secret_ref, SecretRef):
            raise TypeError("secret_ref must be a SecretRef value")
        if self.version < 1:
            raise ValueError("connection version must be positive")
        object.__setattr__(self, "catalog_mode", ModelCatalogMode(self.catalog_mode))
        object.__setattr__(self, "status", ModelConnectionStatus(self.status))

    def __repr__(self) -> str:
        return (
            f"ModelConnection(connection_id={self.connection_id!r}, tenant_id={self.tenant_id!r}, "
            f"name={self.name!r}, provider_kind={self.provider_kind!r}, secret_ref=<redacted>)"
        )

    def to_public_dict(self) -> dict[str, object]:
        """返回连接元数据；认证值只通过 SecretRef 暴露。"""

        return {
            "connection_id": self.connection_id,
            "tenant_id": self.tenant_id,
            "name": self.name,
            "provider_kind": self.provider_kind,
            "protocol": self.protocol,
            "endpoint": self.endpoint,
            "secret_ref": self.secret_ref.to_public_dict() if self.secret_ref else None,
            "catalog_mode": self.catalog_mode.value,
            "status": self.status.value,
            "version": self.version,
        }


@dataclass(frozen=True, slots=True)
class ModelCatalogItem:
    """一个连接下可独立选择的模型 ID 与非敏感能力目录。"""

    connection_id: str
    tenant_id: str
    model_id: str
    display_name: str
    capabilities: Mapping[str, object] = field(default_factory=dict)
    context_window: int | None = None
    catalog_source: ModelCatalogSource = ModelCatalogSource.MANUAL
    status: ModelCatalogStatus = ModelCatalogStatus.ACTIVE
    last_seen_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in ("connection_id", "tenant_id", "model_id", "display_name"):
            _text(getattr(self, name), name)
        if not isinstance(self.capabilities, Mapping):
            raise TypeError("capabilities must be a mapping")
        if self.context_window is not None and self.context_window < 1:
            raise ValueError("context_window must be positive when provided")
        object.__setattr__(self, "capabilities", MappingProxyType(dict(self.capabilities)))
        object.__setattr__(self, "catalog_source", ModelCatalogSource(self.catalog_source))
        object.__setattr__(self, "status", ModelCatalogStatus(self.status))

    def to_public_dict(self) -> dict[str, object]:
        return {
            "connection_id": self.connection_id,
            "tenant_id": self.tenant_id,
            "model_id": self.model_id,
            "display_name": self.display_name,
            "capabilities": dict(self.capabilities),
            "context_window": self.context_window,
            "catalog_source": self.catalog_source.value,
            "status": self.status.value,
            "last_seen_at": self.last_seen_at,
        }


@dataclass(frozen=True, slots=True, repr=False)
class ModelTestRecord:
    """一次模型可用性检查的异步记录；快照只含凭据引用，不含请求正文或密钥。"""

    test_id: str
    tenant_id: str
    connection_id: str
    test_kind: ModelTestKind
    state: ModelTestState
    snapshot: Mapping[str, object] = field(repr=False)
    created_by_user_id: str
    route_id: str | None = None
    model_id: str | None = None
    worker_id: str | None = None
    fencing_token: int = 0
    lease_expires_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    latency_ms: int | None = None
    provider_request_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    usage_status: UsageStatus | None = None
    error_category: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        test_kind = ModelTestKind(self.test_kind)
        state = ModelTestState(self.state)
        for name in ("test_id", "tenant_id", "connection_id", "created_by_user_id"):
            _text(getattr(self, name), name)
        if self.route_id is not None:
            _text(self.route_id, "route_id")
        if self.model_id is not None:
            _text(self.model_id, "model_id")
        if test_kind is ModelTestKind.MODEL and self.model_id is None:
            raise ValueError("model test requires a model_id")
        if not isinstance(self.snapshot, Mapping):
            raise TypeError("snapshot must be a mapping")
        if self.fencing_token < 0:
            raise ValueError("fencing_token must be non-negative")
        if self.latency_ms is not None and self.latency_ms < 0:
            raise ValueError("latency_ms must be non-negative")
        if state is ModelTestState.RUNNING and (
            self.worker_id is None or self.fencing_token < 1 or self.lease_expires_at is None
        ):
            raise ValueError("running model test requires a worker lease and fencing token")
        for name in ("input_tokens", "output_tokens", "total_tokens"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, int) or value < 0):
                raise ValueError(f"{name} must be a non-negative integer")
        if self.usage_status is not None:
            object.__setattr__(self, "usage_status", UsageStatus(self.usage_status))
        if self.provider_request_id is not None:
            value = _text(self.provider_request_id, "provider_request_id")
            if len(value) > 255:
                raise ValueError("provider_request_id exceeds 255 characters")
            object.__setattr__(self, "provider_request_id", value)
        if self.error_category is not None:
            value = _text(self.error_category, "error_category")
            if len(value) > 64:
                raise ValueError("error_category exceeds 64 characters")
            object.__setattr__(self, "error_category", value)
        object.__setattr__(self, "test_kind", test_kind)
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "snapshot", MappingProxyType(dict(self.snapshot)))

    def claim(self, worker_id: str, *, now: datetime, lease_seconds: int) -> ModelTestRecord:
        """仅 QUEUED 任务可被租约领取；每次领取都提高 fencing token。"""

        if self.state is not ModelTestState.QUEUED:
            raise ValueError("only queued model tests can be claimed")
        worker = _text(worker_id, "worker_id")
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        timestamp = now.astimezone(UTC) if now.tzinfo is not None else now.replace(tzinfo=UTC)
        return replace(
            self,
            state=ModelTestState.RUNNING,
            worker_id=worker,
            fencing_token=self.fencing_token + 1,
            lease_expires_at=timestamp + timedelta(seconds=lease_seconds),
            started_at=timestamp,
            finished_at=None,
            error_category=None,
            updated_at=timestamp,
        )

    def finish(self, result: ModelTestResult, *, finished_at: datetime) -> ModelTestRecord:
        """只允许持有 RUNNING 租约的工作通过终态结果关闭任务。"""

        if self.state is not ModelTestState.RUNNING or self.worker_id is None:
            raise ValueError("only a claimed model test can be finished")
        if not isinstance(result, ModelTestResult):
            raise TypeError("result must be a ModelTestResult")
        timestamp = (
            finished_at.astimezone(UTC)
            if finished_at.tzinfo is not None
            else finished_at.replace(tzinfo=UTC)
        )
        return replace(
            self,
            state=result.state,
            lease_expires_at=None,
            finished_at=timestamp,
            latency_ms=result.latency_ms,
            provider_request_id=result.provider_request_id,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            total_tokens=result.total_tokens,
            usage_status=result.usage_status,
            error_category=result.error_category,
            updated_at=timestamp,
        )

    def __repr__(self) -> str:
        return (
            f"ModelTestRecord(test_id={self.test_id!r}, tenant_id={self.tenant_id!r}, "
            f"connection_id={self.connection_id!r}, test_kind={self.test_kind.value!r}, "
            f"state={self.state.value!r}, snapshot=<redacted>)"
        )

    def to_public_dict(self) -> dict[str, object]:
        """返回轮询所需的测试状态，不包含内部快照或凭据引用。"""

        return {
            "test_id": self.test_id,
            "tenant_id": self.tenant_id,
            "connection_id": self.connection_id,
            "route_id": self.route_id,
            "model_id": self.model_id,
            "test_kind": self.test_kind.value,
            "state": self.state.value,
            "created_by_user_id": self.created_by_user_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "latency_ms": self.latency_ms,
            "provider_request_id": self.provider_request_id,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "usage_status": self.usage_status.value if self.usage_status else None,
            "error_category": self.error_category,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True, slots=True)
class ModelTestResult:
    """Worker 可持久化的有界测试结果，不承载模型响应正文或供应商原始对象。"""

    state: ModelTestState
    latency_ms: int | None = None
    provider_request_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    usage_status: UsageStatus | None = None
    error_category: str | None = None

    def __post_init__(self) -> None:
        state = ModelTestState(self.state)
        if state not in {ModelTestState.SUCCEEDED, ModelTestState.FAILED, ModelTestState.UNKNOWN}:
            raise ValueError("model test result must be terminal")
        if self.latency_ms is not None and self.latency_ms < 0:
            raise ValueError("latency_ms must be non-negative")
        for name in ("input_tokens", "output_tokens", "total_tokens"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, int) or value < 0):
                raise ValueError(f"{name} must be a non-negative integer")
        if (
            all(
                value is not None
                for value in (self.input_tokens, self.output_tokens, self.total_tokens)
            )
            and self.total_tokens != self.input_tokens + self.output_tokens
        ):
            raise ValueError("total_tokens must equal input plus output tokens")
        if self.usage_status is not None:
            object.__setattr__(self, "usage_status", UsageStatus(self.usage_status))
        if self.provider_request_id is not None:
            value = _text(self.provider_request_id, "provider_request_id")
            if len(value) > 255:
                raise ValueError("provider_request_id exceeds 255 characters")
            object.__setattr__(self, "provider_request_id", value)
        if self.error_category is not None:
            value = _text(self.error_category, "error_category")
            if len(value) > 64:
                raise ValueError("error_category exceeds 64 characters")
            object.__setattr__(self, "error_category", value)
        object.__setattr__(self, "state", state)


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
    connection_id: str | None = None
    catalog_model_id: str | None = None

    def __post_init__(self) -> None:
        for name in ("profile_id", "tenant_id", "model_name", "provider", "endpoint", "protocol"):
            _text(getattr(self, name), name)
        if self.pricing_snapshot_ref is not None:
            _text(self.pricing_snapshot_ref, "pricing_snapshot_ref")
        if not isinstance(self.parameters, Mapping):
            raise TypeError("parameters must be a mapping")
        if (self.connection_id is None) != (self.catalog_model_id is None):
            raise ValueError("connection_id and catalog_model_id must be provided together")
        if self.connection_id is not None:
            _text(self.connection_id, "connection_id")
            _text(self.catalog_model_id or "", "catalog_model_id")
            if self.catalog_model_id != self.model_name:
                raise ValueError("catalog_model_id must match model_name")

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
            "connection_id": self.connection_id,
            "catalog_model_id": self.catalog_model_id,
        }

    @property
    def model_alias(self) -> str:
        """兼容调用方把模型名称称为 alias 的只读视图。"""

        return self.model_name


def legacy_model_connection_id(platform_id: str, tenant_id: str, profile: ModelProfile) -> str:
    """为旧版直连 ModelRoute 生成稳定连接 ID，避免重复建连接。"""

    secret = profile.secret_ref
    material = "\x1f".join(
        (
            platform_id,
            tenant_id,
            profile.provider,
            profile.protocol,
            profile.endpoint,
            secret.secret_id if secret else "",
            secret.scope if secret else "",
            secret.provider if secret else "",
        )
    )
    return "conn-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True, slots=True, repr=False)
class ModelRoute:
    """可绑定到 ExecutionProfile 的模型路由；路由快照用于固定已开始 Run。"""

    route_id: str
    tenant_id: str
    alias: str
    profile: ModelProfile
    status: str = "ACTIVE"
    route_version: int = 1

    def __post_init__(self) -> None:
        for name in ("route_id", "tenant_id", "alias", "status"):
            _text(getattr(self, name), name)
        if self.profile.tenant_id != self.tenant_id:
            raise ValueError("model profile and route must share tenant scope")
        if self.route_version < 1:
            raise ValueError("route_version must be positive")

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
            "route_version": self.route_version,
            "profile": self.profile.to_public_dict(),
        }

    def to_snapshot(self) -> dict[str, object]:
        """构造可持久化 Run 快照，绝不包含认证值。"""

        return self.to_public_dict()

    def to_snapshot_json(self) -> str:
        return json.dumps(self.to_snapshot(), ensure_ascii=False, sort_keys=True)


@dataclass(frozen=True, slots=True, repr=False)
class ModelRouteSnapshot:
    """Run 使用的完整模型连接/路由版本，不包含凭据明文。"""

    tenant_id: str
    route_id: str
    route_version: int
    route_alias: str
    connection_id: str
    connection_version: int
    provider_kind: str
    model_id: str
    protocol: str
    endpoint: str
    secret_ref: SecretRef | None = None
    parameters: Mapping[str, object] = field(default_factory=dict, repr=False)
    pricing_snapshot_ref: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "tenant_id",
            "route_id",
            "route_alias",
            "connection_id",
            "provider_kind",
            "model_id",
            "protocol",
            "endpoint",
        ):
            _text(getattr(self, name), name)
        if self.route_version < 1 or self.connection_version < 1:
            raise ValueError("model route and connection versions must be positive")
        if self.secret_ref is not None and not isinstance(self.secret_ref, SecretRef):
            raise TypeError("secret_ref must be a SecretRef value")
        if not isinstance(self.parameters, Mapping):
            raise TypeError("parameters must be a mapping")
        object.__setattr__(self, "parameters", dict(self.parameters))
        if self.pricing_snapshot_ref is not None:
            _text(self.pricing_snapshot_ref, "pricing_snapshot_ref")

    def __repr__(self) -> str:
        return (
            f"ModelRouteSnapshot(tenant_id={self.tenant_id!r}, route_id={self.route_id!r}, "
            f"route_version={self.route_version}, connection_id={self.connection_id!r}, "
            f"connection_version={self.connection_version}, provider_kind={self.provider_kind!r}, "
            f"model_id={self.model_id!r}, secret_ref=<redacted>)"
        )

    def to_snapshot_dict(self) -> dict[str, object]:
        """构造可持久化执行快照，认证信息只包含 SecretRef 元数据。"""

        return {
            "tenant_id": self.tenant_id,
            "route_id": self.route_id,
            "route_version": self.route_version,
            "route_alias": self.route_alias,
            "connection_id": self.connection_id,
            "connection_version": self.connection_version,
            "provider_kind": self.provider_kind,
            "model_id": self.model_id,
            "protocol": self.protocol,
            "endpoint": self.endpoint,
            "secret_ref": self.secret_ref.to_public_dict() if self.secret_ref else None,
            "parameters": dict(self.parameters),
            "pricing_snapshot_ref": self.pricing_snapshot_ref,
        }

    @classmethod
    def from_snapshot_dict(cls, value: Mapping[str, object]) -> ModelRouteSnapshot:
        """从持久化快照恢复模型配置；旧版 Run 没有该对象时由调用方跳过。"""

        raw_secret = value.get("secret_ref")
        secret_ref = None
        if isinstance(raw_secret, Mapping):
            secret_ref = SecretRef(
                str(raw_secret["secret_id"]),
                str(raw_secret.get("scope", "tenant")),
                str(raw_secret.get("provider", "configured")),
            )
        raw_parameters = value.get("parameters", {})
        parameters = raw_parameters if isinstance(raw_parameters, Mapping) else {}
        return cls(
            tenant_id=str(value["tenant_id"]),
            route_id=str(value["route_id"]),
            route_version=int(value["route_version"]),
            route_alias=str(value["route_alias"]),
            connection_id=str(value["connection_id"]),
            connection_version=int(value["connection_version"]),
            provider_kind=str(value["provider_kind"]),
            model_id=str(value["model_id"]),
            protocol=str(value["protocol"]),
            endpoint=str(value["endpoint"]),
            secret_ref=secret_ref,
            parameters=parameters,
            pricing_snapshot_ref=(
                str(value["pricing_snapshot_ref"])
                if value.get("pricing_snapshot_ref") is not None
                else None
            ),
        )


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
