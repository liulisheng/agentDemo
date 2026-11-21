"""Worker 进程内按租户环境变量解析模型凭据引用。"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping

from agent_platform.domain.model_routes import SecretRef
from agent_platform.ports.model_provider import SecretProvider


class ModelSecretError(RuntimeError):
    """模型凭据解析错误；消息只含稳定类别，不包含 SecretRef 或密钥。"""

    def __init__(self, category: str) -> None:
        self.category = category
        super().__init__(category)


class EnvironmentModelSecretProvider(SecretProvider):
    """将租户 SecretRef 映射到 Worker 专属环境变量。"""

    _prefix = "AGENT_PLATFORM_MODEL_SECRET_"

    def __init__(self, environment: Mapping[str, str] | None = None) -> None:
        self._environment = environment

    @staticmethod
    def environment_name(secret_ref: SecretRef) -> str:
        """生成包含租户作用域的变量名，避免不同租户同名凭据互相覆盖。"""

        parts = (secret_ref.scope, secret_ref.secret_id)
        normalized = [re.sub(r"[^A-Za-z0-9]+", "_", part).strip("_").upper() for part in parts]
        if any(not part for part in normalized):
            raise ModelSecretError("MODEL_SECRET_REFERENCE_INVALID")
        return EnvironmentModelSecretProvider._prefix + "_".join(normalized)

    def resolve(self, secret_ref: object) -> str:
        if not isinstance(secret_ref, SecretRef):
            raise ModelSecretError("MODEL_SECRET_REFERENCE_INVALID")
        if secret_ref.provider != "environment":
            raise ModelSecretError("MODEL_SECRET_PROVIDER_UNSUPPORTED")
        variable = self.environment_name(secret_ref)
        environment = self._environment if self._environment is not None else os.environ
        value = environment.get(variable)
        if not isinstance(value, str) or not value:
            raise ModelSecretError("MODEL_SECRET_UNAVAILABLE")
        return value

    def __repr__(self) -> str:
        return "EnvironmentModelSecretProvider(environment=<redacted>)"
