"""V003 schema 的 MySQL 8.4 集成契约，只能使用随机 disposable database。"""

from __future__ import annotations

import os
import re
import secrets
from pathlib import Path

import pytest

mysql = pytest.importorskip("mysql.connector")

_REQUIRED_TABLES = {
    "digital_employees",
    "digital_employee_versions",
    "skill_versions",
    "skill_tool_declarations",
    "tool_versions",
    "execution_profiles",
    "execution_profile_bindings",
    "workspace_profiles",
    "sandbox_profiles",
    "employee_skill_bindings",
    "employee_tool_bindings",
    "capability_bindings",
    "model_routes",
    "deployments",
    "conversations",
    "conversation_participants",
    "agent_bindings",
    "platform_trace_spans",
    "usage_ledger",
    "media_assets",
    "schema_migration_history",
}

_NEW_TABLES = _REQUIRED_TABLES.difference(
    {"sessions", "runs", "messages", "content_upload_intents", "content_gc_queue"}
)

_COMMENTED_TABLES = _NEW_TABLES.union(
    {"sessions", "runs", "messages", "content_upload_intents", "content_gc_queue"}
)

_SCOPED_TABLES = _NEW_TABLES.difference({"schema_migration_history"}).union(
    {"sessions", "runs", "messages", "content_upload_intents", "content_gc_queue"}
)

_CHANGED_COLUMNS = {
    "sessions": {"deployment_id", "employee_id", "employee_version", "trace_id", "execution_profile_id", "execution_profile_version", "model_route_id", "composition_snapshot_json", "runtime_thread_ref", "runtime_workspace_ref", "runtime_result_ref"},
    "runs": {"employee_id", "employee_version", "execution_profile_id", "execution_profile_version", "model_route_id", "trace_id", "snapshot_json", "context_snapshot_json", "runtime_session_ref", "runtime_result_ref"},
    "messages": {"sender_id", "source", "routing_depth", "conversation_id", "retention_policy"},
    "content_upload_intents": {"original_filename", "mime_type", "retention_expires_at", "retention_policy", "encryption_key_version", "expected_size_bytes", "expected_sha256"},
}


def _split_sql(script: str) -> list[str]:
    """拆分本仓库无存储过程的 DDL，忽略引号内分号和 SQL 注释。"""

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
    return statements


def _has_chinese(text: str | None) -> bool:
    return bool(text and re.search(r"[\u3400-\u9fff]", text))


@pytest.fixture
def migrated_database():
    if os.environ.get("AGENT_PLATFORM_MYSQL_TEST_ALLOW_CREATE_DATABASE") != "1":
        pytest.skip("set AGENT_PLATFORM_MYSQL_TEST_ALLOW_CREATE_DATABASE=1 for disposable MySQL integration")
    database = f"agent_platform_schema_test_{secrets.token_hex(5)}"
    config = {
        "host": os.environ.get("MYSQL_HOST", "127.0.0.1"),
        "port": int(os.environ.get("MYSQL_PORT", "3306")),
        "user": os.environ.get("MYSQL_USER", "root"),
        "password": os.environ.get("MYSQL_PASSWORD", ""),
    }
    root_connection = mysql.connect(**config)
    cursor = root_connection.cursor()
    try:
        cursor.execute(f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci")
        root_connection.commit()
    finally:
        cursor.close()
        root_connection.close()
    connection = mysql.connect(**config, database=database)
    try:
        root = Path(__file__).parents[3]
        for filename in (
            "V001__core_schema.sql",
            "V002__conversation_messages.sql",
            "V003__agent_platform_foundation.sql",
        ):
            script = (root / "docs" / "migrations" / filename).read_text(encoding="utf-8")
            cursor = connection.cursor()
            try:
                for statement in _split_sql(script):
                    cursor.execute(statement)
                connection.commit()
            finally:
                cursor.close()
        yield connection, database, config
    finally:
        connection.close()
        cleanup = mysql.connect(**config)
        cleanup_cursor = cleanup.cursor()
        try:
            cleanup_cursor.execute(f"DROP DATABASE IF EXISTS `{database}`")
            cleanup.commit()
        finally:
            cleanup_cursor.close()
            cleanup.close()


def test_v003_creates_platform_relations_and_preserves_char26_keys(migrated_database) -> None:
    connection, _, _ = migrated_database
    cursor = connection.cursor()
    try:
        cursor.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = DATABASE()")
        tables = {row[0] for row in cursor.fetchall()}
        assert _REQUIRED_TABLES <= tables

        cursor.execute(
            "SELECT non_unique FROM information_schema.statistics "
            "WHERE table_schema = DATABASE() AND table_name = 'deployments' "
            "AND index_name = 'idx_deployment_employee_version' ORDER BY seq_in_index"
        )
        deployment_version_index = [row[0] for row in cursor.fetchall()]
        assert deployment_version_index and all(value == 1 for value in deployment_version_index)

        for table, column in (
            ("sessions", "session_id"),
            ("runs", "run_id"),
            ("messages", "message_id"),
            ("content_upload_intents", "upload_id"),
            ("content_gc_queue", "cleanup_id"),
        ):
            cursor.execute(
                "SELECT character_maximum_length FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = %s AND column_name = %s",
                (table, column),
            )
            assert cursor.fetchone()[0] == 26

        for table, columns in _CHANGED_COLUMNS.items():
            cursor.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = %s",
                (table,),
            )
            present = {row[0] for row in cursor.fetchall()}
            assert columns <= present

        cursor.execute(
            "SELECT table_name, table_comment FROM information_schema.tables "
            "WHERE table_schema = DATABASE() AND table_name IN (" + ",".join(["%s"] * len(_COMMENTED_TABLES)) + ")",
            tuple(sorted(_COMMENTED_TABLES)),
        )
        comments = {table: comment for table, comment in cursor.fetchall()}
        assert _COMMENTED_TABLES <= comments.keys()
        assert all(_has_chinese(comments[table]) for table in _COMMENTED_TABLES)

        for table in _NEW_TABLES:
            cursor.execute(
                "SELECT column_name, column_comment FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = %s",
                (table,),
            )
            columns = cursor.fetchall()
            assert columns
            assert all(_has_chinese(comment) for _, comment in columns), table

        for table, columns in _CHANGED_COLUMNS.items():
            cursor.execute(
                "SELECT column_name, column_comment FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = %s",
                (table,),
            )
            comments = dict(cursor.fetchall())
            assert all(_has_chinese(comments[column]) for column in columns), table

        cursor.execute("SELECT MAX(version) FROM schema_version")
        assert cursor.fetchone()[0] == 3
    finally:
        cursor.close()


def test_v003_down_restores_v002_schema(migrated_database) -> None:
    connection, _, _ = migrated_database
    root = Path(__file__).parents[3]
    script = (root / "docs" / "migrations" / "V003__agent_platform_foundation.down.sql").read_text(encoding="utf-8")
    cursor = connection.cursor()
    try:
        for statement in _split_sql(script):
            cursor.execute(statement)
        connection.commit()
        cursor.execute("SELECT MAX(version) FROM schema_version")
        assert cursor.fetchone()[0] == 2
        cursor.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = DATABASE()")
        remaining = {row[0] for row in cursor.fetchall()}
        assert "digital_employees" not in remaining
        assert "conversations" not in remaining
        cursor.execute(
            "SELECT COUNT(*) FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = 'sessions' AND column_name = 'deployment_id'"
        )
        assert cursor.fetchone()[0] == 0
    finally:
        cursor.close()


def test_v003_tenant_tables_scope_indexes_and_foreign_keys(migrated_database) -> None:
    connection, _, _ = migrated_database
    scoped_tables = _SCOPED_TABLES
    cursor = connection.cursor()
    try:
        for table in scoped_tables:
            cursor.execute(
                "SELECT index_name, seq_in_index, column_name FROM information_schema.statistics "
                "WHERE table_schema = DATABASE() AND table_name = %s ORDER BY index_name, seq_in_index",
                (table,),
            )
            indexes: dict[str, list[str]] = {}
            for index_name, _, column_name in cursor.fetchall():
                indexes.setdefault(index_name, []).append(column_name)
            assert indexes, table
            for index_name, columns in indexes.items():
                assert columns[:2] == ["platform_id", "tenant_id"], (table, index_name, columns)

        cursor.execute(
            "SELECT table_name, constraint_name, ordinal_position, column_name "
            "FROM information_schema.key_column_usage "
            "WHERE table_schema = DATABASE() AND referenced_table_name IS NOT NULL "
            "ORDER BY table_name, constraint_name, ordinal_position"
        )
        foreign_keys: dict[tuple[str, str], list[str]] = {}
        for table, constraint, _, column in cursor.fetchall():
            if table in scoped_tables:
                foreign_keys.setdefault((table, constraint), []).append(column)
        assert foreign_keys
        for key, columns in foreign_keys.items():
            assert columns[:2] == ["platform_id", "tenant_id"], (key, columns)
    finally:
        cursor.close()
