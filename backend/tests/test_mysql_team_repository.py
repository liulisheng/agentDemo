from __future__ import annotations

from agent_platform.adapters.identity.fake import FakeIdentityProvider
from agent_platform.domain.teams import TeamMember, TeamVersion
from agent_platform.infrastructure.mysql_teams import MySqlTeamRepository


class _Cursor:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[object, ...]]] = []

    def execute(self, statement: str, params: tuple[object, ...]) -> None:
        self.executed.append((statement, params))

    def fetchall(self) -> list[tuple[object, ...]]:
        return []

    def close(self) -> None:
        pass


class _Connection:
    def __init__(self) -> None:
        self.sql = _Cursor()
        self.committed = False
        self.rolled_back = False

    def cursor(self) -> _Cursor:
        return self.sql

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        pass


def test_team_version_persists_members_before_router_and_finalizer_foreign_keys() -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    connection = _Connection()
    repository = MySqlTeamRepository(lambda: connection)
    version = TeamVersion(
        team_id="team-1",
        platform_id=identity.platform_id,
        tenant_id=identity.tenant_id,
        version=1,
        name="分析团队",
        members=(
            TeamMember("router-1", "employee-1", 1, "deployment-1", "ROUTER", "fake"),
            TeamMember("finalizer-1", "employee-2", 1, "deployment-2", "FINALIZER", "fake"),
        ),
        router_member_id="router-1",
        finalizer_member_id="finalizer-1",
    )

    repository.save_version(identity, version)

    statements = [statement.lower() for statement, _ in connection.sql.executed]
    insert_version = next(i for i, statement in enumerate(statements) if "insert into team_versions" in statement)
    insert_members = [i for i, statement in enumerate(statements) if "insert into team_members" in statement]
    update_roles = next(i for i, statement in enumerate(statements) if "update team_versions set router_member_id" in statement)
    version_values = connection.sql.executed[insert_version][1]
    assert version_values[5:7] == (None, None)
    assert len(insert_members) == 2
    assert insert_version < min(insert_members) < max(insert_members) < update_roles
    assert connection.committed is True
    assert connection.rolled_back is False
