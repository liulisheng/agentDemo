from pathlib import Path


def test_agent_platform_migration_is_ordered_and_has_reversal() -> None:
    root = Path(__file__).parents[2]
    up = root / "docs/migrations/V003__agent_platform_foundation.sql"
    down = root / "docs/migrations/V003__agent_platform_foundation.down.sql"
    text = up.read_text()
    assert "platform_id" in text and "tenant_id" in text
    for table in ("digital_employees", "deployments", "model_routes", "platform_trace_spans", "usage_ledger"):
        assert f"CREATE TABLE {table}" in text
        assert f"DROP TABLE IF EXISTS {table}" in down.read_text()
