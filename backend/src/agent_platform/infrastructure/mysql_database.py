"""受控 MySQL 连接池及其生命周期。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class MySqlConfig:
    host: str
    port: int
    database: str
    user: str
    password: str
    pool_name: str = "agent_platform"
    pool_size: int = 8

    def __post_init__(self) -> None:
        for name in ("host", "database", "user", "pool_name"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} 不能为空")
        if not 1 <= self.port <= 65535:
            raise ValueError("MySQL 端口无效")
        if not 1 <= self.pool_size <= 64:
            raise ValueError("MySQL 连接池大小必须处于 1 到 64 之间")


class MySqlConnectionPool:
    """通过单个连接池工厂向仓储提供连接，并在应用关闭时释放全部连接。"""

    def __init__(self, config: MySqlConfig) -> None:
        try:
            from mysql.connector.pooling import MySQLConnectionPool
        except ImportError as error:
            raise RuntimeError(
                "MySQL 模式需要安装 backend optional dependency：pip install -e '.[mysql]'"
            ) from error
        self._pool = MySQLConnectionPool(
            pool_name=config.pool_name,
            pool_size=config.pool_size,
            host=config.host,
            port=config.port,
            database=config.database,
            user=config.user,
            password=config.password,
            autocommit=False,
            charset="utf8mb4",
            collation="utf8mb4_0900_ai_ci",
        )
        self._closed = False

    def connection(self) -> Any:
        if self._closed:
            raise RuntimeError("MySQL connection pool 已关闭")
        return self._pool.get_connection()

    def close(self) -> None:
        """销毁池内空闲连接；正在使用的连接会在归还时由 Connector 关闭。"""

        if self._closed:
            return
        self._closed = True
        self._pool._remove_connections()
