import json
from typing import Any

import pytest

from agent_platform.domain.common import DomainError
from agent_platform.domain.identity import IdentityContext
from agent_platform.domain.mcp import McpServerProfile
from agent_platform.infrastructure.mysql_mcp import MySqlMcpRepository


class FakeCursor:
    def __init__(self) -> None:
        self.statements: list[tuple[str, tuple[Any, ...] | None]] = []
        self.rowcount = 0
        self.closed = False

    def execute(self, statement: str, parameters: tuple[Any, ...] | None = None) -> None:
        self.statements.append((statement, parameters))
        self.rowcount = 1 if statement.lstrip().startswith("INSERT") else 0

    def fetchone(self):
        return None

    def close(self) -> None:
        self.closed = True


class FakeConnection:
    def __init__(self) -> None:
        self.query = FakeCursor()
        self.started = False
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def cursor(self) -> FakeCursor:
        return self.query

    def start_transaction(self) -> None:
        self.started = True

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        self.closed = True


def test_profile_repository_saves_only_secret_references_in_a_tenant_transaction() -> None:
    connection = FakeConnection()
    repository = MySqlMcpRepository(lambda: connection)
    identity = IdentityContext(
        "platform-1", "tenant-1", "admin-1", frozenset({"admin"})
    )
    profile = McpServerProfile(
        platform_id="platform-1",
        tenant_id="tenant-1",
        profile_id="profile-1",
        version=1,
        name="DolphinScheduler",
        transport="stdio",
        command="/usr/local/bin/python3",
        args=("-m", "dolphin_mcp"),
        working_directory_ref="workspace://dolphin",
        environment_secret_refs={"DOLPHIN_TOKEN": "secret://tenant-a/dolphin"},
        enabled_tool_names=("projects_list", "workflows_list"),
        tool_catalog_sha256="a" * 64,
    )

    repository.save_profile(identity, profile)

    assert connection.started is True
    assert connection.committed is True
    assert connection.rolled_back is False
    assert connection.closed is True
    insert_sql, values = connection.query.statements[-1]
    assert "INSERT INTO mcp_server_profiles" in insert_sql
    assert "WHERE platform_id" in connection.query.statements[0][0]
    assert values is not None
    assert json.loads(values[9]) == {"DOLPHIN_TOKEN": "secret://tenant-a/dolphin"}
    assert "actual-token-value" not in repr(values)


def test_profile_repository_rejects_cross_tenant_configuration_before_sql() -> None:
    connection = FakeConnection()
    repository = MySqlMcpRepository(lambda: connection)
    identity = IdentityContext(
        "platform-1", "tenant-b", "admin-b", frozenset({"admin"})
    )
    profile = McpServerProfile(
        platform_id="platform-1",
        tenant_id="tenant-a",
        profile_id="profile-1",
        version=1,
        name="DolphinScheduler",
        transport="stdio",
        command="/usr/local/bin/python3",
        args=(),
        working_directory_ref="workspace://dolphin",
        enabled_tool_names=("projects_list",),
        tool_catalog_sha256="b" * 64,
    )

    with pytest.raises(DomainError) as error:
        repository.save_profile(identity, profile)

    assert error.value.code == "AUTHORIZATION_DENIED"
    assert connection.query.statements == []
    assert connection.committed is False
