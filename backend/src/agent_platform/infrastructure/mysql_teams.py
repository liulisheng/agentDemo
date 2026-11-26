"""V011 Agent 团队、版本、成员和 Conversation 绑定的 MySQL 仓储。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from agent_platform.domain.common import DomainError
from agent_platform.domain.identity import IdentityContext
from agent_platform.domain.teams import (
    ConversationTeamBinding,
    Team,
    TeamMember,
    TeamVersion,
)


class MySqlTeamRepository:
    """使用 V011 表保存团队配置；所有查询都附加平台和租户条件。"""

    def __init__(self, connection_factory) -> None:
        self._connection_factory = connection_factory

    @contextmanager
    def _transaction(self) -> Iterator[tuple[Any, Any]]:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            yield connection, cursor
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    @staticmethod
    def _not_found(resource: str) -> DomainError:
        return DomainError("RESOURCE_NOT_FOUND", "资源不存在", {"resource": resource})

    def save_team(self, identity: IdentityContext, value: Team) -> None:
        with self._transaction() as (_, cursor):
            cursor.execute(
                "INSERT INTO teams (platform_id,tenant_id,team_id,name,status,current_version,created_by) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE name=VALUES(name),status=VALUES(status),current_version=VALUES(current_version)",
                (identity.platform_id, identity.tenant_id, value.team_id, value.name, value.status.value, value.current_version, identity.user_id),
            )

    def save_version(self, identity: IdentityContext, value: TeamVersion) -> None:
        import json
        policy = json.dumps({
            "orchestration_mode": value.orchestration_mode,
            "approval_mode": value.approval_mode,
            "max_concurrency": value.max_concurrency,
            "delivery_policy": value.delivery_policy,
        }, ensure_ascii=False)
        with self._transaction() as (_, cursor):
            cursor.execute(
                "INSERT INTO team_versions (platform_id,tenant_id,team_id,version,name,router_member_id,finalizer_member_id,orchestration_mode,approval_mode,policy_json,status,created_by,published_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,CASE WHEN %s='PUBLISHED' THEN CURRENT_TIMESTAMP(6) ELSE NULL END) "
                "ON DUPLICATE KEY UPDATE name=VALUES(name),router_member_id=NULL,finalizer_member_id=NULL,orchestration_mode=VALUES(orchestration_mode),approval_mode=VALUES(approval_mode),policy_json=VALUES(policy_json),status=VALUES(status),published_at=VALUES(published_at)",
                (identity.platform_id, identity.tenant_id, value.team_id, value.version, value.name, None, None, value.orchestration_mode, value.approval_mode, policy, value.status.value, identity.user_id, value.status.value),
            )
            cursor.execute(
                "SELECT member_id FROM team_members WHERE platform_id=%s AND tenant_id=%s AND team_id=%s AND team_version=%s",
                (identity.platform_id, identity.tenant_id, value.team_id, value.version),
            )
            existing_member_ids = {str(row[0]) for row in cursor.fetchall()}
            for member in value.members:
                self._save_member_cursor(cursor, identity, value.team_id, value.version, member)
            for removed_member_id in existing_member_ids.difference(member.member_id for member in value.members):
                cursor.execute(
                    "DELETE FROM team_members WHERE platform_id=%s AND tenant_id=%s AND team_id=%s AND team_version=%s AND member_id=%s",
                    (identity.platform_id, identity.tenant_id, value.team_id, value.version, removed_member_id),
                )
            # team_versions 引用 team_members，先写成员再恢复角色外键，避免首次保存时违反外键。
            cursor.execute(
                "UPDATE team_versions SET router_member_id=%s,finalizer_member_id=%s "
                "WHERE platform_id=%s AND tenant_id=%s AND team_id=%s AND version=%s",
                (value.router_member_id, value.finalizer_member_id, identity.platform_id, identity.tenant_id, value.team_id, value.version),
            )

    def _save_member_cursor(self, cursor: Any, identity: IdentityContext, team_id: str, version: int, value: TeamMember) -> None:
        import json
        cursor.execute(
            "INSERT INTO team_members (platform_id,tenant_id,team_id,team_version,member_id,employee_id,employee_version,deployment_id,role,engine_name,model_route_id,capabilities_snapshot_json,max_concurrency,status) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE role=VALUES(role),engine_name=VALUES(engine_name),model_route_id=VALUES(model_route_id),capabilities_snapshot_json=VALUES(capabilities_snapshot_json),max_concurrency=VALUES(max_concurrency),status=VALUES(status)",
            (identity.platform_id, identity.tenant_id, team_id, version, value.member_id, value.employee_id, value.employee_version, value.deployment_id, value.role.value, value.engine_name, value.model_route_id, json.dumps(list(value.capabilities)), value.max_concurrency, value.status),
        )

    def _load_version(self, identity: IdentityContext, row: tuple[Any, ...], members: tuple[TeamMember, ...]) -> TeamVersion:
        import json
        policy = json.loads(row[8] or "{}") if isinstance(row[8], (str, bytes)) else (row[8] or {})
        return TeamVersion(
            team_id=str(row[0]), platform_id=identity.platform_id, tenant_id=identity.tenant_id,
            version=int(row[1]), name=str(row[2]), members=members, router_member_id=row[3],
            finalizer_member_id=row[4], orchestration_mode=str(row[5]), approval_mode=str(row[6]),
            status=str(row[7]), max_concurrency=int(policy.get("max_concurrency", 3)),
            delivery_policy=str(policy.get("delivery_policy", "CONFIRM")),
        )

    def get_version(self, identity: IdentityContext, team_id: str, version: int) -> TeamVersion:
        connection = self._connection_factory(); cursor = connection.cursor()
        try:
            cursor.execute("SELECT team_id,version,name,router_member_id,finalizer_member_id,orchestration_mode,approval_mode,status,policy_json FROM team_versions WHERE platform_id=%s AND tenant_id=%s AND team_id=%s AND version=%s", (identity.platform_id, identity.tenant_id, team_id, version))
            row = cursor.fetchone()
            if row is None: raise self._not_found("team_version")
            cursor.execute("SELECT member_id,employee_id,employee_version,deployment_id,role,engine_name,model_route_id,capabilities_snapshot_json,max_concurrency,status FROM team_members WHERE platform_id=%s AND tenant_id=%s AND team_id=%s AND team_version=%s ORDER BY member_id", (identity.platform_id, identity.tenant_id, team_id, version))
            import json
            members = tuple(TeamMember(str(r[0]), str(r[1]), int(r[2]), str(r[3]), str(r[4]), str(r[5]), r[6], tuple(json.loads(r[7] or "[]")), int(r[8]), str(r[9])) for r in cursor.fetchall())
            return self._load_version(identity, row, members)
        finally:
            cursor.close(); connection.close()

    def get_current(self, identity: IdentityContext, team_id: str) -> TeamVersion:
        connection = self._connection_factory(); cursor = connection.cursor()
        try:
            cursor.execute("SELECT current_version FROM teams WHERE platform_id=%s AND tenant_id=%s AND team_id=%s", (identity.platform_id, identity.tenant_id, team_id))
            row = cursor.fetchone()
            if row is None: raise self._not_found("team")
            version = int(row[0])
        finally:
            cursor.close(); connection.close()
        return self.get_version(identity, team_id, version)

    def list_current(self, identity: IdentityContext) -> tuple[TeamVersion, ...]:
        connection = self._connection_factory(); cursor = connection.cursor()
        try:
            cursor.execute("SELECT team_id,current_version FROM teams WHERE platform_id=%s AND tenant_id=%s ORDER BY name", (identity.platform_id, identity.tenant_id))
            rows = cursor.fetchall()
        finally:
            cursor.close(); connection.close()
        return tuple(self.get_version(identity, str(row[0]), int(row[1])) for row in rows)

    def list_versions(self, identity: IdentityContext) -> tuple[TeamVersion, ...]:
        connection = self._connection_factory(); cursor = connection.cursor()
        try:
            cursor.execute(
                "SELECT team_id,version FROM team_versions WHERE platform_id=%s AND tenant_id=%s "
                "ORDER BY name,version DESC",
                (identity.platform_id, identity.tenant_id),
            )
            rows = cursor.fetchall()
        finally:
            cursor.close(); connection.close()
        return tuple(self.get_version(identity, str(row[0]), int(row[1])) for row in rows)

    def save_member(self, identity: IdentityContext, team_id: str, version: int, value: TeamMember) -> None:
        with self._transaction() as (_, cursor):
            self._save_member_cursor(cursor, identity, team_id, version, value)

    def save_binding(self, identity: IdentityContext, value: ConversationTeamBinding) -> None:
        with self._transaction() as (_, cursor):
            cursor.execute("INSERT INTO conversation_team_bindings (platform_id,tenant_id,conversation_id,team_id,team_version,orchestration_mode,approval_mode) VALUES (%s,%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE team_id=VALUES(team_id),team_version=VALUES(team_version),orchestration_mode=VALUES(orchestration_mode),approval_mode=VALUES(approval_mode)", (identity.platform_id, identity.tenant_id, value.conversation_id, value.team_id, value.team_version, value.orchestration_mode, value.approval_mode))

    def get_binding(self, identity: IdentityContext, conversation_id: str) -> ConversationTeamBinding:
        connection = self._connection_factory(); cursor = connection.cursor()
        try:
            cursor.execute("SELECT conversation_id,team_id,team_version,orchestration_mode,approval_mode,created_at FROM conversation_team_bindings WHERE platform_id=%s AND tenant_id=%s AND conversation_id=%s", (identity.platform_id, identity.tenant_id, conversation_id))
            row = cursor.fetchone()
            if row is None: raise self._not_found("conversation_team_binding")
            return ConversationTeamBinding(str(row[0]), str(row[1]), int(row[2]), str(row[3]), str(row[4]), row[5])
        finally:
            cursor.close(); connection.close()

    def list_bindings(self, identity: IdentityContext, team_id: str) -> tuple[ConversationTeamBinding, ...]:
        connection = self._connection_factory(); cursor = connection.cursor()
        try:
            cursor.execute("SELECT conversation_id,team_id,team_version,orchestration_mode,approval_mode,created_at FROM conversation_team_bindings WHERE platform_id=%s AND tenant_id=%s AND team_id=%s ORDER BY created_at DESC, conversation_id DESC", (identity.platform_id, identity.tenant_id, team_id))
            return tuple(ConversationTeamBinding(str(row[0]), str(row[1]), int(row[2]), str(row[3]), str(row[4]), row[5]) for row in cursor.fetchall())
        finally:
            cursor.close(); connection.close()
