from agent_platform.infrastructure.migrations import (
    MigrationBaseline,
    MigrationPreflight,
    PreflightAction,
)

V003_OBJECTS = frozenset(
    {
        "digital_employees",
        "digital_employee_versions",
        "deployments",
        "model_routes",
        "platform_trace_spans",
        "usage_ledger",
    }
)


def test_empty_database_plans_full_ordered_chain_without_mutating_schema() -> None:
    result = MigrationPreflight().assess(MigrationBaseline(schema_version=None))

    assert result.action is PreflightAction.APPLY_MIGRATIONS
    assert result.migrations_to_apply == ("V001", "V002", "V003")
    assert result.database_changed is False


def test_v001_v002_database_plans_only_v003() -> None:
    result = MigrationPreflight().assess(MigrationBaseline(schema_version=2))

    assert result.action is PreflightAction.APPLY_MIGRATIONS
    assert result.migrations_to_apply == ("V003",)
    assert result.warnings == ("legacy_migration_checksums_unavailable",)


def test_current_v003_database_plans_v004_when_v004_migration_exists() -> None:
    from agent_platform.infrastructure.migrations import (
        V001_SENTINEL_OBJECTS,
        V002_SENTINEL_OBJECTS,
    )

    checksums = {1: "v1", 2: "v2", 3: "v3", 4: "v4"}
    baseline = MigrationBaseline(
        schema_version=3,
        applied_checksums={1: "v1", 2: "v2", 3: "v3"},
        schema_objects=V001_SENTINEL_OBJECTS | V002_SENTINEL_OBJECTS | V003_OBJECTS,
        schema_columns={"sessions": frozenset({"last_message_sequence"})},
        checksum_history_available=True,
        introspection_available=True,
    )

    result = MigrationPreflight(checksums).assess(baseline)

    assert result.action is PreflightAction.APPLY_MIGRATIONS
    assert result.migrations_to_apply == ("V004",)


def test_repository_migration_catalog_plans_forward_migrations_after_v003() -> None:
    from pathlib import Path

    from agent_platform.infrastructure.migrations import load_migration_checksums

    migration_dir = Path(__file__).parents[2] / "docs" / "migrations"
    checksums = load_migration_checksums(migration_dir)
    result = MigrationPreflight(checksums).assess(
        MigrationBaseline(
            schema_version=3,
            applied_checksums={version: checksum for version, checksum in checksums.items() if version <= 3},
            schema_objects=V003_OBJECTS,
        )
    )

    assert result.action is PreflightAction.APPLY_MIGRATIONS
    assert result.migrations_to_apply == ("V004", "V005")


def test_complete_v003_marker_without_checksum_requires_forward_v004() -> None:
    result = MigrationPreflight().assess(
        MigrationBaseline(schema_version=3, schema_objects=V003_OBJECTS)
    )

    assert result.action is PreflightAction.USE_FORWARD_MIGRATION
    assert result.migrations_to_apply == ("V004",)
    assert result.database_changed is False


def test_partial_v003_marker_fails_closed() -> None:
    result = MigrationPreflight().assess(
        MigrationBaseline(schema_version=2, schema_objects=frozenset({"digital_employees"}))
    )

    assert result.action is PreflightAction.BLOCK_PARTIAL_MIGRATION
    assert result.migrations_to_apply == ()
    assert result.errors[0].startswith("partial_v003_schema:")


def test_mismatched_applied_checksum_never_recommends_reapplying_migration() -> None:
    result = MigrationPreflight(expected_checksums={1: "expected-v1", 2: "expected-v2", 3: "expected-v3"}).assess(
        MigrationBaseline(
            schema_version=2,
            applied_checksums={1: "wrong-v1", 2: "expected-v2"},
        )
    )

    assert result.action is PreflightAction.BLOCK_CHECKSUM_MISMATCH
    assert result.migrations_to_apply == ()
    assert result.errors == ("checksum_mismatch:V001",)


def test_v003_checksum_mismatch_recommends_forward_v004() -> None:
    result = MigrationPreflight(expected_checksums={3: "current-v3"}).assess(
        MigrationBaseline(
            schema_version=3,
            applied_checksums={3: "previous-v3"},
            schema_objects=V003_OBJECTS,
        )
    )

    assert result.action is PreflightAction.USE_FORWARD_MIGRATION
    assert result.migrations_to_apply == ("V004",)
    assert "checksum_mismatch:V003" in result.warnings


def test_v004_database_with_complete_markers_and_checksums_is_up_to_date() -> None:
    from agent_platform.infrastructure.migrations import (
        V001_SENTINEL_OBJECTS,
        V002_SENTINEL_OBJECTS,
        V004_SENTINEL_OBJECTS,
    )

    checksums = {1: "v1", 2: "v2", 3: "v3", 4: "v4"}
    result = MigrationPreflight(checksums).assess(
        MigrationBaseline(
            schema_version=4,
            applied_checksums=checksums,
            schema_objects=V001_SENTINEL_OBJECTS | V002_SENTINEL_OBJECTS | V003_OBJECTS | V004_SENTINEL_OBJECTS,
            schema_columns={"sessions": frozenset({"last_message_sequence"})},
            checksum_history_available=True,
            introspection_available=True,
        )
    )

    assert result.action is PreflightAction.UP_TO_DATE


def test_v004_database_plans_v005_file_package_migration() -> None:
    from agent_platform.infrastructure.migrations import (
        V001_SENTINEL_OBJECTS,
        V002_SENTINEL_OBJECTS,
        V004_SENTINEL_OBJECTS,
    )

    result = MigrationPreflight({version: f"v{version}" for version in range(1, 6)}).assess(
        MigrationBaseline(
            schema_version=4,
            applied_checksums={version: f"v{version}" for version in range(1, 5)},
            schema_objects=V001_SENTINEL_OBJECTS | V002_SENTINEL_OBJECTS | V003_OBJECTS | V004_SENTINEL_OBJECTS,
            schema_columns={"sessions": frozenset({"last_message_sequence"})},
            checksum_history_available=True,
            introspection_available=True,
        )
    )

    assert result.action is PreflightAction.APPLY_MIGRATIONS
    assert result.migrations_to_apply == ("V005",)


def test_partial_v005_file_package_schema_blocks_migration() -> None:
    from agent_platform.infrastructure.migrations import (
        V001_SENTINEL_OBJECTS,
        V002_SENTINEL_OBJECTS,
        V004_SENTINEL_OBJECTS,
        V005_SENTINEL_OBJECTS,
    )

    result = MigrationPreflight({version: f"v{version}" for version in range(1, 6)}).assess(
        MigrationBaseline(
            schema_version=4,
            schema_objects=(
                V001_SENTINEL_OBJECTS
                | V002_SENTINEL_OBJECTS
                | V003_OBJECTS
                | V004_SENTINEL_OBJECTS
                | V005_SENTINEL_OBJECTS
            ),
            schema_columns={
                "sessions": frozenset({"last_message_sequence"}),
                "skill_versions": frozenset({"content_format", "entry_path"}),
            },
            introspection_available=True,
        )
    )

    assert result.action is PreflightAction.BLOCK_PARTIAL_MIGRATION
    assert result.errors[0].startswith("partial_v005_schema:")


def test_v005_database_with_complete_markers_and_checksums_is_up_to_date() -> None:
    from agent_platform.infrastructure.migrations import (
        V001_SENTINEL_OBJECTS,
        V002_SENTINEL_OBJECTS,
        V004_SENTINEL_OBJECTS,
        V005_SENTINEL_OBJECTS,
        V005_SKILL_COLUMNS,
    )

    checksums = {version: f"v{version}" for version in range(1, 6)}
    result = MigrationPreflight(checksums).assess(
        MigrationBaseline(
            schema_version=5,
            applied_checksums=checksums,
            schema_objects=(
                V001_SENTINEL_OBJECTS
                | V002_SENTINEL_OBJECTS
                | V003_OBJECTS
                | V004_SENTINEL_OBJECTS
                | V005_SENTINEL_OBJECTS
            ),
            schema_columns={
                "sessions": frozenset({"last_message_sequence"}),
                "skill_versions": V005_SKILL_COLUMNS,
            },
            checksum_history_available=True,
            introspection_available=True,
        )
    )

    assert result.action is PreflightAction.UP_TO_DATE


def test_partial_v004_objects_block_next_migration() -> None:
    from agent_platform.infrastructure.migrations import V004_SENTINEL_OBJECTS

    result = MigrationPreflight({1: "v1", 2: "v2", 3: "v3", 4: "v4"}).assess(
        MigrationBaseline(schema_version=3, schema_objects=frozenset({"hub_resources"}))
    )

    assert result.action is PreflightAction.BLOCK_PARTIAL_MIGRATION
    assert result.errors[0].startswith("partial_v004_schema:")
    assert V004_SENTINEL_OBJECTS


def test_mysql_state_reader_uses_select_only_and_closes_connection() -> None:
    from agent_platform.infrastructure.migrations import MySqlMigrationStateReader

    class Cursor:
        def __init__(self) -> None:
            self.queries: list[str] = []
            self._result = []
            self.closed = False

        def execute(self, query: str) -> None:
            self.queries.append(query)
            if "information_schema.tables" in query:
                self._result = [("schema_version",)]
            elif "information_schema.columns" in query:
                self._result = [("sessions", "last_message_sequence")]
            elif "MAX(version)" in query:
                self._result = [(2,)]
            else:
                raise AssertionError(f"unexpected query: {query}")

        def fetchall(self):
            return self._result

        def fetchone(self):
            return self._result[0]

        def close(self) -> None:
            self.closed = True

    class Connection:
        def __init__(self) -> None:
            self.db_cursor = Cursor()
            self.closed = False

        def cursor(self):
            return self.db_cursor

        def close(self) -> None:
            self.closed = True

    connection = Connection()
    reader = MySqlMigrationStateReader(lambda: connection)

    baseline = reader.read_baseline()

    assert baseline.schema_version == 2
    assert baseline.checksum_history_available is False
    assert all(query.lstrip().upper().startswith("SELECT") for query in connection.db_cursor.queries)
    assert connection.db_cursor.closed is True
    assert connection.closed is True


def test_migration_checksum_loader_ignores_rollback_files(tmp_path) -> None:
    import hashlib

    from agent_platform.infrastructure.migrations import load_migration_checksums

    migration = tmp_path / "V003__control_plane.sql"
    migration.write_text("SELECT 3;", encoding="utf-8")
    (tmp_path / "V003__control_plane.down.sql").write_text("SELECT 2;", encoding="utf-8")

    checksums = load_migration_checksums(tmp_path)

    assert checksums == {3: hashlib.sha256(b"SELECT 3;").hexdigest()}


def test_preflight_cli_reports_plan_without_running_migrations(tmp_path, capsys) -> None:
    from agent_platform.infrastructure.migrations import (
        V001_SENTINEL_OBJECTS,
        V002_SENTINEL_OBJECTS,
        main,
    )

    class Cursor:
        def __init__(self) -> None:
            self._result = []
            self.queries: list[str] = []

        def execute(self, query: str) -> None:
            self.queries.append(query)
            if "information_schema.tables" in query:
                self._result = [(name,) for name in sorted(V001_SENTINEL_OBJECTS | V002_SENTINEL_OBJECTS)]
            else:
                self._result = [("sessions", "last_message_sequence")]

        def fetchall(self):
            return self._result

        def fetchone(self):
            if self._result and self._result[0] == ("sessions", "last_message_sequence"):
                return (2,)
            return self._result[0]

        def close(self) -> None:
            return None

    class Connection:
        def __init__(self) -> None:
            self.db_cursor = Cursor()

        def cursor(self):
            return self.db_cursor

        def close(self) -> None:
            return None

    connection = Connection()
    (tmp_path / "V001__core.sql").write_text("SELECT 1;", encoding="utf-8")
    (tmp_path / "V002__messages.sql").write_text("SELECT 2;", encoding="utf-8")
    (tmp_path / "V003__platform.sql").write_text("SELECT 3;", encoding="utf-8")

    exit_code = main(["preflight", "--migrations-dir", str(tmp_path)], connection_factory=lambda: connection)

    output = capsys.readouterr().out
    assert exit_code == 0
    assert '"migrations_to_apply": ["V003"]' in output
    assert all(query.lstrip().upper().startswith("SELECT") for query in connection.db_cursor.queries)


def test_schema_version_three_without_v003_objects_is_inconsistent() -> None:
    result = MigrationPreflight(expected_checksums={1: "v1", 2: "v2", 3: "v3"}).assess(
        MigrationBaseline(schema_version=3)
    )

    assert result.action is PreflightAction.BLOCK_INCONSISTENT_SCHEMA
    assert result.errors == ("schema_version_ahead_of_v003_objects",)


def test_checksum_mismatch_at_current_version_fails_closed() -> None:
    result = MigrationPreflight(expected_checksums={1: "v1", 2: "v2", 3: "v3", 4: "v4"}).assess(
        MigrationBaseline(
            schema_version=4,
            applied_checksums={3: "v3", 4: "wrong-v4"},
            schema_objects=V003_OBJECTS,
        )
    )

    assert result.action is PreflightAction.BLOCK_CHECKSUM_MISMATCH
    assert result.errors == ("checksum_mismatch:V004",)


def test_introspected_v002_database_missing_required_table_blocks_migration() -> None:
    from agent_platform.infrastructure.migrations import (
        V001_SENTINEL_OBJECTS,
        V002_SENTINEL_OBJECTS,
    )

    baseline = MigrationBaseline(
        schema_version=2,
        schema_objects=V001_SENTINEL_OBJECTS | V002_SENTINEL_OBJECTS.difference({"message_chunks"}),
        schema_columns={"sessions": frozenset({"last_message_sequence"})},
        introspection_available=True,
    )
    result = MigrationPreflight().assess(baseline)

    assert result.action is PreflightAction.BLOCK_PARTIAL_MIGRATION
    assert result.errors[0].startswith("partial_v002_schema:")


def test_introspected_v002_database_with_complete_foundation_plans_v003() -> None:
    from agent_platform.infrastructure.migrations import (
        V001_SENTINEL_OBJECTS,
        V002_SENTINEL_OBJECTS,
    )

    baseline = MigrationBaseline(
        schema_version=2,
        schema_objects=V001_SENTINEL_OBJECTS | V002_SENTINEL_OBJECTS,
        schema_columns={"sessions": frozenset({"last_message_sequence"})},
        introspection_available=True,
    )
    result = MigrationPreflight().assess(baseline)

    assert result.action is PreflightAction.APPLY_MIGRATIONS
    assert result.migrations_to_apply == ("V003",)


def test_module_cli_help_starts_without_resolving_runner_before_module_load() -> None:
    import subprocess
    import sys
    from pathlib import Path

    backend_root = Path(__file__).parents[1]
    result = subprocess.run(
        [sys.executable, "-m", "agent_platform.infrastructure.migrations", "--help"],
        cwd=backend_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "preflight" in result.stdout
    assert "apply" in result.stdout
