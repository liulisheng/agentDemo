"""显式执行一轮 MySQL-backed LocalObjectStore 垃圾回收。"""

from __future__ import annotations

import os
from pathlib import Path

from agent_platform.infrastructure.mysql_database import MySqlConfig, MySqlConnectionPool
from agent_platform.infrastructure.mysql_storage import MySqlMediaAssetRepository
from agent_platform.ports.storage import LocalObjectStore


def main() -> int:
    """作为 one-shot Worker 命令运行；API 启动不会隐式触发 GC。"""

    required = ("MYSQL_HOST", "MYSQL_DATABASE", "MYSQL_USER", "MYSQL_PASSWORD")
    missing = [name for name in required if name not in os.environ]
    if missing:
        raise RuntimeError("GC Worker 缺少 MySQL 环境变量：" + ", ".join(missing))
    config = MySqlConfig(
        host=os.environ["MYSQL_HOST"],
        port=int(os.environ.get("MYSQL_PORT", "3306")),
        database=os.environ["MYSQL_DATABASE"],
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
    )
    data_root = Path(
        os.environ.get(
            "AGENT_PLATFORM_DATA_ROOT",
            str(Path.home() / ".local" / "share" / "agent-platform"),
        )
    ).expanduser()
    pool = MySqlConnectionPool(config)
    try:
        store = LocalObjectStore(
            data_root / "objects",
            metadata_repository=MySqlMediaAssetRepository(pool.connection),
        )
        print(f"本轮 GC 删除对象数：{store.collect_garbage()}")
    finally:
        pool.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
