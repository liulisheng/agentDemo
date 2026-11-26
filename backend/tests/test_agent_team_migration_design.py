from pathlib import Path


def test_v011_team_orchestration_migration_has_all_tables_and_comments() -> None:
    root = Path(__file__).parents[2]
    text = (root / "docs/migrations/V011__agent_team_orchestration.sql").read_text(encoding="utf-8")
    rollback = (root / "docs/migrations/V011__agent_team_orchestration.down.sql").read_text(encoding="utf-8")

    for table in (
        "teams",
        "team_versions",
        "team_members",
        "conversation_team_bindings",
        "orchestration_runs",
        "orchestration_tasks",
        "orchestration_task_dependencies",
        "orchestration_task_attempts",
        "orchestration_task_events",
        "execution_artifacts",
        "artifact_links",
        "artifact_deliveries",
    ):
        assert f"CREATE TABLE {table}" in text
        assert f"DROP TABLE IF EXISTS {table}" in rollback
    assert "ADD COLUMN conversation_mode" in text
    assert "DEFAULT 'SINGLE_AGENT'" in text
    assert "ADD COLUMN team_member_id" in text
    assert "ADD COLUMN capabilities_snapshot_json" in text
    assert "ADD COLUMN orchestration_id" in text
    assert "COMMENT='" in text
    assert "INSERT INTO schema_version (version) VALUES (11)" in text
    assert "DELETE FROM schema_version WHERE version = 11" in rollback


def test_v011_down_migration_removes_forward_columns() -> None:
    root = Path(__file__).parents[2]
    rollback = (root / "docs/migrations/V011__agent_team_orchestration.down.sql").read_text(encoding="utf-8")

    assert "DROP COLUMN conversation_mode" in rollback
    assert "DROP COLUMN orchestration_id" in rollback
    assert "DROP COLUMN task_id" in rollback


def test_v012_attempt_lease_migration_documents_worker_and_fencing_columns() -> None:
    root = Path(__file__).parents[2]
    migration = (root / "docs/migrations/V012__team_attempt_leases.sql").read_text(encoding="utf-8")
    rollback = (root / "docs/migrations/V012__team_attempt_leases.down.sql").read_text(encoding="utf-8")

    assert "ADD COLUMN worker_id" in migration and "COMMENT '当前领取该 Attempt 的 Worker 标识'" in migration
    assert "ADD COLUMN fencing_token" in migration and "COMMENT 'Attempt 租约围栏序号" in migration
    assert "INSERT INTO schema_version (version) VALUES (12)" in migration
    assert "DROP COLUMN worker_id" in rollback and "DROP COLUMN fencing_token" in rollback


def test_v013_allows_multiple_team_member_sessions_per_employee() -> None:
    root = Path(__file__).parents[2]
    migration = (root / "docs/migrations/V013__conversation_agent_binding_cardinality.sql").read_text(encoding="utf-8")
    rollback = (root / "docs/migrations/V013__conversation_agent_binding_cardinality.down.sql").read_text(encoding="utf-8")

    assert "single_agent_employee_id" in migration
    assert "COMMENT '单员工 Conversation 的唯一员工绑定键" in migration
    assert "uk_conversation_team_member" in migration
    assert "INSERT INTO schema_version (version) VALUES (13)" in migration
    assert "恢复旧唯一索引会失败" in rollback
