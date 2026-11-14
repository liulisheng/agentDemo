"""Read-only environment checks for local development and deployment gates."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from .persistence.errors import DatabaseUnavailableError
from .persistence.mysql import MySqlConfig, MySqlDatabase


class DoctorStatus(StrEnum):
    PASSED = "PASSED"
    SKIPPED = "SKIPPED"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class DoctorCheck:
    name: str
    status: DoctorStatus
    detail: str


@dataclass(frozen=True, slots=True)
class DoctorReport:
    checks: tuple[DoctorCheck, ...]

    @property
    def exit_code(self) -> int:
        return 2 if any(check.status is DoctorStatus.UNAVAILABLE for check in self.checks) else 0

    def check(self, name: str) -> DoctorCheck:
        for check in self.checks:
            if check.name == name:
                return check
        raise KeyError(name)

    def to_dict(self) -> dict[str, Any]:
        return {
            "exit_code": self.exit_code,
            "checks": [
                {
                    "name": check.name,
                    "status": check.status.value,
                    "detail": check.detail,
                }
                for check in self.checks
            ],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


def _default_migration_path() -> Path:
    return Path(__file__).resolve().parents[2].parent / "docs" / "migrations" / "V001__core_schema.sql"


def run_doctor(
    *,
    database: Any | None,
    require_mysql: bool = False,
    migration_path: Path | None = None,
    database_error: str | None = None,
) -> DoctorReport:
    checks = [
        DoctorCheck("package_import", DoctorStatus.PASSED, "harness_engine import succeeded"),
    ]
    path = migration_path or _default_migration_path()
    v2_path = path.with_name("V002__conversation_messages.sql")
    migrations = (
        (path, ("CREATE TABLE schema_version",)),
        (v2_path, ("CREATE TABLE messages", "INSERT INTO schema_version (version) VALUES (2)")),
    )
    if (migration_path is None and not path.exists() and not v2_path.exists()
            and not (Path(__file__).resolve().parents[2] / "pyproject.toml").is_file()):
        checks.append(DoctorCheck("schema_file", DoctorStatus.SKIPPED,
                                  "canonical V001 and V002 migrations are distributed separately from the wheel"))
    else:
        invalid = []
        for file_path, markers in migrations:
            try:
                contents = file_path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                invalid.append(file_path)
                continue
            if not all(marker in contents for marker in markers):
                invalid.append(file_path)
        if invalid:
            checks.append(DoctorCheck("schema_file", DoctorStatus.UNAVAILABLE,
                                      "missing or invalid: " + ", ".join(map(str, invalid))))
        else:
            checks.append(DoctorCheck("schema_file", DoctorStatus.PASSED,
                                      f"{path}; {v2_path}"))

    if database is None:
        if require_mysql:
            checks.append(DoctorCheck("mysql", DoctorStatus.UNAVAILABLE, database_error or "MySQL database was not configured"))
        else:
            checks.append(DoctorCheck("mysql", DoctorStatus.SKIPPED, "no database requested"))
    else:
        try:
            detail = database.health()
        except Exception as exc:  # noqa: BLE001 - doctor reports adapter-specific failures
            checks.append(DoctorCheck("mysql", DoctorStatus.UNAVAILABLE, str(exc)))
        else:
            checks.append(DoctorCheck("mysql", DoctorStatus.PASSED, str(detail)))
    return DoctorReport(checks=tuple(checks))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="harness doctor")
    parser.add_argument("--json", action="store_true", help="emit a JSON report")
    parser.add_argument(
        "--migration",
        type=Path,
        help="path to canonical V001; canonical V002 must be beside it",
    )
    parser.add_argument("--mysql", action="store_true", help="connect to configured MySQL")
    parser.add_argument(
        "--require-mysql",
        action="store_true",
        help="fail when MySQL is unavailable or not requested",
    )
    args = parser.parse_args(argv)
    database = None
    database_error = None
    if args.mysql or args.require_mysql:
        try:
            database = MySqlDatabase.from_config(MySqlConfig.from_env())
        except DatabaseUnavailableError as exc:
            database_error = str(exc)
        except Exception as exc:  # noqa: BLE001 - connector/configuration error types are optional
            database_error = f"MySQL configuration failed: {exc}"
    report = run_doctor(
        database=database,
        require_mysql=args.require_mysql or args.mysql,
        migration_path=args.migration,
        database_error=database_error,
    )
    if args.json:
        print(report.to_json())
    else:
        for check in report.checks:
            print(f"{check.status.value}\t{check.name}\t{check.detail}")
    return report.exit_code


__all__ = ["DoctorCheck", "DoctorReport", "DoctorStatus", "main", "run_doctor"]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
