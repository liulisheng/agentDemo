"""只读 schema migration 预检；该模块绝不执行 DDL。"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

_logger = logging.getLogger(__name__)

MIGRATION_FILE = re.compile(r"^V(?P<version>[0-9]{3})__(?P<name>[a-zA-Z0-9_]+)\.sql$")
V003_SENTINEL_OBJECTS = frozenset(
    {
        "digital_employees",
        "digital_employee_versions",
        "deployments",
        "model_routes",
        "platform_trace_spans",
        "usage_ledger",
    }
)
V001_SENTINEL_OBJECTS = frozenset(
    {"schema_version", "platforms", "tenants", "users", "sessions", "runs", "audit_events"}
)
V002_SENTINEL_OBJECTS = frozenset(
    {"messages", "message_chunks", "content_upload_intents", "content_gc_queue"}
)


class PreflightAction(StrEnum):
    """预检建议；所有分支都只给建议，不执行迁移。"""

    APPLY_MIGRATIONS = "APPLY_MIGRATIONS"
    USE_FORWARD_MIGRATION = "USE_FORWARD_MIGRATION"
    UP_TO_DATE = "UP_TO_DATE"
    BLOCK_PARTIAL_MIGRATION = "BLOCK_PARTIAL_MIGRATION"
    BLOCK_CHECKSUM_MISMATCH = "BLOCK_CHECKSUM_MISMATCH"
    BLOCK_INCONSISTENT_SCHEMA = "BLOCK_INCONSISTENT_SCHEMA"
    MIGRATION_FAILED = "MIGRATION_FAILED"
    MIGRATION_LOCKED = "MIGRATION_LOCKED"


@dataclass(frozen=True, slots=True)
class MigrationBaseline:
    """从数据库只读读取的版本、checksum 与迁移标志对象。"""

    schema_version: int | None
    applied_checksums: Mapping[int, str] = field(default_factory=dict)
    schema_objects: frozenset[str] = frozenset()
    checksum_history_available: bool = False
    schema_columns: Mapping[str, frozenset[str]] = field(default_factory=dict)
    introspection_available: bool = False


@dataclass(frozen=True, slots=True)
class MigrationPreflightResult:
    """migration preflight 的可打印结果，不代表已执行迁移。"""

    action: PreflightAction
    current_version: int | None
    migrations_to_apply: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    database_changed: bool = False


@dataclass(frozen=True, slots=True)
class MigrationApplyResult:
    """显式 migration runner 的结果，不会混淆预检与实际 apply。"""

    action: PreflightAction
    current_version: int | None
    applied_migrations: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    database_changed: bool = False


class MigrationStateReader(Protocol):
    """只读查询数据库 schema 状态的端口。"""

    def read_baseline(self) -> MigrationBaseline:
        ...


class MySqlMigrationStateReader:
    """通过 information_schema 与版本表读取状态，不创建或修改数据库对象。"""

    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connection_factory = connection_factory

    def read_baseline(self) -> MigrationBaseline:
        connection = self._connection_factory()
        try:
            return self.read_connection(connection)
        finally:
            connection.close()

    def read_connection(self, connection: Any) -> MigrationBaseline:
        """读取调用方已打开连接，供 runner 在持有 GET_LOCK 时复用同一会话。"""

        cursor = connection.cursor()
        try:
            cursor.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = DATABASE()"
            )
            tables = frozenset(self._first_value(row) for row in cursor.fetchall())

            columns: dict[str, set[str]] = {}
            cursor.execute(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = DATABASE()"
            )
            for row in cursor.fetchall():
                table_name, column_name = self._two_values(row)
                columns.setdefault(str(table_name), set()).add(str(column_name))

            schema_version: int | None = None
            if "schema_version" in tables:
                cursor.execute("SELECT MAX(version) FROM schema_version")
                row = cursor.fetchone()
                value = self._first_value(row) if row is not None else None
                schema_version = int(value) if value is not None else None

            checksums: dict[int, str] = {}
            checksum_table = "schema_migration_history"
            if checksum_table in tables:
                cursor.execute(
                    "SELECT version, checksum FROM schema_migration_history ORDER BY version"
                )
                for row in cursor.fetchall():
                    version, checksum = self._two_values(row)
                    checksums[int(version)] = str(checksum)
            checksum_history_complete = bool(
                checksum_table in tables
                and schema_version is not None
                and all(version in checksums for version in range(1, schema_version + 1))
            )
            return MigrationBaseline(
                schema_version=schema_version,
                applied_checksums=checksums,
                schema_objects=tables,
                checksum_history_available=checksum_history_complete,
                schema_columns={name: frozenset(values) for name, values in columns.items()},
                introspection_available=True,
            )
        finally:
            cursor.close()

    @staticmethod
    def _first_value(row: Any) -> Any:
        if isinstance(row, Mapping):
            return next(iter(row.values()))
        return row[0]

    @staticmethod
    def _two_values(row: Any) -> tuple[Any, Any]:
        if isinstance(row, Mapping):
            return row["version"], row["checksum"]
        return row[0], row[1]


def load_migration_checksums(directory: str | Path) -> dict[int, str]:
    """计算规范 migration 文件 checksum，忽略 down 文件和非 SQL 文件。"""

    root = Path(directory)
    checksums: dict[int, str] = {}
    for path in sorted(root.glob("V[0-9][0-9][0-9]__*.sql")):
        match = MIGRATION_FILE.fullmatch(path.name)
        if match is None:
            continue
        version = int(match.group("version"))
        if version in checksums:
            raise ValueError(f"duplicate migration version: {version}")
        checksums[version] = hashlib.sha256(path.read_bytes()).hexdigest()
    return checksums


def main(
    argv: Sequence[str] | None = None,
    *,
    connection_factory: Callable[[], Any] | None = None,
) -> int:
    """显式执行只读 preflight 或 migration apply；应用启动不会调用该命令。"""

    parser = argparse.ArgumentParser(prog="agent-platform-migration")
    commands = parser.add_subparsers(dest="command", required=True)
    preflight = commands.add_parser("preflight", help="只读检查 schema 版本与 migration checksum")
    apply = commands.add_parser("apply", help="显式按版本应用 migration")
    repository_root = Path(__file__).resolve().parents[4]
    for command in (preflight, apply):
        command.add_argument("--migrations-dir", type=Path, default=repository_root / "docs" / "migrations")
        command.add_argument("--host", default=os.environ.get("MYSQL_HOST", "127.0.0.1"))
        command.add_argument("--port", type=int, default=int(os.environ.get("MYSQL_PORT", "3306")))
        command.add_argument("--database", default=os.environ.get("MYSQL_DATABASE", "harness"))
        command.add_argument("--user", default=os.environ.get("MYSQL_USER", "harness"))
    apply.add_argument("--lock-timeout", type=int, default=MigrationRunner.LOCK_TIMEOUT_SECONDS)
    args = parser.parse_args(argv)

    try:
        if connection_factory is None:
            try:
                import mysql.connector  # type: ignore[import-not-found]
            except ImportError as error:
                raise RuntimeError(
                    "MySQL preflight requires the backend optional dependency: pip install -e '.[mysql]'"
                ) from error

            password = os.environ.get("MYSQL_PASSWORD")

            def connection_factory() -> Any:
                return mysql.connector.connect(
                    host=args.host,
                    port=args.port,
                    database=args.database,
                    user=args.user,
                    password=password,
                )

        if args.command == "preflight":
            checksums = load_migration_checksums(args.migrations_dir)
            result = MigrationPreflight(checksums).inspect(
                MySqlMigrationStateReader(connection_factory)
            )
            applied_migrations: list[str] = []
            migrations_to_apply = list(result.migrations_to_apply)
        else:
            result = MigrationRunner(
                connection_factory,
                args.migrations_dir,
                lock_timeout_seconds=args.lock_timeout,
            ).apply()
            applied_migrations = list(result.applied_migrations)
            migrations_to_apply = []
        payload = {
            "action": result.action.value,
            "current_version": result.current_version,
            "migrations_to_apply": migrations_to_apply,
            "applied_migrations": applied_migrations,
            "warnings": list(result.warnings),
            "errors": list(result.errors),
            "database_changed": result.database_changed,
        }
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return 2 if result.errors or result.action in {
            PreflightAction.BLOCK_PARTIAL_MIGRATION,
            PreflightAction.BLOCK_CHECKSUM_MISMATCH,
            PreflightAction.BLOCK_INCONSISTENT_SCHEMA,
            PreflightAction.MIGRATION_FAILED,
            PreflightAction.MIGRATION_LOCKED,
        } else 0
    except Exception as error:  # noqa: BLE001 - CLI reports a stable, concise preflight failure.
        print(
            json.dumps(
                {"action": "ERROR", "error": str(error), "database_changed": False},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 2


class MigrationPreflight:
    """比较当前 schema 与 migration 文件，只建议 V003 或 forward V004。"""

    def __init__(self, expected_checksums: Mapping[int, str] | None = None) -> None:
        self._checksums = dict(expected_checksums or {})

    def inspect(self, reader: MigrationStateReader) -> MigrationPreflightResult:
        """读取数据库一次并产生只读预检结果。"""

        return self.assess(reader.read_baseline())

    def assess(self, baseline: MigrationBaseline) -> MigrationPreflightResult:
        current = baseline.schema_version
        warnings: list[str] = []
        errors: list[str] = []

        for version, applied_checksum in sorted(baseline.applied_checksums.items()):
            expected = self._checksums.get(version)
            if expected is None or expected == applied_checksum:
                continue
            # 只允许把正在核对的草稿 V003 转成 V004；历史版本或当前版本的 checksum
            # 不一致表示数据库历史与仓库不一致，必须停止而不是继续猜测。
            if not (version == 3 and current == 3):
                errors.append(f"checksum_mismatch:V{version:03d}")
            else:
                warnings.append(f"checksum_mismatch:V{version:03d}")

        if errors:
            return MigrationPreflightResult(
                PreflightAction.BLOCK_CHECKSUM_MISMATCH,
                current,
                warnings=tuple(warnings),
                errors=tuple(errors),
            )

        if current is not None and current >= 3 and not baseline.checksum_history_available:
            warnings.append("migration_history_incomplete")

        if baseline.introspection_available:
            current_objects = baseline.schema_objects
            any_known_objects = bool(
                current_objects.intersection(V001_SENTINEL_OBJECTS | V002_SENTINEL_OBJECTS | V003_SENTINEL_OBJECTS)
            )
            if current is None and any_known_objects:
                return MigrationPreflightResult(
                    PreflightAction.BLOCK_PARTIAL_MIGRATION,
                    current,
                    errors=("schema_objects_present_without_version",),
                )
            if current is not None and current >= 1:
                missing_v001 = sorted(V001_SENTINEL_OBJECTS.difference(current_objects))
                if missing_v001:
                    return MigrationPreflightResult(
                        PreflightAction.BLOCK_PARTIAL_MIGRATION,
                        current,
                        errors=("partial_v001_schema:" + ",".join(missing_v001),),
                    )
            if current is not None and current >= 2:
                missing_v002 = sorted(V002_SENTINEL_OBJECTS.difference(current_objects))
                session_columns = baseline.schema_columns.get("sessions", frozenset())
                if "last_message_sequence" not in session_columns:
                    missing_v002.append("sessions.last_message_sequence")
                if missing_v002:
                    return MigrationPreflightResult(
                        PreflightAction.BLOCK_PARTIAL_MIGRATION,
                        current,
                        errors=("partial_v002_schema:" + ",".join(missing_v002),),
                    )
            if current is not None and current < 2:
                partial_v002 = current_objects.intersection(V002_SENTINEL_OBJECTS)
                session_columns = baseline.schema_columns.get("sessions", frozenset())
                if partial_v002 or "last_message_sequence" in session_columns:
                    return MigrationPreflightResult(
                        PreflightAction.BLOCK_PARTIAL_MIGRATION,
                        current,
                        errors=("partial_v002_schema",),
                    )

        found_v003_objects = baseline.schema_objects.intersection(V003_SENTINEL_OBJECTS)
        if found_v003_objects and found_v003_objects != V003_SENTINEL_OBJECTS:
            missing = sorted(V003_SENTINEL_OBJECTS.difference(found_v003_objects))
            return MigrationPreflightResult(
                PreflightAction.BLOCK_PARTIAL_MIGRATION,
                current,
                warnings=tuple(warnings),
                errors=("partial_v003_schema:" + ",".join(missing),),
            )

        v003_present = found_v003_objects == V003_SENTINEL_OBJECTS
        if v003_present and (current is None or current < 3):
            warnings.append("v003_objects_present_without_schema_version")
            return MigrationPreflightResult(
                PreflightAction.USE_FORWARD_MIGRATION,
                current,
                migrations_to_apply=("V004",),
                warnings=tuple(warnings),
            )

        if current is not None and current >= 3 and not v003_present:
            return MigrationPreflightResult(
                PreflightAction.BLOCK_INCONSISTENT_SCHEMA,
                current,
                warnings=tuple(warnings),
                errors=("schema_version_ahead_of_v003_objects",),
            )

        if current is not None and current >= 3:
            if current == 3:
                applied = baseline.applied_checksums.get(3)
                expected = self._checksums.get(3)
                if applied is None or expected is None:
                    warnings.append("v003_checksum_unavailable_requires_forward_migration")
                    return MigrationPreflightResult(
                        PreflightAction.USE_FORWARD_MIGRATION,
                        current,
                        migrations_to_apply=("V004",),
                        warnings=tuple(warnings),
                    )
                if applied != expected:
                    warnings.append("checksum_mismatch:V003")
                    return MigrationPreflightResult(
                        PreflightAction.USE_FORWARD_MIGRATION,
                        current,
                        migrations_to_apply=("V004",),
                        warnings=tuple(warnings),
                    )
            return MigrationPreflightResult(
                PreflightAction.UP_TO_DATE,
                current,
                warnings=tuple(warnings),
            )

        if current is None:
            start = 1
        elif current < 0:
            return MigrationPreflightResult(
                PreflightAction.BLOCK_INCONSISTENT_SCHEMA,
                current,
                errors=("invalid_schema_version",),
            )
        else:
            start = current + 1

        available_versions = sorted(self._checksums) or [1, 2, 3]
        pending = tuple(f"V{version:03d}" for version in available_versions if version >= start and version <= 3)
        if not pending:
            return MigrationPreflightResult(
                PreflightAction.BLOCK_INCONSISTENT_SCHEMA,
                current,
                warnings=tuple(warnings),
                errors=("no_forward_migration_available",),
            )
        if not baseline.checksum_history_available and current in {1, 2}:
            warnings.append("legacy_migration_checksums_unavailable")
        return MigrationPreflightResult(
            PreflightAction.APPLY_MIGRATIONS,
            current,
            migrations_to_apply=pending,
            warnings=tuple(warnings),
        )


def split_sql_statements(script: str) -> tuple[str, ...]:
    """按 MySQL 引号和注释边界拆分 migration；当前 schema 不使用存储过程 delimiter。"""

    statements: list[str] = []
    buffer: list[str] = []
    quote: str | None = None
    line_comment = False
    block_comment = False
    index = 0
    while index < len(script):
        char = script[index]
        next_char = script[index + 1] if index + 1 < len(script) else ""
        if line_comment:
            if char == "\n":
                line_comment = False
                buffer.append(char)
            index += 1
            continue
        if block_comment:
            if char == "*" and next_char == "/":
                block_comment = False
                index += 2
            else:
                index += 1
            continue
        if quote is None and char == "-" and next_char == "-":
            line_comment = True
            index += 2
            continue
        if quote is None and char == "/" and next_char == "*":
            block_comment = True
            index += 2
            continue
        if quote is not None:
            buffer.append(char)
            if char == "\\" and index + 1 < len(script):
                buffer.append(script[index + 1])
                index += 2
                continue
            if char == quote:
                if next_char == quote:
                    buffer.append(next_char)
                    index += 2
                    continue
                quote = None
            index += 1
            continue
        if char in {"'", '"', "`"}:
            quote = char
            buffer.append(char)
        elif char == ";":
            statement = "".join(buffer).strip()
            if statement:
                statements.append(statement)
            buffer.clear()
        else:
            buffer.append(char)
        index += 1
    tail = "".join(buffer).strip()
    if tail:
        statements.append(tail)
    if quote is not None or block_comment:
        raise ValueError("migration contains an unterminated quote or comment")
    return tuple(statements)


class MigrationRunner:
    """显式执行版本化 migration；API/Worker 启动路径不调用此类。"""

    LOCK_TIMEOUT_SECONDS = 30

    def __init__(
        self,
        connection_factory: Callable[[], Any],
        migrations_directory: str | Path,
        *,
        lock_timeout_seconds: int = LOCK_TIMEOUT_SECONDS,
    ) -> None:
        self.connection_factory = connection_factory
        self.migrations_directory = Path(migrations_directory)
        if lock_timeout_seconds <= 0:
            raise ValueError("lock_timeout_seconds must be positive")
        self.lock_timeout_seconds = lock_timeout_seconds

    def apply(self) -> MigrationApplyResult:
        """持有数据库 advisory lock 后按顺序应用待执行版本并记录 checksum。"""

        try:
            checksums = load_migration_checksums(self.migrations_directory)
        except Exception as error:  # noqa: BLE001 - 配置错误转为稳定 CLI 结果
            return MigrationApplyResult(
                PreflightAction.MIGRATION_FAILED,
                None,
                errors=(f"migration_catalog_error:{type(error).__name__}",),
            )
        connection = None
        lock_name: str | None = None
        lock_acquired = False
        applied: list[str] = []
        database_changed = False
        try:
            connection = self.connection_factory()
            lock_cursor = connection.cursor()
            try:
                lock_cursor.execute("SELECT DATABASE()")
                database_row = lock_cursor.fetchone()
                database_name = MySqlMigrationStateReader._first_value(database_row)
                if not database_name:
                    return MigrationApplyResult(
                        PreflightAction.MIGRATION_FAILED,
                        None,
                        errors=("database_name_unavailable",),
                    )
                lock_name = "agent-platform-migrate:" + hashlib.sha256(
                    str(database_name).encode("utf-8")
                ).hexdigest()[:32]
                lock_cursor.execute(
                    "SELECT GET_LOCK(%s, %s)",
                    (lock_name, self.lock_timeout_seconds),
                )
                lock_acquired = MySqlMigrationStateReader._first_value(lock_cursor.fetchone()) == 1
            finally:
                lock_cursor.close()

            if not lock_acquired:
                baseline = MySqlMigrationStateReader(lambda: connection).read_connection(connection)
                return MigrationApplyResult(
                    PreflightAction.MIGRATION_LOCKED,
                    baseline.schema_version,
                    warnings=("migration_lock_unavailable",),
                    errors=("migration_lock_timeout",),
                )

            reader = MySqlMigrationStateReader(lambda: connection)
            baseline = reader.read_connection(connection)
            plan = MigrationPreflight(checksums).assess(baseline)
            if plan.action is PreflightAction.UP_TO_DATE:
                return MigrationApplyResult(
                    plan.action,
                    plan.current_version,
                    warnings=plan.warnings,
                    errors=plan.errors,
                )
            if plan.errors or plan.action not in {
                PreflightAction.APPLY_MIGRATIONS,
                PreflightAction.USE_FORWARD_MIGRATION,
            }:
                return MigrationApplyResult(
                    plan.action,
                    plan.current_version,
                    warnings=plan.warnings,
                    errors=plan.errors,
                )

            files = self._migration_files()
            missing = [name for name in plan.migrations_to_apply if name not in files]
            if missing:
                return MigrationApplyResult(
                    PreflightAction.BLOCK_INCONSISTENT_SCHEMA,
                    plan.current_version,
                    warnings=plan.warnings,
                    errors=tuple(f"forward_migration_file_missing:{name}" for name in missing),
                )

            for migration_name in plan.migrations_to_apply:
                version = int(migration_name[1:])
                self._execute_file(connection, files[migration_name])
                database_changed = True
                current = reader.read_connection(connection)
                if current.schema_version != version:
                    raise RuntimeError(
                        f"migration_version_not_recorded:{migration_name}:"
                        f"{current.schema_version}"
                    )
                applied.append(migration_name)

            final_baseline = reader.read_connection(connection)
            if "schema_migration_history" not in final_baseline.schema_objects:
                raise RuntimeError("schema_migration_history_missing_after_migration")
            history_cursor = connection.cursor()
            try:
                for migration_name in applied:
                    version = int(migration_name[1:])
                    checksum = checksums[version]
                    history_cursor.execute(
                        "SELECT checksum FROM schema_migration_history WHERE version = %s",
                        (version,),
                    )
                    row = history_cursor.fetchone()
                    if row is not None:
                        stored_checksum = str(MySqlMigrationStateReader._first_value(row))
                        if stored_checksum != checksum:
                            raise RuntimeError(f"checksum_mismatch_after_apply:{migration_name}")
                        continue
                    file_path = files[migration_name]
                    history_cursor.execute(
                        "INSERT INTO schema_migration_history (version, migration_name, checksum) "
                        "VALUES (%s, %s, %s)",
                        (version, file_path.name, checksum),
                    )
                connection.commit()
            finally:
                history_cursor.close()

            final_baseline = reader.read_connection(connection)
            return MigrationApplyResult(
                PreflightAction.APPLY_MIGRATIONS,
                final_baseline.schema_version,
                applied_migrations=tuple(applied),
                warnings=plan.warnings,
                database_changed=database_changed,
            )
        except Exception as error:  # noqa: BLE001 - runner stops at first error and never marks it successful
            if connection is not None:
                try:
                    connection.rollback()
                except Exception as rollback_error:  # noqa: BLE001 - preserve the original migration error
                    _logger.warning(
                        "数据库回滚清理失败，原迁移错误仍将返回: %s",
                        type(rollback_error).__name__,
                    )
            current_version = None
            if connection is not None:
                try:
                    current_version = MySqlMigrationStateReader(lambda: connection).read_connection(connection).schema_version
                except Exception as state_error:  # noqa: BLE001 - DB may be unavailable after the failure
                    _logger.warning(
                        "迁移失败后无法再次读取 schema 状态: %s",
                        type(state_error).__name__,
                    )
            return MigrationApplyResult(
                PreflightAction.MIGRATION_FAILED,
                current_version,
                applied_migrations=tuple(applied),
                errors=(f"migration_failed:{type(error).__name__}:{error}",),
                database_changed=database_changed,
            )
        finally:
            if connection is not None:
                if lock_acquired and lock_name is not None:
                    try:
                        cursor = connection.cursor()
                        try:
                            cursor.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))
                            cursor.fetchone()
                        finally:
                            cursor.close()
                    except Exception as lock_error:  # noqa: BLE001 - closing connection also releases named locks
                        _logger.warning(
                            "释放 migration advisory lock 失败: %s",
                            type(lock_error).__name__,
                        )
                connection.close()

    def _migration_files(self) -> dict[str, Path]:
        files: dict[str, Path] = {}
        for path in sorted(self.migrations_directory.glob("V[0-9][0-9][0-9]__*.sql")):
            match = MIGRATION_FILE.fullmatch(path.name)
            if match is not None:
                files[f"V{match.group('version')}"] = path
        return files

    @staticmethod
    def _execute_file(connection: Any, path: Path) -> None:
        cursor = connection.cursor()
        try:
            for statement in split_sql_statements(path.read_text(encoding="utf-8")):
                cursor.execute(statement)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()


if __name__ == "__main__":
    raise SystemExit(main())
