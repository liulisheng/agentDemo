"""从进程环境短时解析 SecretRef；不写日志、不返回 Secret 引用以外的配置。"""

from __future__ import annotations

import os

from agent_platform.ports.workspaces import SecretProvider


class EnvironmentSecretProvider(SecretProvider):
    """按版本 SecretRef 从环境变量解析密钥，支持同时保留旧版本用于读回。"""

    def __init__(
        self,
        secret_ref: str,
        *,
        current_key_version: str,
        environment_variable: str,
    ) -> None:
        self._secret_ref = secret_ref
        self._current_key_version = current_key_version
        self._environment_variable = environment_variable

    def resolve(self, secret_ref: str, *, ttl_seconds: int = 300) -> dict[str, str]:
        if ttl_seconds <= 0 or ttl_seconds > 3600:
            raise ValueError("secret TTL must be between 1 and 3600 seconds")
        prefix = f"{self._secret_ref}@"
        if not secret_ref.startswith(prefix):
            raise KeyError("secret reference not found")
        key_version = secret_ref.removeprefix(prefix)
        version_variable = f"{self._environment_variable}_{key_version.upper().replace('-', '_')}"
        value = os.environ.get(version_variable)
        if value is None and key_version == self._current_key_version:
            value = os.environ.get(self._environment_variable)
        if not value:
            raise KeyError("secret value is unavailable")
        return {"key_base64": value}
