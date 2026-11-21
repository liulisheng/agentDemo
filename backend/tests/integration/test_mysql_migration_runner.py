"""显式 migration runner 的 MySQL 8.4 disposable database 集成测试。"""

from __future__ import annotations

import os
import secrets
import shutil
from pathlib import Path

import pytest

mysql = pytest.importorskip("mysql.connector")


class _RedactedMySqlConfig(dict[str, object]):
    """让 pytest 失败回溯也不打印本地 MySQL 密码。"""

    def __repr__(self) -> str:
        return "MySQL test connection config(<redacted>)"


def _has_chinese(value: str | None) -> bool:
    """确认 information_schema 注释包含可见中文字符。"""

    return isinstance(value, str) and any("\u4e00" <= character <= "\u9fff" for character in value)


@pytest.fixture
def disposable_mysql_database():
    if os.environ.get("AGENT_PLATFORM_MYSQL_TEST_ALLOW_CREATE_DATABASE") != "1":
        pytest.skip("set AGENT_PLATFORM_MYSQL_TEST_ALLOW_CREATE_DATABASE=1 for disposable MySQL integration")
    database = f"agent_platform_runner_test_{secrets.token_hex(5)}"
    config = _RedactedMySqlConfig({
        "host": os.environ.get("MYSQL_HOST", "127.0.0.1"),
        "port": int(os.environ.get("MYSQL_PORT", "3306")),
        "user": os.environ.get("MYSQL_USER", "root"),
        "password": os.environ.get("MYSQL_PASSWORD", ""),
    })
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
    assert first.applied_migrations == (
        "V001", "V002", "V003", "V004", "V005", "V006", "V007", "V008"
    )
    assert second.action is PreflightAction.UP_TO_DATE
    assert second.applied_migrations == ()
    connection = mysql.connect(**config, database=database)
    cursor = connection.cursor()
    try:
        cursor.execute("SELECT MAX(version) FROM schema_version")
        assert cursor.fetchone()[0] == 8
        cursor.execute("SELECT version FROM schema_migration_history ORDER BY version")
        assert [row[0] for row in cursor.fetchall()] == [1, 2, 3, 4, 5, 6, 7, 8]
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
        assert cursor.fetchone()[0] == 8
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


def test_v006_schema_has_tenant_keys_and_chinese_comments(disposable_mysql_database) -> None:
    from agent_platform.infrastructure.migrations import MigrationRunner

    database, config = disposable_mysql_database
    MigrationRunner(
        lambda: mysql.connect(**config, database=database),
        Path(__file__).parents[3] / "docs" / "migrations",
    ).apply()
    connection = mysql.connect(**config, database=database)
    cursor = connection.cursor()
    try:
        expected_tables = {
            "mcp_server_profiles",
            "tool_approval_requests",
            "tool_versions",
            "runs",
        }
        table_placeholders = ",".join(["%s"] * len(expected_tables))
        cursor.execute(
            "SELECT table_name, table_comment FROM information_schema.tables "
            "WHERE table_schema = DATABASE() AND table_name IN (" + table_placeholders + ")",
            tuple(sorted(expected_tables)),
        )
        tables = {name: comment for name, comment in cursor.fetchall()}
        assert expected_tables == tables.keys()
        assert all(_has_chinese(comment) for comment in tables.values())

        expected_columns = {
            "mcp_server_profiles": {
                "profile_id", "version", "transport", "command", "args_json",
                "working_directory_ref", "environment_secret_refs_json",
                "enabled_tool_names_json", "tool_catalog_sha256", "status",
            },
            "tool_approval_requests": {
                "approval_id", "run_id", "requested_by", "approved_by", "rejected_by",
                "tool_invocation_id", "arguments_ref", "arguments_sha256",
                "preview_ref", "preview_sha256", "confirmation_idempotency_key",
                "active_run_id", "state", "worker_id",
                "fencing_token", "lease_expires_at",
            },
            "tool_versions": {
                "mcp_server_profile_id", "mcp_server_profile_version",
                "remote_tool_name", "input_schema_sha256", "preview_tool_name",
                "mcp_risk_level", "idempotency_support",
            },
            "runs": {"submitted_by_user_id"},
        }
        for table, required in expected_columns.items():
            cursor.execute(
                "SELECT column_name, column_comment FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = %s",
                (table,),
            )
            columns = dict(cursor.fetchall())
            assert required <= columns.keys()
            comment_columns = columns if table in {
                "mcp_server_profiles", "tool_approval_requests"
            } else {column: columns[column] for column in required}
            assert all(_has_chinese(comment) for comment in comment_columns.values())

        assert "MCP 调用配置" in tables["tool_versions"]
        assert "审批等待状态" in tables["runs"]

        cursor.execute(
            "SELECT column_name FROM information_schema.statistics "
            "WHERE table_schema = DATABASE() AND table_name = 'runs' "
            "AND index_name = 'idx_run_platform_claim' ORDER BY seq_in_index"
        )
        assert tuple(row[0] for row in cursor.fetchall()) == (
            "platform_id", "state", "created_at", "run_id",
        )

        cursor.execute(
            "SELECT CHECK_CLAUSE FROM information_schema.check_constraints "
            "WHERE constraint_schema = DATABASE() AND constraint_name = 'ck_run_state'"
        )
        assert "AWAITING_APPROVAL" in cursor.fetchone()[0]
    finally:
        cursor.close()
        connection.close()


def test_v006_down_restores_the_v005_schema_boundary(
    tmp_path: Path, disposable_mysql_database
) -> None:
    from agent_platform.infrastructure.migrations import MigrationRunner, split_sql_statements

    database, config = disposable_mysql_database
    migration_directory = Path(__file__).parents[3] / "docs" / "migrations"
    v006_migrations = tmp_path / "migrations-v006"
    v006_migrations.mkdir()
    for version in range(1, 7):
        migration = next(
            path
            for path in migration_directory.glob(f"V{version:03d}__*.sql")
            if ".down." not in path.name
        )
        shutil.copyfile(migration, v006_migrations / migration.name)
    MigrationRunner(
        lambda: mysql.connect(**config, database=database), v006_migrations
    ).apply()
    connection = mysql.connect(**config, database=database)
    cursor = connection.cursor()
    try:
        rollback = migration_directory.joinpath(
            "V006__mcp_profiles_and_approvals.down.sql"
        ).read_text(encoding="utf-8")
        for statement in split_sql_statements(rollback):
            cursor.execute(statement)
        connection.commit()

        cursor.execute("SELECT MAX(version) FROM schema_version")
        assert cursor.fetchone()[0] == 5
        cursor.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=DATABASE() "
            "AND table_name IN ('mcp_server_profiles','tool_approval_requests')"
        )
        assert cursor.fetchone()[0] == 0
        cursor.execute(
            "SELECT COUNT(*) FROM information_schema.columns WHERE table_schema=DATABASE() "
            "AND ((table_name='runs' AND column_name='submitted_by_user_id') OR "
            "(table_name='tool_versions' AND column_name='mcp_server_profile_id'))"
        )
        assert cursor.fetchone()[0] == 0
        cursor.execute(
            "SELECT COUNT(*) FROM information_schema.statistics WHERE table_schema=DATABASE() "
            "AND table_name='runs' AND index_name IN "
            "('idx_run_session_latest','idx_run_platform_claim')"
        )
        assert cursor.fetchone()[0] == 0
        cursor.execute(
            "SELECT CHECK_CLAUSE FROM information_schema.check_constraints "
            "WHERE constraint_schema=DATABASE() AND constraint_name='ck_run_state'"
        )
        assert "AWAITING_APPROVAL" not in cursor.fetchone()[0]
        cursor.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=DATABASE() "
            "AND table_name='skill_file_entries'"
        )
        assert cursor.fetchone()[0] == 1
    finally:
        cursor.close()
        connection.close()


def test_v007_schema_has_tenant_keys_and_chinese_comments(disposable_mysql_database) -> None:
    from agent_platform.infrastructure.migrations import MigrationRunner

    database, config = disposable_mysql_database
    MigrationRunner(
        lambda: mysql.connect(**config, database=database),
        Path(__file__).parents[3] / "docs" / "migrations",
    ).apply()
    connection = mysql.connect(**config, database=database)
    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT table_comment FROM information_schema.tables "
            "WHERE table_schema=DATABASE() AND table_name='session_runtime_leases'"
        )
        table_comment = cursor.fetchone()[0]
        assert _has_chinese(table_comment)
        cursor.execute(
            "SELECT column_name,column_comment FROM information_schema.columns "
            "WHERE table_schema=DATABASE() AND table_name='session_runtime_leases'"
        )
        columns = dict(cursor.fetchall())
        assert set(columns) == {
            "platform_id", "tenant_id", "session_id", "run_id", "worker_id",
            "fencing_token", "lease_expires_at", "state", "created_at", "updated_at",
        }
        assert all(_has_chinese(comment) for comment in columns.values())
        cursor.execute(
            "SELECT constraint_name,table_name,column_name,referenced_table_name "
            "FROM information_schema.key_column_usage WHERE table_schema=DATABASE() "
            "AND table_name='session_runtime_leases' AND referenced_table_name IS NOT NULL "
            "ORDER BY constraint_name,ordinal_position"
        )
        references = {(name, table, column, target) for name, table, column, target in cursor.fetchall()}
        assert {
            ("fk_session_runtime_lease_session", "session_runtime_leases", "session_id", "sessions"),
            ("fk_session_runtime_lease_run", "session_runtime_leases", "run_id", "runs"),
        } <= references
        cursor.execute("SELECT MAX(version) FROM schema_version")
        assert cursor.fetchone()[0] == 8
    finally:
        cursor.close()
        connection.close()


def test_v007_down_restores_the_v006_schema_boundary(
    tmp_path: Path, disposable_mysql_database
) -> None:
    from agent_platform.infrastructure.migrations import MigrationRunner, split_sql_statements

    database, config = disposable_mysql_database
    migration_directory = Path(__file__).parents[3] / "docs" / "migrations"
    v007_migrations = tmp_path / "migrations-v007"
    v007_migrations.mkdir()
    for version in range(1, 8):
        migration = next(
            path
            for path in migration_directory.glob(f"V{version:03d}__*.sql")
            if ".down." not in path.name
        )
        shutil.copyfile(migration, v007_migrations / migration.name)
    MigrationRunner(
        lambda: mysql.connect(**config, database=database), v007_migrations
    ).apply()
    connection = mysql.connect(**config, database=database)
    cursor = connection.cursor()
    try:
        rollback = migration_directory.joinpath(
            "V007__session_runtime_leases.down.sql"
        ).read_text(encoding="utf-8")
        for statement in split_sql_statements(rollback):
            cursor.execute(statement)
        connection.commit()
        cursor.execute("SELECT MAX(version) FROM schema_version")
        assert cursor.fetchone()[0] == 6
        cursor.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=DATABASE() "
            "AND table_name='session_runtime_leases'"
        )
        assert cursor.fetchone()[0] == 0
        cursor.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=DATABASE() "
            "AND table_name IN ('mcp_server_profiles','tool_approval_requests')"
        )
        assert cursor.fetchone()[0] == 2
    finally:
        cursor.close()
        connection.close()

def test_v007_backfills_an_inflight_session_home_before_worker_upgrade(
    tmp_path: Path, disposable_mysql_database
) -> None:
    from agent_platform.domain.common import generate_database_id
    from agent_platform.infrastructure.migrations import MigrationRunner

    database, config = disposable_mysql_database
    migration_directory = Path(__file__).parents[3] / "docs" / "migrations"
    v006_directory = tmp_path / "migrations-v006"
    v006_directory.mkdir()
    v007_directory = tmp_path / "migrations-v007"
    v007_directory.mkdir()
    for version in range(1, 8):
        migration = next(
            path
            for path in migration_directory.glob(f"V{version:03d}__*.sql")
            if ".down." not in path.name
        )
        shutil.copyfile(migration, v007_directory / migration.name)
        if version < 7:
            shutil.copyfile(migration, v006_directory / migration.name)
    connection_factory = lambda: mysql.connect(**config, database=database)
    MigrationRunner(connection_factory, v006_directory).apply()

    session_id = generate_database_id()
    run_id = generate_database_id()
    connection = connection_factory()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "INSERT INTO platforms (platform_id,code,name,status) VALUES "
            "('plt-local','local','本地平台','ACTIVE')"
        )
        cursor.execute(
            "INSERT INTO tenants (platform_id,tenant_id,code,name,status) VALUES "
            "('plt-local','tenant-a','tenant-a','租户 A','ACTIVE')"
        )
        cursor.execute(
            "INSERT INTO users "
            "(platform_id,tenant_id,user_id,external_subject,username,display_name,status) "
            "VALUES ('plt-local','tenant-a','admin-local','subject-local','admin-local','管理员','ACTIVE')"
        )
        cursor.execute(
            "INSERT INTO sessions "
            "(platform_id,tenant_id,session_id,user_id,engine_name,engine_session_id,status,metadata_json) "
            "VALUES ('plt-local','tenant-a',%s,'admin-local','deepseek','legacy-engine-session','ACTIVE','{}')",
            (session_id,),
        )
        cursor.execute(
            "INSERT INTO runs "
            "(platform_id,tenant_id,run_id,session_id,idempotency_key,request_digest,state,"
            "worker_id,fencing_token,lease_expires_at,submitted_by_user_id) "
            "VALUES ('plt-local','tenant-a',%s,%s,'legacy-inflight',%s,'RUNNING','legacy-worker',"
            "12,TIMESTAMPADD(SECOND,60,CURRENT_TIMESTAMP(6)),'admin-local')",
            (run_id, session_id, "a" * 64),
        )
        connection.commit()
    finally:
        cursor.close()
        connection.close()


    MigrationRunner(connection_factory, v007_directory).apply()
    connection = connection_factory()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT session_id,run_id,worker_id,fencing_token,state "
            "FROM session_runtime_leases WHERE platform_id='plt-local' AND tenant_id='tenant-a'"
        )
        assert cursor.fetchone() == (
            session_id,
            run_id,
            "legacy-worker",
            12,
            "ACTIVE",
        )
    finally:
        cursor.close()
        connection.close()


def test_v008_model_management_schema_has_tenant_keys_and_chinese_comments(
    disposable_mysql_database,
) -> None:
    from agent_platform.infrastructure.migrations import MigrationRunner

    database, config = disposable_mysql_database
    MigrationRunner(
        lambda: mysql.connect(**config, database=database),
        Path(__file__).parents[3] / "docs" / "migrations",
    ).apply()
    connection = mysql.connect(**config, database=database)
    cursor = connection.cursor()
    try:
        expected_tables = {
            "model_connections",
            "model_catalog_items",
            "model_test_records",
        }
        cursor.execute(
            "SELECT table_name,table_comment FROM information_schema.tables "
            "WHERE table_schema=DATABASE() AND table_name IN "
            "('model_connections','model_catalog_items','model_test_records')"
        )
        table_comments = dict(cursor.fetchall())
        assert set(table_comments) == expected_tables
        assert all(_has_chinese(comment) for comment in table_comments.values())

        cursor.execute(
            "SELECT table_name,column_name,column_comment FROM information_schema.columns "
            "WHERE table_schema=DATABASE() AND table_name IN "
            "('model_connections','model_catalog_items','model_test_records')"
        )
        comments_by_table: dict[str, dict[str, str]] = {}
        for table_name, column_name, comment in cursor.fetchall():
            comments_by_table.setdefault(table_name, {})[column_name] = comment
        assert set(comments_by_table) == expected_tables
        assert all(
            _has_chinese(comment)
            for columns in comments_by_table.values()
            for comment in columns.values()
        )
        cursor.execute(
            "SELECT column_name,column_comment FROM information_schema.columns "
            "WHERE table_schema=DATABASE() AND table_name='model_routes' "
            "AND column_name IN ('model_connection_id','catalog_model_id','route_version')"
        )
        route_comments = dict(cursor.fetchall())
        assert set(route_comments) == {"model_connection_id", "catalog_model_id", "route_version"}
        assert all(_has_chinese(comment) for comment in route_comments.values())

        cursor.execute(
            "SELECT table_name,column_name,referenced_table_name,referenced_column_name "
            "FROM information_schema.key_column_usage WHERE table_schema=DATABASE() "
            "AND referenced_table_name IS NOT NULL AND table_name IN "
            "('model_catalog_items','model_routes','model_test_records')"
        )
        foreign_keys = set(cursor.fetchall())
        assert ("model_catalog_items", "connection_id", "model_connections", "connection_id") in foreign_keys
        assert ("model_routes", "model_connection_id", "model_connections", "connection_id") in foreign_keys
        assert ("model_routes", "catalog_model_id", "model_catalog_items", "model_id") in foreign_keys
        assert ("model_test_records", "connection_id", "model_connections", "connection_id") in foreign_keys

        cursor.execute("SELECT MAX(version) FROM schema_version")
        assert cursor.fetchone()[0] == 8
    finally:
        cursor.close()
        connection.close()


def test_v008_backfills_multiple_legacy_routes_to_one_tenant_connection(
    tmp_path: Path, disposable_mysql_database
) -> None:
    from agent_platform.infrastructure.migrations import MigrationRunner

    database, config = disposable_mysql_database
    project_migrations = Path(__file__).parents[3] / "docs" / "migrations"
    v007_migrations = tmp_path / "migrations-v007"
    v007_migrations.mkdir()
    for version in range(1, 8):
        migration = next(
            path
            for path in project_migrations.glob(f"V{version:03d}__*.sql")
            if ".down." not in path.name
        )
        shutil.copyfile(migration, v007_migrations / migration.name)
    connection_factory = lambda: mysql.connect(**config, database=database)
    MigrationRunner(connection_factory, v007_migrations).apply()

    connection = connection_factory()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "INSERT INTO platforms (platform_id,code,name,status) VALUES "
            "('plt-local','local','本地平台','ACTIVE')"
        )
        cursor.execute(
            "INSERT INTO tenants (platform_id,tenant_id,code,name,status) VALUES "
            "('plt-local','tenant-a','tenant-a','租户 A','ACTIVE')"
        )
        cursor.executemany(
            "INSERT INTO model_routes (platform_id,tenant_id,route_id,alias,provider,model_name,"
            "endpoint,protocol,auth_secret_ref,auth_secret_scope,auth_secret_provider,parameters_json,status) "
            "VALUES ('plt-local','tenant-a',%s,%s,'openai-compatible',%s,'http://host.docker.internal:63072/v1',"
            "'openai-chat-completions','local-secret','tenant','environment',JSON_OBJECT(),'ACTIVE')",
            (
                ("route-chat", "chat", "chat-model"),
                ("route-reasoning", "reasoning", "reasoning-model"),
            ),
        )
        connection.commit()
    finally:
        cursor.close()
        connection.close()

    MigrationRunner(connection_factory, project_migrations).apply()
    connection = connection_factory()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT COUNT(DISTINCT model_connection_id),COUNT(DISTINCT catalog_model_id) "
            "FROM model_routes WHERE platform_id='plt-local' AND tenant_id='tenant-a'"
        )
        assert cursor.fetchone() == (1, 2)
        cursor.execute(
            "SELECT c.endpoint,c.auth_secret_ref,c.version,r.route_version "
            "FROM model_routes r JOIN model_connections c ON c.platform_id=r.platform_id "
            "AND c.tenant_id=r.tenant_id AND c.connection_id=r.model_connection_id "
            "WHERE r.platform_id='plt-local' AND r.tenant_id='tenant-a' ORDER BY r.route_id"
        )
        rows = cursor.fetchall()
        assert rows == [
            ("http://host.docker.internal:63072/v1", "local-secret", 1, 1),
            ("http://host.docker.internal:63072/v1", "local-secret", 1, 1),
        ]
        cursor.execute(
            "SELECT model_id FROM model_catalog_items WHERE platform_id='plt-local' "
            "AND tenant_id='tenant-a' AND connection_id=(SELECT model_connection_id FROM model_routes "
            "WHERE route_id='route-chat') ORDER BY model_id"
        )
        assert [row[0] for row in cursor.fetchall()] == ["chat-model", "reasoning-model"]
        cursor.execute("SELECT MAX(version) FROM schema_version")
        assert cursor.fetchone()[0] == 8
    finally:
        cursor.close()
        connection.close()


def test_v008_down_restores_the_v007_schema_boundary(disposable_mysql_database) -> None:
    from agent_platform.infrastructure.migrations import MigrationRunner, split_sql_statements

    database, config = disposable_mysql_database
    migration_directory = Path(__file__).parents[3] / "docs" / "migrations"
    connection_factory = lambda: mysql.connect(**config, database=database)
    MigrationRunner(connection_factory, migration_directory).apply()
    connection = connection_factory()
    cursor = connection.cursor()
    try:
        rollback = migration_directory.joinpath(
            "V008__tenant_model_management.down.sql"
        ).read_text(encoding="utf-8")
        for statement in split_sql_statements(rollback):
            cursor.execute(statement)
        connection.commit()
        cursor.execute("SELECT MAX(version) FROM schema_version")
        assert cursor.fetchone()[0] == 7
        cursor.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=DATABASE() "
            "AND table_name IN ('model_connections','model_catalog_items','model_test_records')"
        )
        assert cursor.fetchone()[0] == 0
        cursor.execute(
            "SELECT COUNT(*) FROM information_schema.columns WHERE table_schema=DATABASE() "
            "AND table_name='model_routes' AND column_name IN "
            "('model_connection_id','catalog_model_id','route_version')"
        )
        assert cursor.fetchone()[0] == 0
        cursor.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=DATABASE() "
            "AND table_name='session_runtime_leases'"
        )
        assert cursor.fetchone()[0] == 1
    finally:
        cursor.close()
        connection.close()
