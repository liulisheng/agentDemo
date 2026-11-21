"""MySQL 连接池的时区和生命周期配置契约。"""

from __future__ import annotations

import pytest

mysql_pooling = pytest.importorskip("mysql.connector.pooling")

from agent_platform.infrastructure.mysql_database import MySqlConfig, MySqlConnectionPool


def test_mysql_connection_pool_sets_utc_session_timezone(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class RecordingPool:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

        def _remove_connections(self) -> None:
            pass

    monkeypatch.setattr(mysql_pooling, "MySQLConnectionPool", RecordingPool)
    pool = MySqlConnectionPool(
        MySqlConfig("127.0.0.1", 3306, "agent_platform", "agent", "unused")
    )

    assert captured["time_zone"] == "+00:00"
    pool.close()
