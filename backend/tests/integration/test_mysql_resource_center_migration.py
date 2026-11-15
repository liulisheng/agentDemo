"""V004 Resource Center MySQL schema/COMMENT/复合租户约束验收。"""

from __future__ import annotations

import os
import re
import secrets
import shutil
from pathlib import Path

import pytest

mysql = pytest.importorskip("mysql.connector")

_HUB_TABLES = {
    "hub_resources",
    "hub_resource_versions",
    "hub_resource_dependencies",
    "hub_resource_grants",
    "tenant_resource_imports",
}


def _split_sql(script: str) -> list[str]:
    """拆分无存储过程 migration，忽略引号和 SQL 注释中的分号。"""

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


@pytest.fixture
def migrated_v004_database(tmp_path):
    if os.environ.get("AGENT_PLATFORM_MYSQL_TEST_ALLOW_CREATE_DATABASE") != "1":
        pytest.skip("set AGENT_PLATFORM_MYSQL_TEST_ALLOW_CREATE_DATABASE=1 for disposable MySQL integration")
    database = f"agent_platform_hub_schema_{secrets.token_hex(5)}"
    config = {
        "host": os.environ.get("MYSQL_HOST", "127.0.0.1"),
        "port": int(os.environ.get("MYSQL_PORT", "3306")),
        "user": os.environ.get("MYSQL_USER", "root"),
        "password": os.environ.get("MYSQL_PASSWORD", ""),
    }
    connection = mysql.connect(**config)
    cursor = connection.cursor()
    try:
        cursor.execute(f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci")
        connection.commit()
    finally:
        cursor.close()
        connection.close()
    try:
        from agent_platform.infrastructure.migrations import MigrationRunner

        migration_source = Path(__file__).parents[3] / "docs" / "migrations"
        for migration in migration_source.glob("V[0-9][0-9][0-9]__*.sql"):
            version = int(migration.name[1:4])
            if version <= 4 and ".down." not in migration.name:
                shutil.copy2(migration, tmp_path / migration.name)
        runner = MigrationRunner(
            lambda: mysql.connect(**config, database=database),
            tmp_path,
        )
        applied = runner.apply()
        yield database, config, applied
    finally:
        connection = mysql.connect(**config)
        cursor = connection.cursor()
        try:
            cursor.execute(f"DROP DATABASE IF EXISTS `{database}`")
            connection.commit()
        finally:
            cursor.close()
            connection.close()


def _has_chinese(value: str | None) -> bool:
    return bool(value and re.search(r"[\u3400-\u9fff]", value))


def test_v004_resource_center_migration_has_documented_scoped_schema(migrated_v004_database) -> None:
    database, config, applied = migrated_v004_database
    assert applied.applied_migrations == ("V001", "V002", "V003", "V004")

    connection = mysql.connect(**config, database=database)
    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT table_name, table_comment FROM information_schema.tables "
            "WHERE table_schema=DATABASE() AND table_name IN (%s,%s,%s,%s,%s)",
            tuple(sorted(_HUB_TABLES)),
        )
        table_comments = dict(cursor.fetchall())
        assert set(table_comments) == _HUB_TABLES
        assert all(_has_chinese(comment) for comment in table_comments.values())

        for table in _HUB_TABLES:
            cursor.execute(
                "SELECT column_name, column_comment FROM information_schema.columns "
                "WHERE table_schema=DATABASE() AND table_name=%s",
                (table,),
            )
            columns = cursor.fetchall()
            assert columns, table
            assert all(_has_chinese(comment) for _, comment in columns), table

        expected_columns = {
            "hub_resources": {
                "platform_id", "hub_resource_id", "resource_type", "publisher_tenant_id",
                "publisher_user_id", "name", "description", "visibility", "status",
            },
            "hub_resource_versions": {
                "platform_id", "hub_resource_id", "version", "publisher_tenant_id",
                "source_resource_type", "source_resource_id", "source_resource_version",
                "manifest_json", "manifest_sha256", "artifact_asset_id", "status", "created_by",
            },
            "hub_resource_dependencies": {
                "platform_id", "hub_resource_id", "hub_version", "dependency_id",
                "dependency_type", "dependency_hub_resource_id", "dependency_hub_version",
                "required", "sequence_no",
            },
            "hub_resource_grants": {
                "platform_id", "grant_id", "hub_resource_id", "hub_version",
                "publisher_tenant_id", "recipient_tenant_id", "copy_allowed",
                "reference_allowed", "status", "expires_at", "revoked_at", "created_by",
            },
            "tenant_resource_imports": {
                "platform_id", "tenant_id", "import_id", "hub_resource_id",
                "hub_version", "mode", "local_resource_type", "local_resource_id",
                "local_resource_version", "manifest_sha256", "status", "imported_by",
            },
        }
        for table, required in expected_columns.items():
            cursor.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema=DATABASE() AND table_name=%s",
                (table,),
            )
            assert required <= {row[0] for row in cursor.fetchall()}, table

        cursor.execute(
            "SELECT column_comment FROM information_schema.columns WHERE table_schema=DATABASE() "
            "AND table_name='tool_versions' AND column_name='requires_recipient_credentials'"
        )
        assert _has_chinese(cursor.fetchone()[0])

        # 每张 Hub 表的所有索引都至少先限定 platform，租户拥有的数据另以
        # platform_id + tenant_id/publisher_tenant_id/recipient_tenant_id 联合键约束。
        for table in _HUB_TABLES:
            cursor.execute(
                "SELECT index_name, seq_in_index, column_name FROM information_schema.statistics "
                "WHERE table_schema=DATABASE() AND table_name=%s ORDER BY index_name, seq_in_index",
                (table,),
            )
            indexes: dict[str, list[str]] = {}
            for index_name, _, column_name in cursor.fetchall():
                indexes.setdefault(str(index_name), []).append(str(column_name))
            assert indexes, table
            for index_name, columns in indexes.items():
                assert columns[0] == "platform_id", (table, index_name, columns)

        cursor.execute(
            "SELECT table_name, constraint_name, ordinal_position, column_name, "
            "referenced_table_name, referenced_column_name "
            "FROM information_schema.key_column_usage WHERE table_schema=DATABASE() "
            "AND table_name IN (%s,%s,%s,%s,%s) AND referenced_table_name IS NOT NULL "
            "ORDER BY table_name, constraint_name, ordinal_position",
            tuple(sorted(_HUB_TABLES)),
        )
        foreign_keys: dict[tuple[str, str], list[tuple[str, str, str]]] = {}
        for table, constraint, _, column, referenced_table, referenced_column in cursor.fetchall():
            foreign_keys.setdefault((str(table), str(constraint)), []).append(
                (str(column), str(referenced_table), str(referenced_column))
            )
        assert foreign_keys
        assert all(any(column == "platform_id" for column, _, _ in columns) for columns in foreign_keys.values())
        assert any(key[0] == "hub_resources" and columns[:2] == [
            ("platform_id", "tenants", "platform_id"),
            ("publisher_tenant_id", "tenants", "tenant_id"),
        ] for key, columns in foreign_keys.items())
        assert any(key[0] == "hub_resource_grants" and any(column == "recipient_tenant_id" for column, _, _ in columns)
                   for key, columns in foreign_keys.items())

        cursor.execute("SELECT MAX(version) FROM schema_version")
        assert cursor.fetchone()[0] == 4
    finally:
        cursor.close()


def test_v004_down_removes_resource_center_and_restores_version_three(migrated_v004_database) -> None:
    database, config, _ = migrated_v004_database
    root = Path(__file__).parents[3]
    script = (root / "docs" / "migrations" / "V004__resource_center.down.sql").read_text(encoding="utf-8")
    connection = mysql.connect(**config, database=database)
    cursor = connection.cursor()
    try:
        for statement in _split_sql(script):
            cursor.execute(statement)
        connection.commit()
        cursor.execute("SELECT MAX(version) FROM schema_version")
        assert cursor.fetchone()[0] == 3
        cursor.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=DATABASE() "
            "AND table_name IN (%s,%s,%s,%s,%s)",
            tuple(sorted(_HUB_TABLES)),
        )
        assert cursor.fetchone()[0] == 0
        cursor.execute(
            "SELECT COUNT(*) FROM information_schema.columns WHERE table_schema=DATABASE() "
            "AND table_name='tool_versions' AND column_name='requires_recipient_credentials'"
        )
        assert cursor.fetchone()[0] == 0
        cursor.execute("SELECT COUNT(*) FROM schema_migration_history WHERE version=4")
        assert cursor.fetchone()[0] == 0
    finally:
        cursor.close()
        connection.close()
