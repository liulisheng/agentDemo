"""MySQL 8.4 数字员工目录、不可变版本和 Deployment 仓储。"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from agent_platform.domain.catalog import (
    AuditEventRecord,
    Deployment,
    DigitalEmployee,
    DigitalEmployeeVersion,
    ExecutionProfile,
    LifecycleStatus,
    SkillContentFormat,
    SkillFileEntry,
    SkillType,
    SkillVersion,
    ToolVersion,
)
from agent_platform.domain.common import DomainError, generate_database_id
from agent_platform.domain.conversations import CapabilityBinding
from agent_platform.domain.identity import IdentityContext
from agent_platform.domain.model_routes import (
    ModelCatalogItem,
    ModelConnection,
    ModelProfile,
    ModelRoute,
    ModelTestRecord,
    ModelTestResult,
    SecretRef,
    legacy_model_connection_id,
)
from agent_platform.ports.workspaces import SandboxProfile, WorkspaceMode, WorkspaceProfile


def _json_dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _json_load(value: object, default: object) -> object:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    if isinstance(value, str):
        return json.loads(value)
    raise TypeError("database JSON value has an unsupported type")


def _identity_key(identity: IdentityContext, resource_id: str) -> tuple[str, str, str]:
    return identity.platform_id, identity.tenant_id, resource_id


class MySqlCatalogRepository:
    """以显式平台/租户谓词读写目录资源，领域服务不接触 SQL 行类型。"""

    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connection_factory = connection_factory

    @contextmanager
    def _transaction(self) -> Iterator[tuple[Any, Any]]:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            connection.start_transaction()
            yield connection, cursor
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    @contextmanager
    def _reader(self) -> Iterator[tuple[Any, Any]]:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            yield connection, cursor
        finally:
            cursor.close()
            connection.close()

    @staticmethod
    def _not_found(resource: str) -> DomainError:
        return DomainError("RESOURCE_NOT_FOUND", "资源不存在", {"resource": resource})

    @staticmethod
    def _assert_version_mutable(current_status: str, existing: object, requested: object, resource: str) -> bool:
        if current_status == LifecycleStatus.DRAFT.value:
            return False
        if existing == requested:
            return True
        raise DomainError("IMMUTABLE_VERSION", f"已发布的{resource}版本不可修改")

    @staticmethod
    def _load_tool(row: tuple[Any, ...]) -> ToolVersion:
        return ToolVersion(
            tool_id=str(row[0]),
            tenant_id=str(row[1]),
            name=str(row[2]),
            description=str(row[3]),
            input_schema=_json_load(row[4], {"type": "object"}),
            version=int(row[5]),
            status=LifecycleStatus(str(row[6])),
            handler_name=str(row[7] or "echo"),
            protocol=str(row[8]),
            requires_recipient_credentials=bool(row[10]),
            auth_secret_ref=str(row[9]) if row[9] is not None else None,
            mcp_server_profile_id=str(row[11]) if row[11] is not None else None,
            mcp_server_profile_version=(
                int(row[12]) if row[12] is not None else None
            ),
            remote_tool_name=str(row[13]) if row[13] is not None else None,
            input_schema_sha256=str(row[14]) if row[14] is not None else None,
            preview_tool_name=str(row[15]) if row[15] is not None else None,
            mcp_risk_level=str(row[16]),
            idempotency_support=bool(row[17]),
        )

    @staticmethod
    def _tool_select() -> str:
        return (
            "SELECT tool_id, tenant_id, name, description, input_schema_json, "
            "version, status, handler_name, protocol, auth_secret_ref, "
            "(requires_recipient_credentials OR auth_secret_ref IS NOT NULL), "
            "mcp_server_profile_id, mcp_server_profile_version, remote_tool_name, "
            "input_schema_sha256, preview_tool_name, mcp_risk_level, idempotency_support "
            "FROM tool_versions"
        )

    def save_tool(self, identity: IdentityContext, value: ToolVersion) -> None:
        """保存 Tool 草稿或发布状态；已发布版本重复写入只允许幂等 no-op。"""

        with self._transaction() as (_, cursor):
            cursor.execute(
                "SELECT status, name, description, input_schema_json, handler_name, protocol, "
                "auth_secret_ref, requires_recipient_credentials "
                "FROM tool_versions WHERE platform_id=%s AND tenant_id=%s AND tool_id=%s AND version=%s FOR UPDATE",
                (identity.platform_id, identity.tenant_id, value.tool_id, value.version),
            )
            current = cursor.fetchone()
            if current is not None and current[0] != LifecycleStatus.DRAFT.value:
                existing = self.get_tool_version(identity, value.tool_id, value.version)
                if self._assert_version_mutable(str(current[0]), existing, value, "Tool"):
                    return
            cursor.execute(
                "INSERT INTO tool_versions "
                "(platform_id, tenant_id, tool_id, version, name, protocol, description, "
                "input_schema_json, output_schema_json, auth_secret_ref, requires_recipient_credentials, "
                "timeout_ms, retry_policy_json, "
                "risk_policy, handler_name, status, mcp_server_profile_id, "
                "mcp_server_profile_version, remote_tool_name, input_schema_sha256, "
                "preview_tool_name, mcp_risk_level, idempotency_support) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,30000,%s,'low',%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE name=VALUES(name), description=VALUES(description), "
                "input_schema_json=VALUES(input_schema_json), output_schema_json=VALUES(output_schema_json), "
                "retry_policy_json=VALUES(retry_policy_json), handler_name=VALUES(handler_name), "
                "protocol=VALUES(protocol), auth_secret_ref=VALUES(auth_secret_ref), "
                "requires_recipient_credentials=VALUES(requires_recipient_credentials), status=VALUES(status), "
                "mcp_server_profile_id=VALUES(mcp_server_profile_id), "
                "mcp_server_profile_version=VALUES(mcp_server_profile_version), "
                "remote_tool_name=VALUES(remote_tool_name), input_schema_sha256=VALUES(input_schema_sha256), "
                "preview_tool_name=VALUES(preview_tool_name), mcp_risk_level=VALUES(mcp_risk_level), "
                "idempotency_support=VALUES(idempotency_support)",
                (
                    identity.platform_id,
                    identity.tenant_id,
                    value.tool_id,
                    value.version,
                    value.name,
                    value.protocol,
                    value.description,
                    _json_dump(dict(value.input_schema)),
                    _json_dump({"type": "object"}),
                    value.auth_secret_ref,
                    value.requires_recipient_credentials,
                    _json_dump({"max_retries": 0}),
                    value.handler_name,
                    value.status.value,
                    value.mcp_server_profile_id,
                    value.mcp_server_profile_version,
                    value.remote_tool_name,
                    value.input_schema_sha256,
                    value.preview_tool_name,
                    value.mcp_risk_level.value,
                    value.idempotency_support,
                ),
            )

    def get_tool_version(self, identity: IdentityContext, tool_id: str, version: int) -> ToolVersion:
        with self._reader() as (_, cursor):
            cursor.execute(
                self._tool_select()
                + " WHERE platform_id=%s AND tenant_id=%s AND tool_id=%s AND version=%s",
                (identity.platform_id, identity.tenant_id, tool_id, version),
            )
            row = cursor.fetchone()
        if row is None:
            raise self._not_found("tool")
        return self._load_tool(row)

    def get_tool(self, identity: IdentityContext, tool_id: str) -> ToolVersion:
        with self._reader() as (_, cursor):
            cursor.execute(
                self._tool_select()
                + " WHERE platform_id=%s AND tenant_id=%s AND tool_id=%s ORDER BY version DESC LIMIT 1",
                (identity.platform_id, identity.tenant_id, tool_id),
            )
            row = cursor.fetchone()
        if row is None:
            raise self._not_found("tool")
        return self._load_tool(row)

    def list_tools(self, identity: IdentityContext) -> tuple[ToolVersion, ...]:
        with self._reader() as (_, cursor):
            cursor.execute(
                self._tool_select()
                + " WHERE platform_id=%s AND tenant_id=%s ORDER BY name, version DESC",
                (identity.platform_id, identity.tenant_id),
            )
            rows = cursor.fetchall()
        latest: dict[str, ToolVersion] = {}
        for row in rows:
            latest.setdefault(str(row[0]), self._load_tool(row))
        return tuple(latest.values())

    def delete_tool(self, identity: IdentityContext, tool_id: str) -> None:
        current = self.get_tool(identity, tool_id)
        current.assert_mutable()
        with self._transaction() as (_, cursor):
            cursor.execute(
                "DELETE FROM tool_versions WHERE platform_id=%s AND tenant_id=%s AND tool_id=%s AND version=%s AND status='DRAFT'",
                (identity.platform_id, identity.tenant_id, tool_id, current.version),
            )
            if cursor.rowcount != 1:
                raise DomainError("INVALID_STATE", "Tool 已变化，不能删除")

    @staticmethod
    def _load_skill(
        row: tuple[Any, ...],
        allowed: tuple[tuple[str, int], ...],
        file_entries: tuple[SkillFileEntry, ...] = (),
    ) -> SkillVersion:
        policy = _json_load(row[11], {"name": "none"})
        approval = _json_load(row[12], {"name": "none"})
        return SkillVersion(
            skill_id=str(row[0]),
            tenant_id=str(row[1]),
            name=str(row[2]),
            skill_type=SkillType(str(row[3])),
            instruction=str(row[4]),
            allowed_tool_ids=tuple(tool_id for tool_id, _ in allowed),
            input_schema=_json_load(row[5], {"type": "object"}),
            output_schema=_json_load(row[6], {"type": "object"}),
            trigger=str(row[7]) if row[7] is not None else None,
            workflow_steps=tuple(_json_load(row[8], [])),
            memory_policy=str(policy.get("name", "none")),
            approval_policy=str(approval.get("name", "none")),
            version=int(row[9]),
            status=LifecycleStatus(str(row[10])),
            allowed_tool_versions=allowed,
            content_format=SkillContentFormat(str(row[13])) if len(row) > 13 else SkillContentFormat.INLINE,
            entry_path=str(row[14]) if len(row) > 14 and row[14] is not None else None,
            file_manifest_sha256=str(row[15]) if len(row) > 15 and row[15] is not None else None,
            file_entries=file_entries,
        )

    @staticmethod
    def _skill_select() -> str:
        return (
            "SELECT skill_id, tenant_id, name, skill_type, instruction, input_schema_json, "
            "output_schema_json, trigger_rule, workflow_steps_json, version, status, "
            "memory_policy_json, approval_policy_json, content_format, entry_path, "
            "file_manifest_sha256 FROM skill_versions"
        )

    @staticmethod
    def _skill_file_entries(
        cursor: Any, identity: IdentityContext, skill_id: str, version: int
    ) -> tuple[SkillFileEntry, ...]:
        cursor.execute(
            "SELECT file_path, media_asset_id, mime_type, size_bytes, content_sha256 "
            "FROM skill_file_entries WHERE platform_id=%s AND tenant_id=%s AND skill_id=%s "
            "AND version=%s ORDER BY file_path",
            (identity.platform_id, identity.tenant_id, skill_id, version),
        )
        return tuple(
            SkillFileEntry(
                file_path=str(row[0]),
                media_asset_id=str(row[1]),
                mime_type=str(row[2]),
                size_bytes=int(row[3]),
                content_sha256=str(row[4]),
            )
            for row in cursor.fetchall()
        )

    def _skill_tool_refs(self, cursor: Any, identity: IdentityContext, skill_id: str, version: int) -> tuple[tuple[str, int], ...]:
        cursor.execute(
            "SELECT tool_id, tool_version FROM skill_tool_declarations "
            "WHERE platform_id=%s AND tenant_id=%s AND skill_id=%s AND skill_version=%s ORDER BY sequence_no",
            (identity.platform_id, identity.tenant_id, skill_id, version),
        )
        return tuple((str(row[0]), int(row[1])) for row in cursor.fetchall())

    def save_skill(self, identity: IdentityContext, value: SkillVersion) -> None:
        """保存 Skill 内容和其逐版本 Tool allowlist，作为一个数据库事务提交。"""

        self._save_skill(identity, value, expected_revision=None)

    def save_skill_if_revision_matches(
        self,
        identity: IdentityContext,
        value: SkillVersion,
        expected_revision: SkillVersion,
    ) -> None:
        """在行锁内对比完整 DRAFT 修订，防止 ZIP 预览覆盖并发编辑。"""

        self._save_skill(identity, value, expected_revision=expected_revision)

    def _save_skill(
        self,
        identity: IdentityContext,
        value: SkillVersion,
        *,
        expected_revision: SkillVersion | None,
    ) -> None:
        with self._transaction() as (_, cursor):
            cursor.execute(
                "SELECT status FROM skill_versions WHERE platform_id=%s AND tenant_id=%s AND skill_id=%s AND version=%s FOR UPDATE",
                (identity.platform_id, identity.tenant_id, value.skill_id, value.version),
            )
            current = cursor.fetchone()
            existing: SkillVersion | None = None
            if expected_revision is not None:
                if current is None:
                    raise DomainError("IDEMPOTENCY_CONFLICT", "Skill 在 ZIP 预览后已被删除")
                cursor.execute(
                    self._skill_select()
                    + " WHERE platform_id=%s AND tenant_id=%s AND skill_id=%s AND version=%s",
                    (identity.platform_id, identity.tenant_id, value.skill_id, value.version),
                )
                row = cursor.fetchone()
                existing = self._load_skill(
                    row,
                    self._skill_tool_refs(cursor, identity, value.skill_id, value.version),
                    self._skill_file_entries(cursor, identity, value.skill_id, value.version),
                )
                if existing != expected_revision:
                    raise DomainError("IDEMPOTENCY_CONFLICT", "Skill 在 ZIP 预览后已被修改")
            if current is not None and current[0] != LifecycleStatus.DRAFT.value:
                if existing is None:
                    cursor.execute(
                        self._skill_select()
                        + " WHERE platform_id=%s AND tenant_id=%s AND skill_id=%s AND version=%s",
                        (identity.platform_id, identity.tenant_id, value.skill_id, value.version),
                    )
                    row = cursor.fetchone()
                    existing = self._load_skill(
                        row,
                        self._skill_tool_refs(cursor, identity, value.skill_id, value.version),
                        self._skill_file_entries(cursor, identity, value.skill_id, value.version),
                    )
                if self._assert_version_mutable(str(current[0]), existing, value, "Skill"):
                    return
            cursor.execute(
                "INSERT INTO skill_versions "
                "(platform_id, tenant_id, skill_id, version, name, skill_type, instruction, "
                "input_schema_json, output_schema_json, trigger_rule, workflow_steps_json, "
                "memory_policy_json, approval_policy_json, status, content_format, entry_path, file_manifest_sha256) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE name=VALUES(name), skill_type=VALUES(skill_type), "
                "instruction=VALUES(instruction), input_schema_json=VALUES(input_schema_json), "
                "output_schema_json=VALUES(output_schema_json), trigger_rule=VALUES(trigger_rule), "
                "workflow_steps_json=VALUES(workflow_steps_json), memory_policy_json=VALUES(memory_policy_json), "
                "approval_policy_json=VALUES(approval_policy_json), status=VALUES(status), "
                "content_format=VALUES(content_format), entry_path=VALUES(entry_path), "
                "file_manifest_sha256=VALUES(file_manifest_sha256)",
                (
                    identity.platform_id,
                    identity.tenant_id,
                    value.skill_id,
                    value.version,
                    value.name,
                    value.skill_type.value,
                    value.instruction,
                    _json_dump(dict(value.input_schema)),
                    _json_dump(dict(value.output_schema)),
                    value.trigger,
                    _json_dump(list(value.workflow_steps)),
                    _json_dump({"name": value.memory_policy}),
                    _json_dump({"name": value.approval_policy}),
                    value.status.value,
                    value.content_format.value,
                    value.entry_path,
                    value.file_manifest_sha256,
                ),
            )
            cursor.execute(
                "DELETE FROM skill_tool_declarations WHERE platform_id=%s AND tenant_id=%s AND skill_id=%s AND skill_version=%s",
                (identity.platform_id, identity.tenant_id, value.skill_id, value.version),
            )
            cursor.execute(
                "DELETE FROM skill_file_entries WHERE platform_id=%s AND tenant_id=%s "
                "AND skill_id=%s AND version=%s",
                (identity.platform_id, identity.tenant_id, value.skill_id, value.version),
            )
            for entry in value.file_entries:
                cursor.execute(
                    "INSERT INTO skill_file_entries "
                    "(platform_id, tenant_id, skill_id, version, file_path, media_asset_id, "
                    "mime_type, size_bytes, content_sha256) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        identity.platform_id,
                        identity.tenant_id,
                        value.skill_id,
                        value.version,
                        entry.file_path,
                        entry.media_asset_id,
                        entry.mime_type,
                        entry.size_bytes,
                        entry.content_sha256,
                    ),
                )
            for sequence, (tool_id, tool_version) in enumerate(value.allowed_tool_versions, start=1):
                cursor.execute(
                    "INSERT INTO skill_tool_declarations "
                    "(platform_id, tenant_id, skill_id, skill_version, tool_id, tool_version, sequence_no) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (
                        identity.platform_id,
                        identity.tenant_id,
                        value.skill_id,
                        value.version,
                        tool_id,
                        tool_version,
                        sequence,
                    ),
                )

    def get_skill_version(self, identity: IdentityContext, skill_id: str, version: int) -> SkillVersion:
        with self._reader() as (_, cursor):
            cursor.execute(
                self._skill_select()
                + " WHERE platform_id=%s AND tenant_id=%s AND skill_id=%s AND version=%s",
                (identity.platform_id, identity.tenant_id, skill_id, version),
            )
            row = cursor.fetchone()
            if row is None:
                raise self._not_found("skill")
            allowed = self._skill_tool_refs(cursor, identity, skill_id, version)
            file_entries = self._skill_file_entries(cursor, identity, skill_id, version)
        return self._load_skill(row, allowed, file_entries)

    def get_skill(self, identity: IdentityContext, skill_id: str) -> SkillVersion:
        with self._reader() as (_, cursor):
            cursor.execute(
                self._skill_select()
                + " WHERE platform_id=%s AND tenant_id=%s AND skill_id=%s ORDER BY version DESC LIMIT 1",
                (identity.platform_id, identity.tenant_id, skill_id),
            )
            row = cursor.fetchone()
            if row is None:
                raise self._not_found("skill")
            allowed = self._skill_tool_refs(cursor, identity, skill_id, int(row[9]))
            file_entries = self._skill_file_entries(cursor, identity, skill_id, int(row[9]))
        return self._load_skill(row, allowed, file_entries)

    def list_skill_versions(self, identity: IdentityContext, skill_id: str) -> tuple[SkillVersion, ...]:
        """读取同一租户 Skill 的所有精确版本，供版本历史和 fork 使用。"""

        with self._reader() as (_, cursor):
            cursor.execute(
                "SELECT version FROM skill_versions WHERE platform_id=%s AND tenant_id=%s "
                "AND skill_id=%s ORDER BY version",
                (identity.platform_id, identity.tenant_id, skill_id),
            )
            versions = tuple(int(row[0]) for row in cursor.fetchall())
        if not versions:
            raise self._not_found("skill")
        return tuple(self.get_skill_version(identity, skill_id, version) for version in versions)

    def fork_skill_version(
        self, identity: IdentityContext, skill_id: str, source_version: int
    ) -> SkillVersion:
        """在事务中分配新版本并复制文件/Tool 清单引用，避免并发 fork 覆盖草稿。"""

        with self._transaction() as (_, cursor):
            cursor.execute(
                "SELECT status FROM skill_versions WHERE platform_id=%s AND tenant_id=%s "
                "AND skill_id=%s AND version=%s FOR UPDATE",
                (identity.platform_id, identity.tenant_id, skill_id, source_version),
            )
            source = cursor.fetchone()
            if source is None:
                raise self._not_found("skill")
            if str(source[0]) != LifecycleStatus.PUBLISHED.value:
                raise DomainError("INVALID_STATE", "只能基于已发布 Skill 版本创建草稿")
            cursor.execute(
                "SELECT version FROM skill_versions WHERE platform_id=%s AND tenant_id=%s "
                "AND skill_id=%s ORDER BY version DESC LIMIT 1 FOR UPDATE",
                (identity.platform_id, identity.tenant_id, skill_id),
            )
            cursor.fetchone()
            cursor.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM skill_versions "
                "WHERE platform_id=%s AND tenant_id=%s AND skill_id=%s",
                (identity.platform_id, identity.tenant_id, skill_id),
            )
            next_version = int(cursor.fetchone()[0])
            cursor.execute(
                "INSERT INTO skill_versions "
                "(platform_id, tenant_id, skill_id, version, name, skill_type, instruction, "
                "input_schema_json, output_schema_json, trigger_rule, workflow_steps_json, "
                "memory_policy_json, approval_policy_json, status, content_format, entry_path, file_manifest_sha256) "
                "SELECT platform_id, tenant_id, skill_id, %s, name, skill_type, instruction, "
                "input_schema_json, output_schema_json, trigger_rule, workflow_steps_json, "
                "memory_policy_json, approval_policy_json, 'DRAFT', content_format, entry_path, file_manifest_sha256 "
                "FROM skill_versions WHERE platform_id=%s AND tenant_id=%s AND skill_id=%s AND version=%s",
                (next_version, identity.platform_id, identity.tenant_id, skill_id, source_version),
            )
            cursor.execute(
                "INSERT INTO skill_tool_declarations "
                "(platform_id, tenant_id, skill_id, skill_version, tool_id, tool_version, sequence_no) "
                "SELECT platform_id, tenant_id, skill_id, %s, tool_id, tool_version, sequence_no "
                "FROM skill_tool_declarations WHERE platform_id=%s AND tenant_id=%s AND skill_id=%s AND skill_version=%s",
                (next_version, identity.platform_id, identity.tenant_id, skill_id, source_version),
            )
            cursor.execute(
                "INSERT INTO skill_file_entries "
                "(platform_id, tenant_id, skill_id, version, file_path, media_asset_id, mime_type, size_bytes, content_sha256) "
                "SELECT platform_id, tenant_id, skill_id, %s, file_path, media_asset_id, mime_type, size_bytes, content_sha256 "
                "FROM skill_file_entries WHERE platform_id=%s AND tenant_id=%s AND skill_id=%s AND version=%s",
                (next_version, identity.platform_id, identity.tenant_id, skill_id, source_version),
            )
        return self.get_skill_version(identity, skill_id, next_version)

    def list_skills(self, identity: IdentityContext) -> tuple[SkillVersion, ...]:
        with self._reader() as (_, cursor):
            cursor.execute(
                self._skill_select()
                + " WHERE platform_id=%s AND tenant_id=%s ORDER BY skill_id, version DESC",
                (identity.platform_id, identity.tenant_id),
            )
            rows = cursor.fetchall()
            values: list[SkillVersion] = []
            seen: set[str] = set()
            for row in rows:
                skill_id = str(row[0])
                if skill_id in seen:
                    continue
                seen.add(skill_id)
                allowed = self._skill_tool_refs(cursor, identity, skill_id, int(row[9]))
                files = self._skill_file_entries(cursor, identity, skill_id, int(row[9]))
                values.append(self._load_skill(row, allowed, files))
        return tuple(values)

    def delete_skill(self, identity: IdentityContext, skill_id: str) -> None:
        current = self.get_skill(identity, skill_id)
        current.assert_mutable()
        with self._transaction() as (_, cursor):
            cursor.execute(
                "DELETE FROM skill_file_entries WHERE platform_id=%s AND tenant_id=%s "
                "AND skill_id=%s AND version=%s",
                (
                    identity.platform_id,
                    identity.tenant_id,
                    skill_id,
                    current.version,
                ),
            )
            cursor.execute(
                "DELETE FROM skill_versions WHERE platform_id=%s AND tenant_id=%s AND skill_id=%s AND version=%s AND status='DRAFT'",
                (identity.platform_id, identity.tenant_id, skill_id, current.version),
            )
            if cursor.rowcount != 1:
                raise DomainError("INVALID_STATE", "Skill 已变化，不能删除")

    @staticmethod
    def _load_employee(row: tuple[Any, ...]) -> DigitalEmployee:
        return DigitalEmployee(
            employee_id=str(row[0]),
            tenant_id=str(row[1]),
            name=str(row[2]),
            role_name=str(row[3]),
            description=str(row[4]),
            status=LifecycleStatus(str(row[5])),
            published_to_gallery=bool(row[6]),
        )

    def save_employee(self, identity: IdentityContext, value: DigitalEmployee) -> None:
        """保存员工稳定资料；员工版本内容单独通过 save_employee_version 保存。"""

        with self._transaction() as (_, cursor):
            cursor.execute(
                "INSERT INTO digital_employees "
                "(platform_id, tenant_id, employee_id, owner_user_id, name, role_name, description, status, published_to_gallery) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE "
                "name=VALUES(name), role_name=VALUES(role_name), description=VALUES(description), "
                "status=VALUES(status), published_to_gallery=VALUES(published_to_gallery)",
                (
                    identity.platform_id,
                    identity.tenant_id,
                    value.employee_id,
                    identity.user_id,
                    value.name,
                    value.role_name,
                    value.description,
                    value.status.value,
                    value.published_to_gallery,
                ),
            )

    def delete_employee(self, identity: IdentityContext, employee_id: str) -> None:
        """仅删除尚无任何版本的员工草稿，避免删除被 Deployment 引用的历史。"""

        with self._transaction() as (_, cursor):
            cursor.execute(
                "SELECT employee_id FROM digital_employees WHERE platform_id=%s AND tenant_id=%s "
                "AND employee_id=%s FOR UPDATE",
                (identity.platform_id, identity.tenant_id, employee_id),
            )
            if cursor.fetchone() is None:
                raise self._not_found("digital_employee")
            cursor.execute(
                "SELECT COUNT(*) FROM digital_employee_versions WHERE platform_id=%s AND tenant_id=%s AND employee_id=%s",
                (identity.platform_id, identity.tenant_id, employee_id),
            )
            if int(cursor.fetchone()[0]) > 0:
                raise DomainError("INVALID_STATE", "员工已有关联版本，不能物理删除")
            cursor.execute(
                "DELETE FROM digital_employees WHERE platform_id=%s AND tenant_id=%s AND employee_id=%s",
                (identity.platform_id, identity.tenant_id, employee_id),
            )

    def get_employee(self, identity: IdentityContext, employee_id: str) -> DigitalEmployee:
        with self._reader() as (_, cursor):
            cursor.execute(
                "SELECT employee_id, tenant_id, name, role_name, description, status, published_to_gallery "
                "FROM digital_employees WHERE platform_id=%s AND tenant_id=%s AND employee_id=%s",
                (identity.platform_id, identity.tenant_id, employee_id),
            )
            row = cursor.fetchone()
        if row is None:
            raise self._not_found("digital_employee")
        return self._load_employee(row)

    def list_employees(self, identity: IdentityContext) -> tuple[DigitalEmployee, ...]:
        with self._reader() as (_, cursor):
            cursor.execute(
                "SELECT employee_id, tenant_id, name, role_name, description, status, published_to_gallery "
                "FROM digital_employees WHERE platform_id=%s AND tenant_id=%s ORDER BY name, employee_id",
                (identity.platform_id, identity.tenant_id),
            )
            rows = cursor.fetchall()
        return tuple(self._load_employee(row) for row in rows)

    def save_employee_version(self, identity: IdentityContext, value: DigitalEmployeeVersion) -> None:
        """在一个事务内保存不可变员工版本及其 Skill/Tool 版本绑定。"""

        with self._transaction() as (_, cursor):
            cursor.execute(
                "SELECT status FROM digital_employee_versions WHERE platform_id=%s AND tenant_id=%s "
                "AND employee_id=%s AND version=%s FOR UPDATE",
                (identity.platform_id, identity.tenant_id, value.employee_id, value.version),
            )
            current = cursor.fetchone()
            if current is not None:
                existing = self.get_employee_version(identity, value.employee_id, value.version)
                if MySqlCatalogRepository._assert_version_mutable(
                    str(current[0]), existing, value, "数字员工"
                ):
                    return
            cursor.execute(
                "INSERT INTO digital_employee_versions "
                "(platform_id, tenant_id, employee_id, version, system_prompt, execution_profile_id, "
                "execution_profile_version, model_route_id, workspace_profile_id, workspace_profile_version, "
                "sandbox_profile_id, sandbox_profile_version, approval_policy_json, status) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE system_prompt=VALUES(system_prompt), "
                "execution_profile_id=VALUES(execution_profile_id), execution_profile_version=VALUES(execution_profile_version), "
                "model_route_id=VALUES(model_route_id), workspace_profile_id=VALUES(workspace_profile_id), "
                "workspace_profile_version=VALUES(workspace_profile_version), sandbox_profile_id=VALUES(sandbox_profile_id), "
                "sandbox_profile_version=VALUES(sandbox_profile_version), approval_policy_json=VALUES(approval_policy_json), "
                "status=VALUES(status)",
                (
                    identity.platform_id,
                    identity.tenant_id,
                    value.employee_id,
                    value.version,
                    value.system_prompt,
                    value.execution_profile_id,
                    value.execution_profile_version,
                    value.model_route_id,
                    value.workspace_profile_id,
                    value.workspace_profile_version,
                    value.sandbox_profile_id,
                    value.sandbox_profile_version,
                    _json_dump({"name": value.approval_policy}),
                    value.status.value,
                ),
            )
            for table in ("employee_skill_bindings", "employee_tool_bindings"):
                cursor.execute(
                    f"DELETE FROM {table} WHERE platform_id=%s AND tenant_id=%s AND employee_id=%s AND employee_version=%s",
                    (identity.platform_id, identity.tenant_id, value.employee_id, value.version),
                )
            cursor.execute(
                "DELETE FROM capability_bindings WHERE platform_id=%s AND tenant_id=%s "
                "AND employee_id=%s AND employee_version=%s",
                (identity.platform_id, identity.tenant_id, value.employee_id, value.version),
            )
            for sequence, (skill_id, skill_version) in enumerate(value.skill_version_refs, start=1):
                cursor.execute(
                    "INSERT INTO employee_skill_bindings "
                    "(platform_id, tenant_id, employee_id, employee_version, skill_id, skill_version, "
                    "sequence_no, input_mapping_json, output_policy_json) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        identity.platform_id,
                        identity.tenant_id,
                        value.employee_id,
                        value.version,
                        skill_id,
                        skill_version,
                        sequence,
                        _json_dump({}),
                        _json_dump({}),
                    ),
                )
                cursor.execute(
                    "INSERT INTO capability_bindings "
                    "(platform_id, tenant_id, binding_id, employee_id, employee_version, slot, module_id, "
                    "module_version, config_revision, resource_ref, config_json) "
                    "VALUES (%s,%s,%s,%s,%s,'skill',%s,%s,1,%s,%s)",
                    (
                        identity.platform_id,
                        identity.tenant_id,
                        generate_database_id(),
                        value.employee_id,
                        value.version,
                        skill_id,
                        skill_version,
                        f"skill:{skill_id}:v{skill_version}",
                        _json_dump({}),
                    ),
                )
            for sequence, (tool_id, tool_version) in enumerate(value.tool_version_refs, start=1):
                cursor.execute(
                    "INSERT INTO employee_tool_bindings "
                    "(platform_id, tenant_id, employee_id, employee_version, tool_id, tool_version, sequence_no, config_json) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        identity.platform_id,
                        identity.tenant_id,
                        value.employee_id,
                        value.version,
                        tool_id,
                        tool_version,
                        sequence,
                        _json_dump({}),
                    ),
                )
                cursor.execute(
                    "INSERT INTO capability_bindings "
                    "(platform_id, tenant_id, binding_id, employee_id, employee_version, slot, module_id, "
                    "module_version, config_revision, resource_ref, config_json) "
                    "VALUES (%s,%s,%s,%s,%s,'tool',%s,%s,1,%s,%s)",
                    (
                        identity.platform_id,
                        identity.tenant_id,
                        generate_database_id(),
                        value.employee_id,
                        value.version,
                        tool_id,
                        tool_version,
                        f"tool:{tool_id}:v{tool_version}",
                        _json_dump({}),
                    ),
                )

    def list_capability_bindings(
        self, identity: IdentityContext, employee_id: str, version: int
    ) -> tuple[CapabilityBinding, ...]:
        """按槽位读取员工版本固化的 Skill/Tool 资源版本。"""

        with self._reader() as (_, cursor):
            cursor.execute(
                "SELECT binding_id, slot, module_id, module_version, config_revision, resource_ref "
                "FROM capability_bindings WHERE platform_id=%s AND tenant_id=%s "
                "AND employee_id=%s AND employee_version=%s ORDER BY slot, module_id",
                (identity.platform_id, identity.tenant_id, employee_id, version),
            )
            rows = cursor.fetchall()
        return tuple(
            CapabilityBinding(
                binding_id=str(row[0]),
                employee_version_id=f"{employee_id}:v{version}",
                slot=str(row[1]),
                module_id=str(row[2]),
                module_version=int(row[3]),
                resource_ref=str(row[5]),
            )
            for row in rows
        )

    def get_employee_version(
        self, identity: IdentityContext, employee_id: str, version: int
    ) -> DigitalEmployeeVersion:
        with self._reader() as (_, cursor):
            return self._read_employee_version(cursor, identity, employee_id, version)

    def _read_employee_version(
        self,
        cursor: Any,
        identity: IdentityContext,
        employee_id: str,
        version: int,
        *,
        lock: bool = False,
    ) -> DigitalEmployeeVersion:
        lock_clause = " FOR UPDATE" if lock else ""
        cursor.execute(
            "SELECT employee_id, tenant_id, version, system_prompt, execution_profile_id, "
            "execution_profile_version, model_route_id, workspace_profile_id, workspace_profile_version, "
            "sandbox_profile_id, sandbox_profile_version, approval_policy_json, status FROM digital_employee_versions "
            "WHERE platform_id=%s AND tenant_id=%s AND employee_id=%s AND version=%s" + lock_clause,
            (identity.platform_id, identity.tenant_id, employee_id, version),
        )
        row = cursor.fetchone()
        if row is None:
            raise self._not_found("digital_employee_version")
        cursor.execute(
            "SELECT skill_id, skill_version FROM employee_skill_bindings "
            "WHERE platform_id=%s AND tenant_id=%s AND employee_id=%s AND employee_version=%s ORDER BY sequence_no",
            (identity.platform_id, identity.tenant_id, employee_id, version),
        )
        skill_refs = tuple((str(item[0]), int(item[1])) for item in cursor.fetchall())
        cursor.execute(
            "SELECT tool_id, tool_version FROM employee_tool_bindings "
            "WHERE platform_id=%s AND tenant_id=%s AND employee_id=%s AND employee_version=%s ORDER BY sequence_no",
            (identity.platform_id, identity.tenant_id, employee_id, version),
        )
        tool_refs = tuple((str(item[0]), int(item[1])) for item in cursor.fetchall())
        approval = _json_load(row[11], {"name": "none"})
        return DigitalEmployeeVersion(
            employee_id=str(row[0]),
            tenant_id=str(row[1]),
            version=int(row[2]),
            system_prompt=str(row[3]),
            skill_version_ids=tuple(item[0] for item in skill_refs),
            tool_version_ids=tuple(item[0] for item in tool_refs),
            execution_profile_id=str(row[4]),
            status=LifecycleStatus(str(row[12])),
            model_route_id=str(row[6]) if row[6] is not None else None,
            workspace_profile_id=str(row[7]) if row[7] is not None else None,
            workspace_profile_version=int(row[8]) if row[8] is not None else None,
            sandbox_profile_id=str(row[9]) if row[9] is not None else None,
            sandbox_profile_version=int(row[10]) if row[10] is not None else None,
            approval_policy=str(approval.get("name", "none")),
            execution_profile_version=int(row[5]),
            skill_version_refs=skill_refs,
            tool_version_refs=tool_refs,
        )

    def list_employee_versions(
        self, identity: IdentityContext, employee_id: str
    ) -> tuple[DigitalEmployeeVersion, ...]:
        with self._reader() as (_, cursor):
            cursor.execute(
                "SELECT version FROM digital_employee_versions WHERE platform_id=%s AND tenant_id=%s "
                "AND employee_id=%s ORDER BY version",
                (identity.platform_id, identity.tenant_id, employee_id),
            )
            versions = tuple(int(row[0]) for row in cursor.fetchall())
        return tuple(
            self.get_employee_version(identity, employee_id, version) for version in versions
        )

    def fork_employee_version(
        self, identity: IdentityContext, employee_id: str, source_version: int
    ) -> DigitalEmployeeVersion:
        """锁定员工聚合根后复制版本和全部绑定，序列化并发版本号分配。"""

        with self._transaction() as (_, cursor):
            if self._lock_employee_status(cursor, identity, employee_id) is LifecycleStatus.ARCHIVED:
                raise DomainError("INVALID_STATE", "已归档员工不能创建新版本")
            cursor.execute(
                "SELECT status FROM digital_employee_versions WHERE platform_id=%s AND tenant_id=%s "
                "AND employee_id=%s AND version=%s FOR UPDATE",
                (identity.platform_id, identity.tenant_id, employee_id, source_version),
            )
            source = cursor.fetchone()
            if source is None:
                raise self._not_found("digital_employee_version")
            if LifecycleStatus(str(source[0])) not in (LifecycleStatus.PUBLISHED, LifecycleStatus.DEPRECATED):
                raise DomainError("INVALID_STATE", "只能基于已发布或已下线员工版本创建草稿")
            cursor.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM digital_employee_versions "
                "WHERE platform_id=%s AND tenant_id=%s AND employee_id=%s",
                (identity.platform_id, identity.tenant_id, employee_id),
            )
            next_version = int(cursor.fetchone()[0])
            cursor.execute(
                "INSERT INTO digital_employee_versions "
                "(platform_id, tenant_id, employee_id, version, system_prompt, execution_profile_id, "
                "execution_profile_version, model_route_id, workspace_profile_id, workspace_profile_version, "
                "sandbox_profile_id, sandbox_profile_version, approval_policy_json, status) "
                "SELECT platform_id, tenant_id, employee_id, %s, system_prompt, execution_profile_id, "
                "execution_profile_version, model_route_id, workspace_profile_id, workspace_profile_version, "
                "sandbox_profile_id, sandbox_profile_version, approval_policy_json, 'DRAFT' "
                "FROM digital_employee_versions WHERE platform_id=%s AND tenant_id=%s AND employee_id=%s AND version=%s",
                (next_version, identity.platform_id, identity.tenant_id, employee_id, source_version),
            )
            cursor.execute(
                "INSERT INTO employee_skill_bindings "
                "(platform_id, tenant_id, employee_id, employee_version, skill_id, skill_version, sequence_no, input_mapping_json, output_policy_json) "
                "SELECT platform_id, tenant_id, employee_id, %s, skill_id, skill_version, sequence_no, input_mapping_json, output_policy_json "
                "FROM employee_skill_bindings WHERE platform_id=%s AND tenant_id=%s AND employee_id=%s AND employee_version=%s",
                (next_version, identity.platform_id, identity.tenant_id, employee_id, source_version),
            )
            cursor.execute(
                "INSERT INTO employee_tool_bindings "
                "(platform_id, tenant_id, employee_id, employee_version, tool_id, tool_version, sequence_no, config_json) "
                "SELECT platform_id, tenant_id, employee_id, %s, tool_id, tool_version, sequence_no, config_json "
                "FROM employee_tool_bindings WHERE platform_id=%s AND tenant_id=%s AND employee_id=%s AND employee_version=%s",
                (next_version, identity.platform_id, identity.tenant_id, employee_id, source_version),
            )
            cursor.execute(
                "SELECT slot, module_id, module_version, config_revision, resource_ref, config_json "
                "FROM capability_bindings WHERE platform_id=%s AND tenant_id=%s AND employee_id=%s AND employee_version=%s",
                (identity.platform_id, identity.tenant_id, employee_id, source_version),
            )
            capability_rows = cursor.fetchall()
            for row in capability_rows:
                cursor.execute(
                    "INSERT INTO capability_bindings "
                    "(platform_id, tenant_id, binding_id, employee_id, employee_version, slot, module_id, module_version, config_revision, resource_ref, config_json) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        identity.platform_id,
                        identity.tenant_id,
                        generate_database_id(),
                        employee_id,
                        next_version,
                        row[0],
                        row[1],
                        row[2],
                        row[3],
                        row[4],
                        row[5],
                    ),
                )
            return self._read_employee_version(cursor, identity, employee_id, next_version)

    def _lock_employee_status(
        self, cursor: Any, identity: IdentityContext, employee_id: str
    ) -> LifecycleStatus:
        cursor.execute(
            "SELECT status FROM digital_employees WHERE platform_id=%s AND tenant_id=%s "
            "AND employee_id=%s FOR UPDATE",
            _identity_key(identity, employee_id),
        )
        row = cursor.fetchone()
        if row is None:
            raise self._not_found("digital_employee")
        return LifecycleStatus(str(row[0]))

    def deprecate_employee_version(
        self, identity: IdentityContext, employee_id: str, version: int
    ) -> DigitalEmployeeVersion:
        """锁定员工及其固定版本，在同一事务内下线并写入审计。"""

        with self._transaction() as (_, cursor):
            # 和分叉使用相同的加锁顺序；在事务内重新确认归档状态。
            if self._lock_employee_status(cursor, identity, employee_id) is LifecycleStatus.ARCHIVED:
                raise DomainError("INVALID_STATE", "已归档员工不能下线版本")
            current = self._read_employee_version(cursor, identity, employee_id, version, lock=True)
            if current.status is LifecycleStatus.DEPRECATED:
                return current
            deprecated = current.deprecate()
            # 只改状态，保持已发布快照、资源绑定和历史部署原样。
            cursor.execute(
                "UPDATE digital_employee_versions SET status=%s WHERE platform_id=%s AND tenant_id=%s "
                "AND employee_id=%s AND version=%s",
                (deprecated.status.value, *_identity_key(identity, employee_id), version),
            )
            cursor.execute(
                "INSERT INTO audit_events "
                "(platform_id, tenant_id, event_id, actor_type, actor_user_id, event_type, policy_version, "
                "resource_type, resource_id, payload_json) "
                "VALUES (%s,%s,%s,'USER',%s,'EMPLOYEE_VERSION_DEPRECATED','1','digital_employee_version',%s,%s)",
                (
                    identity.platform_id,
                    identity.tenant_id,
                    generate_database_id(),
                    identity.user_id,
                    employee_id,
                    _json_dump({"employee_id": employee_id, "version": version}),
                ),
            )
            return deprecated

    def replace_employee_resource_bindings(
        self,
        identity: IdentityContext,
        employee_id: str,
        version: int,
        slot: str,
        refs: tuple[tuple[str, int], ...],
    ) -> None:
        """仅在同一事务内替换指定资源槽位，保留另一槽位及员工配置列。"""

        if slot == "skill":
            binding_table = "employee_skill_bindings"
        elif slot == "tool":
            binding_table = "employee_tool_bindings"
        else:
            raise ValueError("slot must be skill or tool")
        with self._transaction() as (_, cursor):
            cursor.execute(
                "SELECT status FROM digital_employee_versions WHERE platform_id=%s AND tenant_id=%s "
                "AND employee_id=%s AND version=%s FOR UPDATE",
                (identity.platform_id, identity.tenant_id, employee_id, version),
            )
            row = cursor.fetchone()
            if row is None:
                raise self._not_found("digital_employee_version")
            if str(row[0]) != LifecycleStatus.DRAFT.value:
                raise DomainError("IMMUTABLE_VERSION", "已发布员工版本不可修改")
            cursor.execute(
                f"DELETE FROM {binding_table} WHERE platform_id=%s AND tenant_id=%s AND employee_id=%s AND employee_version=%s",
                (identity.platform_id, identity.tenant_id, employee_id, version),
            )
            cursor.execute(
                "DELETE FROM capability_bindings WHERE platform_id=%s AND tenant_id=%s "
                "AND employee_id=%s AND employee_version=%s AND slot=%s",
                (identity.platform_id, identity.tenant_id, employee_id, version, slot),
            )
            for sequence, (resource_id, resource_version) in enumerate(refs, start=1):
                if slot == "skill":
                    cursor.execute(
                        "INSERT INTO employee_skill_bindings "
                        "(platform_id, tenant_id, employee_id, employee_version, skill_id, skill_version, sequence_no, input_mapping_json, output_policy_json) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (
                            identity.platform_id,
                            identity.tenant_id,
                            employee_id,
                            version,
                            resource_id,
                            resource_version,
                            sequence,
                            _json_dump({}),
                            _json_dump({}),
                        ),
                    )
                else:
                    cursor.execute(
                        "INSERT INTO employee_tool_bindings "
                        "(platform_id, tenant_id, employee_id, employee_version, tool_id, tool_version, sequence_no, config_json) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                        (
                            identity.platform_id,
                            identity.tenant_id,
                            employee_id,
                            version,
                            resource_id,
                            resource_version,
                            sequence,
                            _json_dump({}),
                        ),
                    )
                cursor.execute(
                    "INSERT INTO capability_bindings "
                    "(platform_id, tenant_id, binding_id, employee_id, employee_version, slot, module_id, module_version, config_revision, resource_ref, config_json) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,1,%s,%s)",
                    (
                        identity.platform_id,
                        identity.tenant_id,
                        generate_database_id(),
                        employee_id,
                        version,
                        slot,
                        resource_id,
                        resource_version,
                        f"{slot}:{resource_id}:v{resource_version}",
                        _json_dump({}),
                    ),
                )

    def _load_profile(self, row: tuple[Any, ...]) -> ExecutionProfile:
        approval = _json_load(row[12], {"name": "none"})
        context = _json_load(row[11], {"name": "default"})
        tools = _json_load(row[10], [])
        return ExecutionProfile(
            profile_id=str(row[0]),
            tenant_id=str(row[1]),
            engine_name=str(row[2]),
            model_alias=str(row[3]),
            tool_policy=tuple(tools),
            context_policy=str(context.get("name", "default")),
            version=int(row[4]),
            status=LifecycleStatus(str(row[13])),
            model_route_id=str(row[5]) if row[5] is not None else None,
            workspace_profile_id=str(row[6]) if row[6] is not None else None,
            workspace_profile_version=int(row[7]) if row[7] is not None else None,
            sandbox_profile_id=str(row[8]) if row[8] is not None else None,
            sandbox_profile_version=int(row[9]) if row[9] is not None else None,
            approval_policy=str(approval.get("name", "none")),
        )

    @staticmethod
    def _profile_select() -> str:
        return (
            "SELECT profile_id, tenant_id, engine_name, model_alias, version, model_route_id, "
            "workspace_profile_id, workspace_profile_version, sandbox_profile_id, sandbox_profile_version, "
            "tool_policy_json, context_policy_json, "
            "approval_policy_json, status FROM execution_profiles"
        )

    def save_profile(self, identity: IdentityContext, value: ExecutionProfile) -> None:
        with self._transaction() as (_, cursor):
            cursor.execute(
                "SELECT status FROM execution_profiles WHERE platform_id=%s AND tenant_id=%s AND profile_id=%s AND version=%s FOR UPDATE",
                (identity.platform_id, identity.tenant_id, value.profile_id, value.version),
            )
            current = cursor.fetchone()
            if current is not None:
                cursor.execute(
                    self._profile_select()
                    + " WHERE platform_id=%s AND tenant_id=%s AND profile_id=%s AND version=%s",
                    (identity.platform_id, identity.tenant_id, value.profile_id, value.version),
                )
                existing = self._load_profile(cursor.fetchone())
                if MySqlCatalogRepository._assert_version_mutable(
                    str(current[0]), existing, value, "ExecutionProfile"
                ):
                    return
            cursor.execute(
                "INSERT INTO execution_profiles "
                "(platform_id, tenant_id, profile_id, version, engine_name, model_alias, model_route_id, "
                "workspace_profile_id, workspace_profile_version, sandbox_profile_id, sandbox_profile_version, "
                "tool_policy_json, approval_policy_json, context_policy_json, status) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE engine_name=VALUES(engine_name), model_alias=VALUES(model_alias), "
                "model_route_id=VALUES(model_route_id), workspace_profile_id=VALUES(workspace_profile_id), "
                "workspace_profile_version=VALUES(workspace_profile_version), sandbox_profile_id=VALUES(sandbox_profile_id), "
                "sandbox_profile_version=VALUES(sandbox_profile_version), tool_policy_json=VALUES(tool_policy_json), "
                "approval_policy_json=VALUES(approval_policy_json), context_policy_json=VALUES(context_policy_json), "
                "status=VALUES(status)",
                (
                    identity.platform_id,
                    identity.tenant_id,
                    value.profile_id,
                    value.version,
                    value.engine_name,
                    value.model_alias,
                    value.model_route_id,
                    value.workspace_profile_id,
                    value.workspace_profile_version,
                    value.sandbox_profile_id,
                    value.sandbox_profile_version,
                    _json_dump(list(value.tool_policy)),
                    _json_dump({"name": value.approval_policy}),
                    _json_dump({"name": value.context_policy}),
                    value.status.value,
                ),
            )

    def get_profile_version(self, identity: IdentityContext, profile_id: str, version: int) -> ExecutionProfile:
        with self._reader() as (_, cursor):
            cursor.execute(
                self._profile_select()
                + " WHERE platform_id=%s AND tenant_id=%s AND profile_id=%s AND version=%s",
                (identity.platform_id, identity.tenant_id, profile_id, version),
            )
            row = cursor.fetchone()
        if row is None:
            raise self._not_found("execution_profile")
        return self._load_profile(row)

    def get_profile(self, identity: IdentityContext, profile_id: str) -> ExecutionProfile:
        with self._reader() as (_, cursor):
            cursor.execute(
                self._profile_select()
                + " WHERE platform_id=%s AND tenant_id=%s AND profile_id=%s ORDER BY version DESC LIMIT 1",
                (identity.platform_id, identity.tenant_id, profile_id),
            )
            row = cursor.fetchone()
        if row is None:
            raise self._not_found("execution_profile")
        return self._load_profile(row)

    def list_profiles(self, identity: IdentityContext) -> tuple[ExecutionProfile, ...]:
        with self._reader() as (_, cursor):
            cursor.execute(
                self._profile_select()
                + " WHERE platform_id=%s AND tenant_id=%s ORDER BY profile_id, version DESC",
                (identity.platform_id, identity.tenant_id),
            )
            rows = cursor.fetchall()
        latest: dict[str, ExecutionProfile] = {}
        for row in rows:
            latest.setdefault(str(row[0]), self._load_profile(row))
        return tuple(latest.values())

    @staticmethod
    def _model_connection_from_row(row: tuple[Any, ...]) -> ModelConnection:
        secret = SecretRef(str(row[6]), str(row[7]), str(row[8])) if row[6] is not None else None
        return ModelConnection(
            connection_id=str(row[0]),
            tenant_id=str(row[1]),
            name=str(row[2]),
            provider_kind=str(row[3]),
            protocol=str(row[4]),
            endpoint=str(row[5]),
            secret_ref=secret,
            catalog_mode=str(row[9]),
            status=str(row[10]),
            version=int(row[11]),
        )

    @staticmethod
    def _model_catalog_item_from_row(row: tuple[Any, ...]) -> ModelCatalogItem:
        return ModelCatalogItem(
            connection_id=str(row[0]),
            tenant_id=str(row[1]),
            model_id=str(row[2]),
            display_name=str(row[3]),
            capabilities=_json_load(row[4], {}),
            context_window=int(row[5]) if row[5] is not None else None,
            catalog_source=str(row[6]),
            status=str(row[7]),
            last_seen_at=row[8],
        )

    def save_model_connection(self, identity: IdentityContext, value: ModelConnection) -> None:
        if value.tenant_id != identity.tenant_id:
            raise DomainError("AUTHORIZATION_DENIED", "模型连接不属于当前租户")
        secret = value.secret_ref
        with self._transaction() as (_, cursor):
            cursor.execute(
                "INSERT INTO model_connections "
                "(platform_id,tenant_id,connection_id,name,provider_kind,protocol,endpoint,"
                "auth_secret_ref,auth_secret_scope,auth_secret_provider,catalog_mode,status,version) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE name=VALUES(name),provider_kind=VALUES(provider_kind),"
                "protocol=VALUES(protocol),endpoint=VALUES(endpoint),auth_secret_ref=VALUES(auth_secret_ref),"
                "auth_secret_scope=VALUES(auth_secret_scope),auth_secret_provider=VALUES(auth_secret_provider),"
                "catalog_mode=VALUES(catalog_mode),status=VALUES(status),version=VALUES(version)",
                (
                    identity.platform_id,
                    identity.tenant_id,
                    value.connection_id,
                    value.name,
                    value.provider_kind,
                    value.protocol,
                    value.endpoint,
                    secret.secret_id if secret else None,
                    secret.scope if secret else None,
                    secret.provider if secret else None,
                    value.catalog_mode.value,
                    value.status.value,
                    value.version,
                ),
            )

    def get_model_connection(self, identity: IdentityContext, connection_id: str) -> ModelConnection:
        with self._reader() as (_, cursor):
            cursor.execute(
                "SELECT connection_id,tenant_id,name,provider_kind,protocol,endpoint,auth_secret_ref,"
                "auth_secret_scope,auth_secret_provider,catalog_mode,status,version "
                "FROM model_connections WHERE platform_id=%s AND tenant_id=%s AND connection_id=%s",
                (identity.platform_id, identity.tenant_id, connection_id),
            )
            row = cursor.fetchone()
        if row is None:
            raise self._not_found("model_connection")
        return self._model_connection_from_row(row)

    def list_model_connections(self, identity: IdentityContext) -> tuple[ModelConnection, ...]:
        with self._reader() as (_, cursor):
            cursor.execute(
                "SELECT connection_id,tenant_id,name,provider_kind,protocol,endpoint,auth_secret_ref,"
                "auth_secret_scope,auth_secret_provider,catalog_mode,status,version "
                "FROM model_connections WHERE platform_id=%s AND tenant_id=%s ORDER BY name,connection_id",
                (identity.platform_id, identity.tenant_id),
            )
            rows = cursor.fetchall()
        return tuple(self._model_connection_from_row(row) for row in rows)

    def save_model_catalog_item(self, identity: IdentityContext, value: ModelCatalogItem) -> None:
        if value.tenant_id != identity.tenant_id:
            raise DomainError("AUTHORIZATION_DENIED", "模型目录项不属于当前租户")
        with self._transaction() as (_, cursor):
            cursor.execute(
                "SELECT 1 FROM model_connections WHERE platform_id=%s AND tenant_id=%s AND connection_id=%s",
                (identity.platform_id, identity.tenant_id, value.connection_id),
            )
            if cursor.fetchone() is None:
                raise self._not_found("model_connection")
            cursor.execute(
                "INSERT INTO model_catalog_items "
                "(platform_id,tenant_id,connection_id,model_id,display_name,capabilities_json,"
                "context_window,catalog_source,status,last_seen_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE display_name=VALUES(display_name),"
                "capabilities_json=VALUES(capabilities_json),context_window=VALUES(context_window),"
                "catalog_source=VALUES(catalog_source),status=VALUES(status),last_seen_at=VALUES(last_seen_at)",
                (
                    identity.platform_id,
                    identity.tenant_id,
                    value.connection_id,
                    value.model_id,
                    value.display_name,
                    _json_dump(dict(value.capabilities)),
                    value.context_window,
                    value.catalog_source.value,
                    value.status.value,
                    value.last_seen_at,
                ),
            )

    def get_model_catalog_item(
        self, identity: IdentityContext, connection_id: str, model_id: str
    ) -> ModelCatalogItem:
        with self._reader() as (_, cursor):
            cursor.execute(
                "SELECT connection_id,tenant_id,model_id,display_name,capabilities_json,"
                "context_window,catalog_source,status,last_seen_at FROM model_catalog_items "
                "WHERE platform_id=%s AND tenant_id=%s AND connection_id=%s AND model_id=%s",
                (identity.platform_id, identity.tenant_id, connection_id, model_id),
            )
            row = cursor.fetchone()
        if row is None:
            raise self._not_found("model_catalog_item")
        return self._model_catalog_item_from_row(row)

    def list_model_catalog_items(
        self, identity: IdentityContext, connection_id: str
    ) -> tuple[ModelCatalogItem, ...]:
        self.get_model_connection(identity, connection_id)
        with self._reader() as (_, cursor):
            cursor.execute(
                "SELECT connection_id,tenant_id,model_id,display_name,capabilities_json,"
                "context_window,catalog_source,status,last_seen_at FROM model_catalog_items "
                "WHERE platform_id=%s AND tenant_id=%s AND connection_id=%s ORDER BY model_id",
                (identity.platform_id, identity.tenant_id, connection_id),
            )
            rows = cursor.fetchall()
        return tuple(self._model_catalog_item_from_row(row) for row in rows)

    @staticmethod
    def _model_test_record_from_row(row: tuple[Any, ...]) -> ModelTestRecord:
        return ModelTestRecord(
            test_id=str(row[0]),
            tenant_id=str(row[1]),
            connection_id=str(row[2]),
            route_id=str(row[3]) if row[3] is not None else None,
            model_id=str(row[4]) if row[4] is not None else None,
            test_kind=str(row[5]),
            state=str(row[6]),
            snapshot=_json_load(row[7], {}),
            created_by_user_id=str(row[8]),
            worker_id=str(row[9]) if row[9] is not None else None,
            fencing_token=int(row[10]),
            lease_expires_at=row[11],
            started_at=row[12],
            finished_at=row[13],
            latency_ms=int(row[14]) if row[14] is not None else None,
            provider_request_id=str(row[15]) if row[15] is not None else None,
            input_tokens=int(row[16]) if row[16] is not None else None,
            output_tokens=int(row[17]) if row[17] is not None else None,
            total_tokens=int(row[18]) if row[18] is not None else None,
            usage_status=str(row[19]) if row[19] is not None else None,
            error_category=str(row[20]) if row[20] is not None else None,
            created_at=row[21],
            updated_at=row[22],
        )

    def save_model_test_record(self, identity: IdentityContext, value: ModelTestRecord) -> None:
        if value.tenant_id != identity.tenant_id:
            raise DomainError("AUTHORIZATION_DENIED", "模型测试记录不属于当前租户")
        with self._transaction() as (_, cursor):
            cursor.execute(
                "INSERT INTO model_test_records "
                "(platform_id,tenant_id,test_id,connection_id,route_id,model_id,test_kind,state,"
                "snapshot_json,created_by_user_id,worker_id,fencing_token,lease_expires_at,started_at,"
                "finished_at,latency_ms,provider_request_id,input_tokens,output_tokens,total_tokens,"
                "usage_status,error_category) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    identity.platform_id,
                    identity.tenant_id,
                    value.test_id,
                    value.connection_id,
                    value.route_id,
                    value.model_id,
                    value.test_kind.value,
                    value.state.value,
                    _json_dump(dict(value.snapshot)),
                    value.created_by_user_id,
                    value.worker_id,
                    value.fencing_token,
                    value.lease_expires_at,
                    value.started_at,
                    value.finished_at,
                    value.latency_ms,
                    value.provider_request_id,
                    value.input_tokens,
                    value.output_tokens,
                    value.total_tokens,
                    value.usage_status.value if value.usage_status else None,
                    value.error_category,
                ),
            )

    def claim_next_model_test(
        self, platform_id: str, worker_id: str, *, lease_seconds: int
    ) -> ModelTestRecord | None:
        if not platform_id.strip() or not worker_id.strip() or lease_seconds < 1:
            raise ValueError("model test claim requires platform, worker and positive lease")
        columns = (
            "test_id,tenant_id,connection_id,route_id,model_id,test_kind,state,snapshot_json,"
            "created_by_user_id,worker_id,fencing_token,lease_expires_at,started_at,finished_at,"
            "latency_ms,provider_request_id,input_tokens,output_tokens,total_tokens,usage_status,"
            "error_category,created_at,updated_at"
        )
        with self._transaction() as (_, cursor):
            # 过期 RUNNING 任务结果不确定，先终结并围栏旧执行，绝不重新放回队列。
            cursor.execute(
                "UPDATE model_test_records SET state='UNKNOWN',"
                "error_category='WORKER_LEASE_EXPIRED',finished_at=UTC_TIMESTAMP(6),"
                "lease_expires_at=NULL,fencing_token=fencing_token+1,updated_at=UTC_TIMESTAMP(6) "
                "WHERE platform_id=%s AND state='RUNNING' AND lease_expires_at<=UTC_TIMESTAMP(6)",
                (platform_id,),
            )
            cursor.execute(
                "SELECT test_id,tenant_id FROM model_test_records "
                "WHERE platform_id=%s AND state='QUEUED' "
                "ORDER BY created_at,test_id LIMIT 1 FOR UPDATE SKIP LOCKED",
                (platform_id,),
            )
            selected = cursor.fetchone()
            if selected is None:
                return None
            test_id, tenant_id = str(selected[0]), str(selected[1])
            cursor.execute(
                "UPDATE model_test_records SET state='RUNNING',worker_id=%s,"
                "fencing_token=fencing_token+1,lease_expires_at=DATE_ADD(UTC_TIMESTAMP(6),INTERVAL %s SECOND),"
                "started_at=UTC_TIMESTAMP(6),updated_at=UTC_TIMESTAMP(6) "
                "WHERE platform_id=%s AND tenant_id=%s AND test_id=%s AND state='QUEUED'",
                (worker_id, lease_seconds, platform_id, tenant_id, test_id),
            )
            if cursor.rowcount != 1:
                return None
            cursor.execute(
                f"SELECT {columns} FROM model_test_records "
                "WHERE platform_id=%s AND tenant_id=%s AND test_id=%s",
                (platform_id, tenant_id, test_id),
            )
            row = cursor.fetchone()
        return self._model_test_record_from_row(row) if row is not None else None

    def heartbeat_model_test(
        self,
        identity: IdentityContext,
        test_id: str,
        worker_id: str,
        fencing_token: int,
        *,
        lease_seconds: int,
    ) -> bool:
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        with self._transaction() as (_, cursor):
            cursor.execute(
                "UPDATE model_test_records SET "
                "lease_expires_at=DATE_ADD(UTC_TIMESTAMP(6),INTERVAL %s SECOND),"
                "updated_at=UTC_TIMESTAMP(6) WHERE platform_id=%s AND tenant_id=%s AND test_id=%s "
                "AND state='RUNNING' AND worker_id=%s AND fencing_token=%s "
                "AND lease_expires_at>UTC_TIMESTAMP(6)",
                (
                    lease_seconds,
                    identity.platform_id,
                    identity.tenant_id,
                    test_id,
                    worker_id,
                    fencing_token,
                ),
            )
            return cursor.rowcount == 1

    def finish_model_test(
        self,
        identity: IdentityContext,
        claimed: ModelTestRecord,
        result: ModelTestResult,
    ) -> bool:
        if claimed.tenant_id != identity.tenant_id or claimed.worker_id is None:
            raise DomainError("AUTHORIZATION_DENIED", "模型测试租约不属于当前 Worker 租户")
        with self._transaction() as (_, cursor):
            cursor.execute(
                "UPDATE model_test_records SET state=%s,lease_expires_at=NULL,"
                "finished_at=UTC_TIMESTAMP(6),latency_ms=%s,provider_request_id=%s,"
                "input_tokens=%s,output_tokens=%s,total_tokens=%s,usage_status=%s,error_category=%s,"
                "updated_at=UTC_TIMESTAMP(6) WHERE platform_id=%s AND tenant_id=%s AND test_id=%s "
                "AND state='RUNNING' AND worker_id=%s AND fencing_token=%s "
                "AND lease_expires_at>UTC_TIMESTAMP(6)",
                (
                    result.state.value,
                    result.latency_ms,
                    result.provider_request_id,
                    result.input_tokens,
                    result.output_tokens,
                    result.total_tokens,
                    result.usage_status.value if result.usage_status else None,
                    result.error_category,
                    identity.platform_id,
                    identity.tenant_id,
                    claimed.test_id,
                    claimed.worker_id,
                    claimed.fencing_token,
                ),
            )
            return cursor.rowcount == 1

    def get_model_test_record(self, identity: IdentityContext, test_id: str) -> ModelTestRecord:
        with self._reader() as (_, cursor):
            cursor.execute(
                "SELECT test_id,tenant_id,connection_id,route_id,model_id,test_kind,state,snapshot_json,"
                "created_by_user_id,worker_id,fencing_token,lease_expires_at,started_at,finished_at,"
                "latency_ms,provider_request_id,input_tokens,output_tokens,total_tokens,usage_status,"
                "error_category,created_at,updated_at FROM model_test_records "
                "WHERE platform_id=%s AND tenant_id=%s AND test_id=%s",
                (identity.platform_id, identity.tenant_id, test_id),
            )
            row = cursor.fetchone()
        if row is None:
            raise self._not_found("model_test")
        return self._model_test_record_from_row(row)

    def save_model_route(self, identity: IdentityContext, value: ModelRoute) -> None:
        profile = value.profile
        secret = profile.secret_ref
        if value.tenant_id != identity.tenant_id or profile.tenant_id != identity.tenant_id:
            raise DomainError("AUTHORIZATION_DENIED", "模型路由不属于当前租户")
        with self._transaction() as (_, cursor):
            if profile.connection_id is None:
                cursor.execute(
                    "SELECT connection_id FROM model_connections WHERE platform_id=%s AND tenant_id=%s "
                    "AND provider_kind=%s AND protocol=%s AND endpoint=%s "
                    "AND auth_secret_ref <=> %s AND auth_secret_scope <=> %s "
                    "AND auth_secret_provider <=> %s AND status='ACTIVE' ORDER BY connection_id LIMIT 1",
                    (
                        identity.platform_id,
                        identity.tenant_id,
                        profile.provider,
                        profile.protocol,
                        profile.endpoint,
                        secret.secret_id if secret else None,
                        secret.scope if secret else None,
                        secret.provider if secret else None,
                    ),
                )
                existing_connection = cursor.fetchone()
                connection_id = (
                    str(existing_connection[0])
                    if existing_connection is not None
                    else legacy_model_connection_id(
                        identity.platform_id, identity.tenant_id, profile
                    )
                )
                if existing_connection is None:
                    cursor.execute(
                        "INSERT INTO model_connections "
                        "(platform_id,tenant_id,connection_id,name,provider_kind,protocol,endpoint,"
                        "auth_secret_ref,auth_secret_scope,auth_secret_provider,catalog_mode,status,version) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'MANUAL','ACTIVE',1)",
                        (
                            identity.platform_id,
                            identity.tenant_id,
                            connection_id,
                            f"{profile.provider}-{connection_id[-8:]}",
                            profile.provider,
                            profile.protocol,
                            profile.endpoint,
                            secret.secret_id if secret else None,
                            secret.scope if secret else None,
                            secret.provider if secret else None,
                        ),
                    )
                model_id = profile.model_name
                cursor.execute(
                    "INSERT INTO model_catalog_items "
                    "(platform_id,tenant_id,connection_id,model_id,display_name,capabilities_json,"
                    "catalog_source,status) VALUES (%s,%s,%s,%s,%s,JSON_OBJECT(),'MANUAL','ACTIVE') "
                    "ON DUPLICATE KEY UPDATE display_name=VALUES(display_name),status='ACTIVE'",
                    (
                        identity.platform_id,
                        identity.tenant_id,
                        connection_id,
                        model_id,
                        model_id,
                    ),
                )
            else:
                connection_id = profile.connection_id
                model_id = profile.catalog_model_id or ""
                cursor.execute(
                    "SELECT provider_kind,protocol,endpoint,auth_secret_ref,auth_secret_scope,"
                    "auth_secret_provider,status FROM model_connections WHERE platform_id=%s "
                    "AND tenant_id=%s AND connection_id=%s",
                    (identity.platform_id, identity.tenant_id, connection_id),
                )
                connection_row = cursor.fetchone()
                if connection_row is None:
                    raise self._not_found("model_connection")
                if (
                    str(connection_row[0]) != profile.provider
                    or str(connection_row[1]) != profile.protocol
                    or str(connection_row[2]) != profile.endpoint
                    or connection_row[3] != (secret.secret_id if secret else None)
                    or connection_row[4] != (secret.scope if secret else None)
                    or connection_row[5] != (secret.provider if secret else None)
                ):
                    raise DomainError("INVALID_ARGUMENT", "ModelProfile 与 ModelConnection 配置不一致")
                if str(connection_row[6]) != "ACTIVE":
                    raise DomainError("DEPENDENCY_VALIDATION_ERROR", "模型连接已停用")
                cursor.execute(
                    "SELECT status FROM model_catalog_items WHERE platform_id=%s AND tenant_id=%s "
                    "AND connection_id=%s AND model_id=%s",
                    (identity.platform_id, identity.tenant_id, connection_id, model_id),
                )
                catalog_row = cursor.fetchone()
                if catalog_row is None:
                    raise self._not_found("model_catalog_item")
                if str(catalog_row[0]) != "ACTIVE":
                    raise DomainError("DEPENDENCY_VALIDATION_ERROR", "模型目录项已停用")
            cursor.execute(
                "INSERT INTO model_routes "
                "(platform_id, tenant_id, route_id, alias, provider, model_name, endpoint, protocol, "
                "auth_secret_ref, auth_secret_scope, auth_secret_provider, pricing_snapshot_ref, parameters_json, status, "
                "model_connection_id, catalog_model_id, route_version) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE alias=VALUES(alias), provider=VALUES(provider), model_name=VALUES(model_name), "
                "endpoint=VALUES(endpoint), protocol=VALUES(protocol), auth_secret_ref=VALUES(auth_secret_ref), "
                "auth_secret_scope=VALUES(auth_secret_scope), auth_secret_provider=VALUES(auth_secret_provider), "
                "pricing_snapshot_ref=VALUES(pricing_snapshot_ref), parameters_json=VALUES(parameters_json), "
                "status=VALUES(status), model_connection_id=VALUES(model_connection_id), "
                "catalog_model_id=VALUES(catalog_model_id), route_version=VALUES(route_version)",
                (
                    identity.platform_id,
                    identity.tenant_id,
                    value.route_id,
                    value.alias,
                    profile.provider,
                    profile.model_name,
                    profile.endpoint,
                    profile.protocol,
                    secret.secret_id if secret else None,
                    secret.scope if secret else None,
                    secret.provider if secret else None,
                    profile.pricing_snapshot_ref,
                    _json_dump(dict(profile.parameters)),
                    value.status,
                    connection_id,
                    model_id,
                    value.route_version,
                ),
            )

    def get_model_route(self, identity: IdentityContext, route_id: str) -> ModelRoute:
        with self._reader() as (_, cursor):
            cursor.execute(
                "SELECT route_id, tenant_id, alias, provider, model_name, endpoint, protocol, auth_secret_ref, "
                "auth_secret_scope, auth_secret_provider, pricing_snapshot_ref, parameters_json, status, "
                "model_connection_id, catalog_model_id, route_version "
                "FROM model_routes WHERE platform_id=%s AND tenant_id=%s AND route_id=%s",
                (identity.platform_id, identity.tenant_id, route_id),
            )
            row = cursor.fetchone()
        if row is None:
            raise self._not_found("model_route")
        secret = SecretRef(str(row[7]), str(row[8]), str(row[9])) if row[7] is not None else None
        return ModelRoute(
            route_id=str(row[0]),
            tenant_id=str(row[1]),
            alias=str(row[2]),
            profile=ModelProfile(
                profile_id=f"model-profile-{row[0]}",
                tenant_id=str(row[1]),
                model_name=str(row[4]),
                provider=str(row[3]),
                endpoint=str(row[5]),
                protocol=str(row[6]),
                secret_ref=secret,
                pricing_snapshot_ref=str(row[10]) if row[10] is not None else None,
                parameters=_json_load(row[11], {}),
                connection_id=str(row[13]),
                catalog_model_id=str(row[14]),
            ),
            status=str(row[12]),
            route_version=int(row[15]),
        )

    def save(self, identity: IdentityContext, route: ModelRoute) -> None:
        """兼容 ModelRouteRepository 端口的写入名称。"""

        self.save_model_route(identity, route)

    def get(self, identity: IdentityContext, route_id: str) -> ModelRoute | None:
        """兼容 ModelRouteRepository 端口的可选读取语义。"""

        try:
            return self.get_model_route(identity, route_id)
        except DomainError as error:
            if error.code == "RESOURCE_NOT_FOUND":
                return None
            raise

    def list(self, identity: IdentityContext) -> tuple[ModelRoute, ...]:
        with self._reader() as (_, cursor):
            cursor.execute(
                "SELECT route_id FROM model_routes WHERE platform_id=%s AND tenant_id=%s ORDER BY alias",
                (identity.platform_id, identity.tenant_id),
            )
            route_ids = tuple(str(row[0]) for row in cursor.fetchall())
        return tuple(self.get_model_route(identity, route_id) for route_id in route_ids)

    def save_workspace_profile(
        self, identity: IdentityContext, value: WorkspaceProfile, *, version: int = 1
    ) -> None:
        policy = {
            "files": value.files,
            "repository": value.repository,
            "remote_data_tools": value.remote_data_tools,
            "network_policy": value.network_policy,
            "quota": dict(value.quota),
            "persistent": value.persistent,
        }
        with self._transaction() as (_, cursor):
            cursor.execute(
                "INSERT INTO workspace_profiles "
                "(platform_id, tenant_id, workspace_profile_id, version, mode, project_id, policy_json, status) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,'PUBLISHED')",
                (
                    identity.platform_id,
                    identity.tenant_id,
                    value.profile_id,
                    version,
                    WorkspaceMode(value.mode).value,
                    value.project_id,
                    _json_dump(policy),
                ),
            )

    def get_workspace_profile(
        self, identity: IdentityContext, profile_id: str, *, version: int = 1
    ) -> WorkspaceProfile:
        with self._reader() as (_, cursor):
            cursor.execute(
                "SELECT workspace_profile_id, mode, project_id, policy_json "
                "FROM workspace_profiles WHERE platform_id=%s AND tenant_id=%s "
                "AND workspace_profile_id=%s AND version=%s",
                (identity.platform_id, identity.tenant_id, profile_id, version),
            )
            row = cursor.fetchone()
        if row is None:
            raise self._not_found("workspace_profile")
        policy = _json_load(row[3], {})
        return WorkspaceProfile(
            profile_id=str(row[0]),
            mode=WorkspaceMode(str(row[1])),
            project_id=str(row[2]) if row[2] is not None else None,
            files=bool(policy.get("files", False)),
            repository=bool(policy.get("repository", False)),
            remote_data_tools=bool(policy.get("remote_data_tools", False)),
            network_policy=str(policy.get("network_policy", "none")),
            quota=policy.get("quota", {}),
            persistent=bool(policy.get("persistent", False)),
        )

    def save_sandbox_profile(
        self, identity: IdentityContext, value: SandboxProfile, *, version: int = 1
    ) -> None:
        limits = {"cpu_limit": value.cpu_limit, "memory_mb": value.memory_mb}
        security = {
            "allowed_host_paths": list(value.allowed_host_paths),
            "devices": list(value.devices),
            "privileged": value.privileged,
            "long_lived_credentials": value.long_lived_credentials,
        }
        with self._transaction() as (_, cursor):
            cursor.execute(
                "INSERT INTO sandbox_profiles "
                "(platform_id, tenant_id, sandbox_profile_id, version, network_policy, "
                "resource_limits_json, security_policy_json, status) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,'PUBLISHED')",
                (
                    identity.platform_id,
                    identity.tenant_id,
                    value.profile_id,
                    version,
                    value.network_policy,
                    _json_dump(limits),
                    _json_dump(security),
                ),
            )

    def get_sandbox_profile(
        self, identity: IdentityContext, profile_id: str, *, version: int = 1
    ) -> SandboxProfile:
        with self._reader() as (_, cursor):
            cursor.execute(
                "SELECT sandbox_profile_id, network_policy, resource_limits_json, security_policy_json "
                "FROM sandbox_profiles WHERE platform_id=%s AND tenant_id=%s "
                "AND sandbox_profile_id=%s AND version=%s",
                (identity.platform_id, identity.tenant_id, profile_id, version),
            )
            row = cursor.fetchone()
        if row is None:
            raise self._not_found("sandbox_profile")
        limits = _json_load(row[2], {})
        security = _json_load(row[3], {})
        return SandboxProfile(
            profile_id=str(row[0]),
            network_policy=str(row[1]),
            cpu_limit=int(limits.get("cpu_limit", 1)),
            memory_mb=int(limits.get("memory_mb", 512)),
            allowed_host_paths=tuple(security.get("allowed_host_paths", ())),
            devices=tuple(security.get("devices", ())),
            privileged=bool(security.get("privileged", False)),
            long_lived_credentials=bool(security.get("long_lived_credentials", False)),
        )

    def save_deployment(self, identity: IdentityContext, value: Deployment) -> Deployment:
        """锁定目标版本并校验发布状态，回滚只更新版本绑定。"""

        key = _identity_key(identity, value.deployment_id)
        with self._transaction() as (_, cursor):
            # 员工行在部署启停和版本下线中也是第一把锁，后续读取以持久状态为准。
            employee_status = self._lock_employee_status(cursor, identity, value.employee_id)
            target = self._read_employee_version(
                cursor, identity, value.employee_id, value.employee_version.version, lock=True
            )
            previous = self._read_deployment_row(cursor, identity, value.deployment_id)
            if previous is not None:
                # 仅锁现有记录，避免不同员工同时新建部署时争用主键间隙锁。
                previous = self._read_deployment_row(cursor, identity, value.deployment_id, lock=True)
            if employee_status is LifecycleStatus.ARCHIVED:
                message = "已归档员工不能回滚部署" if previous else "已归档员工不能创建新的部署"
                raise DomainError("INVALID_STATE", message)
            if target.status is not LifecycleStatus.PUBLISHED:
                if previous is not None:
                    raise DomainError("DEPENDENCY_VALIDATION_ERROR", "回滚目标必须是已发布版本")
                raise DomainError("INVALID_STATE", "只能部署已发布员工版本")

            if previous is None:
                saved = replace(value, employee_version=target)
                previous_version = None
                cursor.execute(
                    "INSERT INTO deployments "
                    "(platform_id, tenant_id, deployment_id, employee_id, employee_version, status, created_by) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (*key, saved.employee_id, target.version, saved.status.value, identity.user_id),
                )
            else:
                employee_id = str(previous[0])
                previous_version = int(previous[1])
                status = LifecycleStatus(str(previous[2]))
                # 用行锁读到的状态返回结果，避免过期快照撤销刚发生的启停操作。
                saved = Deployment(value.deployment_id, identity.tenant_id, employee_id, target, status)
                if previous_version != target.version:
                    cursor.execute(
                        "UPDATE deployments SET employee_version=%s WHERE platform_id=%s AND tenant_id=%s "
                        "AND deployment_id=%s",
                        (target.version, *key),
                    )

            if previous_version != target.version:
                self._record_deployment_event(
                    cursor,
                    identity,
                    saved,
                    "DEPLOYMENT_CREATED" if previous is None else "DEPLOYMENT_ROLLED_BACK",
                    {"previous_version": previous_version},
                )
            return saved

    def transition_deployment(
        self, identity: IdentityContext, deployment_id: str, target_status: LifecycleStatus
    ) -> Deployment:
        """原子更新当前租户内的部署状态和审计，重新上线时锁定固定版本。"""

        # 员工归属在部署创建后保持固定，先找出员工以遵循统一的加锁顺序。
        with self._reader() as (_, cursor):
            row = self._read_deployment_row(cursor, identity, deployment_id)
        if row is None:
            raise self._not_found("deployment")
        employee_id = str(row[0])

        key = _identity_key(identity, deployment_id)
        with self._transaction() as (_, cursor):
            employee_status = self._lock_employee_status(cursor, identity, employee_id)
            row = self._read_deployment_row(cursor, identity, deployment_id, lock=True)
            if row is None:
                raise self._not_found("deployment")
            # 回滚可能已更换固定版本，使用锁定的部署行读取最新绑定。
            current = self._load_deployment(cursor, identity, deployment_id, row, lock_version=True)
            if target_status is LifecycleStatus.DISABLED:
                updated = current.disable()
                event_type = "DEPLOYMENT_DISABLED"
            elif target_status is LifecycleStatus.ACTIVE:
                if employee_status is LifecycleStatus.ARCHIVED:
                    raise DomainError("INVALID_STATE", "已归档员工不能重新上线部署")
                updated = current.enable()
                event_type = "DEPLOYMENT_ENABLED"
            else:
                raise DomainError("INVALID_STATE", "部署只能下线或重新上线")

            cursor.execute(
                "UPDATE deployments SET status=%s WHERE platform_id=%s AND tenant_id=%s AND deployment_id=%s",
                (updated.status.value, *key),
            )
            self._record_deployment_event(
                cursor,
                identity,
                updated,
                event_type,
                {"previous_status": current.status.value, "status": updated.status.value},
            )
            return updated

    @staticmethod
    def _record_deployment_event(
        cursor: Any, identity: IdentityContext, value: Deployment, event_type: str, changes: dict[str, object]
    ) -> None:
        cursor.execute(
            "INSERT INTO audit_events "
            "(platform_id, tenant_id, event_id, actor_type, actor_user_id, event_type, policy_version, "
            "resource_type, resource_id, payload_json) VALUES (%s,%s,%s,'USER',%s,%s,'1','deployment',%s,%s)",
            (
                identity.platform_id,
                identity.tenant_id,
                generate_database_id(),
                identity.user_id,
                event_type,
                value.deployment_id,
                _json_dump({"employee_id": value.employee_id, "version": value.employee_version.version, **changes}),
            ),
        )

    @staticmethod
    def _read_deployment_row(
        cursor: Any, identity: IdentityContext, deployment_id: str, *, lock: bool = False
    ) -> tuple[Any, ...] | None:
        lock_clause = " FOR UPDATE" if lock else ""
        cursor.execute(
            "SELECT employee_id, employee_version, status FROM deployments "
            "WHERE platform_id=%s AND tenant_id=%s AND deployment_id=%s" + lock_clause,
            _identity_key(identity, deployment_id),
        )
        return cursor.fetchone()

    def _load_deployment(
        self,
        cursor: Any,
        identity: IdentityContext,
        deployment_id: str,
        row: tuple[Any, ...],
        *,
        lock_version: bool = False,
    ) -> Deployment:
        employee_id = str(row[0])
        version = self._read_employee_version(cursor, identity, employee_id, int(row[1]), lock=lock_version)
        return Deployment(deployment_id, identity.tenant_id, employee_id, version, LifecycleStatus(str(row[2])))

    def get_deployment(self, identity: IdentityContext, deployment_id: str) -> Deployment:
        """读取部署及其固定版本，供运行准入和历史状态查询使用。"""

        with self._reader() as (_, cursor):
            row = self._read_deployment_row(cursor, identity, deployment_id)
            if row is None:
                raise self._not_found("deployment")
            return self._load_deployment(cursor, identity, deployment_id, row)

    def list_deployments(self, identity: IdentityContext) -> tuple[Deployment, ...]:
        with self._reader() as (_, cursor):
            cursor.execute(
                "SELECT deployment_id FROM deployments WHERE platform_id=%s AND tenant_id=%s ORDER BY created_at, deployment_id",
                (identity.platform_id, identity.tenant_id),
            )
            ids = tuple(str(row[0]) for row in cursor.fetchall())
        return tuple(self.get_deployment(identity, deployment_id) for deployment_id in ids)

    def list_audit_events(
        self,
        identity: IdentityContext,
        *,
        resource_type: str | None = None,
        resource_id: str | None = None,
        event_type: str | None = None,
        limit: int = 100,
        before_created_at: datetime | None = None,
        before_event_id: str | None = None,
    ) -> tuple[AuditEventRecord, ...]:
        if not 1 <= limit <= 201:
            raise DomainError("INVALID_ARGUMENT", "审计查询 limit 必须在 1 到 201 之间")
        conditions = ["platform_id=%s", "tenant_id=%s"]
        parameters: list[object] = [identity.platform_id, identity.tenant_id]
        for column, value in (
            ("resource_type", resource_type),
            ("resource_id", resource_id),
            ("event_type", event_type),
        ):
            if value is not None:
                conditions.append(f"{column}=%s")
                parameters.append(value)
        if before_created_at is not None and before_event_id is not None:
            conditions.append("(created_at<%s OR (created_at=%s AND event_id<%s))")
            before_db = before_created_at.astimezone(UTC).replace(tzinfo=None)
            parameters.extend((before_db, before_db, before_event_id))
        parameters.append(limit)
        with self._reader() as (_, cursor):
            cursor.execute(
                "SELECT event_id, platform_id, tenant_id, actor_type, actor_user_id, event_type, "
                "resource_type, resource_id, payload_json, created_at FROM audit_events WHERE "
                + " AND ".join(conditions)
                + " ORDER BY created_at DESC, event_id DESC LIMIT %s",
                tuple(parameters),
            )
            rows = cursor.fetchall()
        return tuple(
            AuditEventRecord(
                event_id=str(row[0]),
                platform_id=str(row[1]),
                tenant_id=str(row[2]),
                actor_type=str(row[3]),
                actor_user_id=str(row[4]) if row[4] is not None else None,
                event_type=str(row[5]),
                resource_type=str(row[6]) if row[6] is not None else None,
                resource_id=str(row[7]) if row[7] is not None else None,
                payload=_json_load(row[8], {}),
                created_at=row[9].replace(tzinfo=UTC) if row[9].tzinfo is None else row[9].astimezone(UTC),
            )
            for row in rows
        )
