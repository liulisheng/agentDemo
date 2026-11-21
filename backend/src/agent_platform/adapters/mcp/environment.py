"""从显式租户范围环境别名解析 MCP Stdio 的 SecretRef 和 cwd 引用。"""

from __future__ import annotations

import os
import re
from pathlib import Path

_ENVIRONMENT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class EnvironmentMcpSecretResolver:
    """只接受 `env://<tenant>/<NAME>` 的本地 MCP SecretRef。"""

    def resolve(self, secret_ref: str, *, tenant_id: str) -> str:
        prefix = f"env://{tenant_id}/"
        if not secret_ref.startswith(prefix):
            raise ValueError("MCP SecretRef tenant scope does not match")
        name = secret_ref.removeprefix(prefix)
        if _ENVIRONMENT_NAME.fullmatch(name) is None:
            raise ValueError("MCP SecretRef environment alias is invalid")
        value = os.environ.get(name)
        if value is None or not value or "\x00" in value:
            raise ValueError("MCP SecretRef environment alias is unavailable")
        return value


class EnvironmentMcpWorkingDirectoryResolver:
    """只接受 `env-path://<tenant>/<NAME>` 的 Worker cwd 引用。"""

    def resolve(self, working_directory_ref: str, *, tenant_id: str) -> Path:
        prefix = f"env-path://{tenant_id}/"
        if not working_directory_ref.startswith(prefix):
            raise ValueError("MCP working-directory tenant scope does not match")
        name = working_directory_ref.removeprefix(prefix)
        if _ENVIRONMENT_NAME.fullmatch(name) is None:
            raise ValueError("MCP working-directory environment alias is invalid")
        value = os.environ.get(name)
        if value is None or "\x00" in value:
            raise ValueError("MCP working-directory environment alias is unavailable")
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise ValueError("MCP working-directory path must be absolute")
        resolved = path.resolve(strict=True)
        if not resolved.is_dir():
            raise ValueError("MCP working-directory path must be a directory")
        return resolved


__all__ = ["EnvironmentMcpSecretResolver", "EnvironmentMcpWorkingDirectoryResolver"]
