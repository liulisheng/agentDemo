"""接收方 Deployment 不可变运行配置修订。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum


class DeploymentRuntimeMode(StrEnum):
    """Deployment 配置继承全局默认或固定员工专属覆盖。"""

    INHERIT = "INHERIT"
    OVERRIDE = "OVERRIDE"


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
