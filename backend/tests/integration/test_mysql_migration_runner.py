"""显式 migration runner 的 MySQL 8.4 disposable database 集成测试。"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

import pytest

mysql = pytest.importorskip("mysql.connector")


@pytest.fixture
def disposable_mysql_database():
    if os.environ.get("AGENT_PLATFORM_MYSQL_TEST_ALLOW_CREATE_DATABASE") != "1":
        pytest.skip("set AGENT_PLATFORM_MYSQL_TEST_ALLOW_CREATE_DATABASE=1 for disposable MySQL integration")
    database = f"agent_platform_runner_test_{secrets.token_hex(5)}"
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
    try:
        yield database, config
    finally:
        cleanup = mysql.connect(**config)
        cleanup_cursor = cleanup.cursor()
        try:
            cleanup_cursor.execute(f"DROP DATABASE IF EXISTS `{database}`")
            cleanup.commit()
        finally:
            cleanup_cursor.close()
            cleanup.close()


def test_runner_applies_chain_records_checksums_and_is_idempotent(disposable_mysql_database) -> None:
    from agent_platform.infrastructure.migrations import MigrationRunner, PreflightAction

    database, config = disposable_mysql_database
    runner = MigrationRunner(
        lambda: mysql.connect(**config, database=database),
        Path(__file__).parents[3] / "docs" / "migrations",
    )

    first = runner.apply()
    second = runner.apply()

    assert first.action is PreflightAction.APPLY_MIGRATIONS
    assert first.applied_migrations == ("V001", "V002", "V003")
    assert second.action is PreflightAction.UP_TO_DATE
    assert second.applied_migrations == ()
    connection = mysql.connect(**config, database=database)
    cursor = connection.cursor()
    try:
        cursor.execute("SELECT MAX(version) FROM schema_version")
        assert cursor.fetchone()[0] == 3
        cursor.execute("SELECT version FROM schema_migration_history ORDER BY version")
        assert [row[0] for row in cursor.fetchall()] == [1, 2, 3]
    finally:
        cursor.close()
        connection.close()


def test_runner_blocks_checksum_mismatch_before_any_write(disposable_mysql_database) -> None:
    from agent_platform.infrastructure.migrations import MigrationRunner, PreflightAction

    database, config = disposable_mysql_database
    runner = MigrationRunner(
        lambda: mysql.connect(**config, database=database),
        Path(__file__).parents[3] / "docs" / "migrations",
    )
    applied = runner.apply()
    assert applied.action is PreflightAction.APPLY_MIGRATIONS
    connection = mysql.connect(**config, database=database)
    cursor = connection.cursor()
    try:
        cursor.execute("UPDATE schema_migration_history SET checksum = %s WHERE version = 1", ("0" * 64,))
        connection.commit()
    finally:
        cursor.close()
        connection.close()

    result = runner.apply()

    assert result.action is PreflightAction.BLOCK_CHECKSUM_MISMATCH
    assert result.database_changed is False
    connection = mysql.connect(**config, database=database)
    cursor = connection.cursor()
    try:
        cursor.execute("SELECT MAX(version) FROM schema_version")
        assert cursor.fetchone()[0] == 3
    finally:
        cursor.close()
        connection.close()


def test_runner_failure_does_not_record_failed_migration(tmp_path: Path, disposable_mysql_database) -> None:
    from agent_platform.infrastructure.migrations import (
        MigrationPreflight,
        MigrationRunner,
        MySqlMigrationStateReader,
        PreflightAction,
        load_migration_checksums,
    )

    database, config = disposable_mysql_database
    project_root = Path(__file__).parents[3]
    (tmp_path / "V001__core_schema.sql").write_bytes((project_root / "docs/migrations/V001__core_schema.sql").read_bytes())
    (tmp_path / "V002__conversation_messages.sql").write_bytes((project_root / "docs/migrations/V002__conversation_messages.sql").read_bytes())
    (tmp_path / "V003__broken.sql").write_text(
        "CREATE TABLE digital_employees (platform_id CHAR(26) NOT NULL COMMENT '平台', "
        "tenant_id CHAR(26) NOT NULL COMMENT '租户', employee_id CHAR(26) NOT NULL COMMENT '员工', "
        "PRIMARY KEY (platform_id, tenant_id, employee_id)) COMMENT='员工'; "
        "INSERT INTO schema_version (version) VALUES (3); INVALID SQL;",
        encoding="utf-8",
    )
    runner = MigrationRunner(lambda: mysql.connect(**config, database=database), tmp_path)

    result = runner.apply()

    assert result.action is PreflightAction.MIGRATION_FAILED
    assert result.applied_migrations == ("V001", "V002")
    connection = mysql.connect(**config, database=database)
    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema = DATABASE() AND table_name = 'schema_migration_history'"
        )
        assert cursor.fetchone()[0] == 0
        cursor.execute("SELECT MAX(version) FROM schema_version")
        assert cursor.fetchone()[0] == 2
    finally:
        cursor.close()
        connection.close()
    baseline = MySqlMigrationStateReader(lambda: mysql.connect(**config, database=database)).read_baseline()
    plan = MigrationPreflight(load_migration_checksums(tmp_path)).assess(baseline)
    assert plan.action is PreflightAction.BLOCK_PARTIAL_MIGRATION
    assert plan.errors[0].startswith("partial_v003_schema:")


def test_runner_refuses_to_apply_when_another_migration_holds_database_lock(disposable_mysql_database) -> None:
    import hashlib

    from agent_platform.infrastructure.migrations import MigrationRunner, PreflightAction

    database, config = disposable_mysql_database
    lock_name = "agent-platform-migrate:" + hashlib.sha256(database.encode("utf-8")).hexdigest()[:32]
    lock_connection = mysql.connect(**config, database=database)
    lock_cursor = lock_connection.cursor()
    try:
        lock_cursor.execute("SELECT GET_LOCK(%s, 0)", (lock_name,))
        assert lock_cursor.fetchone()[0] == 1
        runner = MigrationRunner(
            lambda: mysql.connect(**config, database=database),
            Path(__file__).parents[3] / "docs" / "migrations",
            lock_timeout_seconds=1,
        )

        result = runner.apply()

        assert result.action is PreflightAction.MIGRATION_LOCKED
        assert result.database_changed is False
    finally:
        lock_cursor.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))
        lock_cursor.fetchone()
        lock_cursor.close()
        lock_connection.close()
