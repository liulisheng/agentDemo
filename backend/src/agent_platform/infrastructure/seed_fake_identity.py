"""显式、幂等写入本地 Fake 身份数据；应用启动流程不会调用本模块。"""

from __future__ import annotations

import argparse
import os
from typing import Any

_USERS = (
    ("admin-local", "tenant-a", "admin-local", "本地管理员", "ACTIVE"),
    ("member-local", "tenant-a", "member-local", "本地成员", "ACTIVE"),
    ("admin-other", "tenant-b", "admin-other", "其他租户管理员", "ACTIVE"),
    ("disabled-local", "tenant-a", "disabled-local", "已停用用户", "DISABLED"),
)


def seed_fake_identity(connection: Any) -> None:
    """在已迁移数据库中幂等写入本地平台、租户和测试用户。"""

    cursor = connection.cursor()
    try:
        cursor.execute(
            "INSERT INTO platforms (platform_id, code, name, status) VALUES (%s,%s,%s,%s) "
            "ON DUPLICATE KEY UPDATE name=VALUES(name), status=VALUES(status)",
            ("plt-local", "local", "本地平台", "ACTIVE"),
        )
        for tenant_id, code, name in (
            ("tenant-a", "tenant-a", "本地租户"),
            ("tenant-b", "tenant-b", "隔离测试租户"),
        ):
            cursor.execute(
                "INSERT INTO tenants (platform_id, tenant_id, code, name, status) "
                "VALUES ('plt-local',%s,%s,%s,'ACTIVE') "
                "ON DUPLICATE KEY UPDATE name=VALUES(name), status=VALUES(status)",
                (tenant_id, code, name),
            )
        for user_id, tenant_id, subject, display_name, status in _USERS:
            cursor.execute(
                "INSERT INTO users "
                "(platform_id, tenant_id, user_id, external_subject, username, display_name, status) "
                "VALUES ('plt-local',%s,%s,%s,%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE external_subject=VALUES(external_subject), "
                "username=VALUES(username), display_name=VALUES(display_name), status=VALUES(status)",
                (tenant_id, user_id, f"fake:{subject}", user_id, display_name, status),
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()


def main(argv: list[str] | None = None) -> int:
    """由明确命令行调用 Fake seed；不会建库、跑 migration 或修改真实身份源。"""

    parser = argparse.ArgumentParser(prog="agent-platform-seed-fake-identity")
    parser.add_argument("--host", default=os.environ.get("MYSQL_HOST"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("MYSQL_PORT", "3306")))
    parser.add_argument("--database", default=os.environ.get("MYSQL_DATABASE"))
    parser.add_argument("--user", default=os.environ.get("MYSQL_USER"))
    args = parser.parse_args(argv)
    password = os.environ.get("MYSQL_PASSWORD")
    missing = [name for name, value in (("MYSQL_HOST", args.host), ("MYSQL_DATABASE", args.database), ("MYSQL_USER", args.user), ("MYSQL_PASSWORD", password)) if value is None]
    if missing:
        parser.error("缺少连接配置：" + ", ".join(missing))
    try:
        import mysql.connector
    except ImportError as error:
        raise RuntimeError("Fake identity seed 需要安装 backend optional dependency：pip install -e '.[mysql]'") from error
    connection = mysql.connector.connect(
        host=args.host,
        port=args.port,
        database=args.database,
        user=args.user,
        password=password,
    )
    try:
        seed_fake_identity(connection)
    finally:
        connection.close()
    print("已幂等写入本地 Fake 平台、租户和用户。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
