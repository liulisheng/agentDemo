"""接收方 Deployment 不可变运行配置修订。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum


class DeploymentRuntimeMode(StrEnum):
    """Deployment 配置继承全局默认或固定员工专属覆盖。"""

    INHERIT = "INHERIT"
    OVERRIDE = "OVERRIDE"


class ReasoningMode(StrEnum):
    """统一回答策略的思考模式；None 表示在作用域配置中继承。"""

    OFF = "OFF"
    AUTO = "AUTO"
    DEEP = "DEEP"


class ReasoningEffort(StrEnum):
    """统一推理强度；最终是否支持由 EngineProvider/ModelRoute 决定。"""

    AUTO = "AUTO"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class ResponseVerbosity(StrEnum):
    """最终答复的详细程度，不代表供应商内部思考内容。"""

    CONCISE = "CONCISE"
    STANDARD = "STANDARD"
    DETAILED = "DETAILED"


@dataclass(frozen=True, slots=True)
class RuntimeOptions:
    """平台统一的回答策略和运行限制；不暴露供应商私有字段。"""

    reasoning_mode: ReasoningMode | None = None
    reasoning_effort: ReasoningEffort | None = None
    reasoning_budget_tokens: int | None = None
    response_verbosity: ResponseVerbosity | None = None
    max_output_tokens: int | None = None
    max_tool_steps: int | None = None
    timeout_seconds: int | None = None

    _FIELDS = frozenset(
        {
            "reasoning_mode",
            "reasoning_effort",
            "reasoning_budget_tokens",
            "response_verbosity",
            "max_output_tokens",
            "max_tool_steps",
            "timeout_seconds",
        }
    )

    @classmethod
    def defaults(cls) -> RuntimeOptions:
        """返回不依赖供应商的安全默认值。"""

        return cls(
            reasoning_mode=ReasoningMode.AUTO,
            reasoning_effort=ReasoningEffort.AUTO,
            response_verbosity=ResponseVerbosity.STANDARD,
            max_tool_steps=20,
            timeout_seconds=300,
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, object] | None) -> RuntimeOptions:
        """解析 API/JSON 的白名单字段，并将 INHERIT 转成 None。"""

        if value is None:
            return cls()
        if not isinstance(value, Mapping):
            raise TypeError("runtime_options must be an object")
        unknown = set(value).difference(cls._FIELDS)
        if unknown:
            raise ValueError(f"unknown runtime option: {min(unknown)}")

        def enum_value(enum_type: type[StrEnum], key: str) -> StrEnum | None:
            raw = value.get(key)
            if raw is None or (isinstance(raw, str) and raw.upper() == "INHERIT"):
                return None
            if not isinstance(raw, str):
                raise TypeError(f"{key} must be a string or null")
            try:
                return enum_type(raw.upper())
            except ValueError as error:
                raise ValueError(f"invalid {key}: {raw}") from error

        def positive_int(key: str) -> int | None:
            raw = value.get(key)
            if raw is None:
                return None
            if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
                raise ValueError(f"{key} must be a positive integer")
            return raw

        budget = positive_int("reasoning_budget_tokens")
        max_output = positive_int("max_output_tokens")
        max_steps = positive_int("max_tool_steps")
        timeout = positive_int("timeout_seconds")
        if budget is not None and budget > 1_000_000:
            raise ValueError("reasoning_budget_tokens exceeds the limit")
        if max_output is not None and max_output > 1_000_000:
            raise ValueError("max_output_tokens exceeds the limit")
        if max_steps is not None and max_steps > 100:
            raise ValueError("max_tool_steps exceeds the limit")
        if timeout is not None and timeout > 3_600:
            raise ValueError("timeout_seconds exceeds the limit")
        return cls(
            reasoning_mode=enum_value(ReasoningMode, "reasoning_mode"),  # type: ignore[arg-type]
            reasoning_effort=enum_value(ReasoningEffort, "reasoning_effort"),  # type: ignore[arg-type]
            reasoning_budget_tokens=budget,
            response_verbosity=enum_value(ResponseVerbosity, "response_verbosity"),  # type: ignore[arg-type]
            max_output_tokens=max_output,
            max_tool_steps=max_steps,
            timeout_seconds=timeout,
        )

    def resolve(self, fallback: RuntimeOptions | None = None) -> RuntimeOptions:
        """按字段继承下一作用域；无 fallback 时使用平台默认。"""

        base = fallback or self.defaults()
        return RuntimeOptions(
            reasoning_mode=self.reasoning_mode or base.reasoning_mode,
            reasoning_effort=self.reasoning_effort or base.reasoning_effort,
            reasoning_budget_tokens=(
                self.reasoning_budget_tokens
                if self.reasoning_budget_tokens is not None
                else base.reasoning_budget_tokens
            ),
            response_verbosity=self.response_verbosity or base.response_verbosity,
            max_output_tokens=(
                self.max_output_tokens
                if self.max_output_tokens is not None
                else base.max_output_tokens
            ),
            max_tool_steps=self.max_tool_steps or base.max_tool_steps,
            timeout_seconds=self.timeout_seconds or base.timeout_seconds,
        )

    def to_mapping(self, *, include_none: bool = False) -> dict[str, object]:
        """输出稳定 JSON 字段；默认省略继承值。"""

        values: dict[str, object] = {
            "reasoning_mode": self.reasoning_mode.value if self.reasoning_mode else None,
            "reasoning_effort": self.reasoning_effort.value if self.reasoning_effort else None,
            "reasoning_budget_tokens": self.reasoning_budget_tokens,
            "response_verbosity": self.response_verbosity.value if self.response_verbosity else None,
            "max_output_tokens": self.max_output_tokens,
            "max_tool_steps": self.max_tool_steps,
            "timeout_seconds": self.timeout_seconds,
        }
        return values if include_none else {key: value for key, value in values.items() if value is not None}


@dataclass(frozen=True, slots=True)
class DeploymentRuntimeConfig:
    """固定某个 Deployment 的运行模式、资源引用和不可变修订号。"""

    platform_id: str
    tenant_id: str
    deployment_id: str
    config_version: int
    config_mode: DeploymentRuntimeMode
    execution_profile_id: str | None = None
    execution_profile_version: int | None = None
    model_route_id: str | None = None
    model_route_snapshot: Mapping[str, object] | None = None
    workspace_profile_id: str | None = None
    workspace_profile_version: int | None = None
    sandbox_profile_id: str | None = None
    sandbox_profile_version: int | None = None
    approval_policy: Mapping[str, object] = field(default_factory=dict)
    runtime_options: Mapping[str, object] = field(default_factory=dict)
    created_by: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "config_mode", DeploymentRuntimeMode(self.config_mode))
        for name in ("platform_id", "tenant_id", "deployment_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must not be blank")
        for name in (
            "execution_profile_id",
            "model_route_id",
            "workspace_profile_id",
            "sandbox_profile_id",
            "created_by",
        ):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{name} must not be blank when provided")
        if self.config_version < 1:
            raise ValueError("config_version must be positive")
        self._validate_pair("execution_profile_id", "execution_profile_version")
        self._validate_pair("workspace_profile_id", "workspace_profile_version")
        self._validate_pair("sandbox_profile_id", "sandbox_profile_version")
        if self.config_mode is DeploymentRuntimeMode.INHERIT and (
            self.execution_profile_id is not None or self.model_route_id is not None
        ):
            raise ValueError("INHERIT configuration must not include engine or ModelRoute overrides")
        if self.config_mode is DeploymentRuntimeMode.OVERRIDE and (
            self.execution_profile_id is None and self.model_route_id is None
        ):
            raise ValueError("OVERRIDE configuration requires an engine or ModelRoute override")
        if not isinstance(self.approval_policy, Mapping):
            raise TypeError("approval_policy must be a mapping")
        if not isinstance(self.runtime_options, Mapping):
            raise TypeError("runtime_options must be a mapping")
        RuntimeOptions.from_mapping(self.runtime_options)
        if self.model_route_snapshot is not None and not isinstance(
            self.model_route_snapshot, Mapping
        ):
            raise TypeError("model_route_snapshot must be a mapping")
        object.__setattr__(self, "approval_policy", dict(self.approval_policy))
        object.__setattr__(self, "runtime_options", dict(self.runtime_options))
        if self.model_route_snapshot is not None:
            object.__setattr__(self, "model_route_snapshot", dict(self.model_route_snapshot))

    def _validate_pair(self, reference_field: str, version_field: str) -> None:
        reference = getattr(self, reference_field)
        version = getattr(self, version_field)
        if (reference is None) != (version is None):
            raise ValueError(f"{reference_field} and {version_field} must be set together")
        if version is not None and version < 1:
            raise ValueError(f"{version_field} must be positive")
