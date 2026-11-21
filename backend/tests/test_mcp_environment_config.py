from __future__ import annotations

from pathlib import Path

import pytest

from agent_platform.adapters.mcp.environment import (
    EnvironmentMcpSecretResolver,
    EnvironmentMcpWorkingDirectoryResolver,
)


def test_environment_mcp_resolvers_require_explicit_aliases(monkeypatch, tmp_path: Path) -> None:
    mcp_root = tmp_path / "mcp-root"
    mcp_root.mkdir()
    monkeypatch.setenv("DOLPHIN_TOKEN", "short-lived-token")
    monkeypatch.setenv("DOLPHIN_MCP_CWD", str(mcp_root))
    secrets = EnvironmentMcpSecretResolver()
    directories = EnvironmentMcpWorkingDirectoryResolver()

    assert secrets.resolve("env://tenant-1/DOLPHIN_TOKEN", tenant_id="tenant-1") == "short-lived-token"
    assert directories.resolve(
        "env-path://tenant-1/DOLPHIN_MCP_CWD", tenant_id="tenant-1"
    ) == mcp_root.resolve()
    with pytest.raises(ValueError, match="tenant scope"):
        secrets.resolve("env://tenant-2/DOLPHIN_TOKEN", tenant_id="tenant-1")
    with pytest.raises(ValueError, match="tenant scope"):
        secrets.resolve("DOLPHIN_TOKEN", tenant_id="tenant-1")
    with pytest.raises(ValueError, match="environment alias"):
        secrets.resolve("env://tenant-1/bad-name", tenant_id="tenant-1")
    with pytest.raises(ValueError, match="tenant scope"):
        directories.resolve(str(mcp_root), tenant_id="tenant-1")
    with pytest.raises(ValueError, match="environment alias"):
        directories.resolve("env-path://tenant-1/bad-name", tenant_id="tenant-1")
