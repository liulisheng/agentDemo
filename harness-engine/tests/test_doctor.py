import json

from harness_engine import doctor
from harness_engine.doctor import DoctorStatus, run_doctor


class HealthyDatabase:
    def health(self):
        return "ok"


class BrokenDatabase:
    def health(self):
        raise RuntimeError("database offline")


def test_doctor_skips_mysql_without_a_database_and_is_json_serializable():
    report = run_doctor(database=None)

    assert report.exit_code == 0
    assert report.check("schema_file").status is DoctorStatus.PASSED
    assert report.check("mysql").status is DoctorStatus.SKIPPED
    payload = json.loads(report.to_json())
    assert payload["checks"][0]["name"] == "package_import"


def test_doctor_requires_mysql_when_requested():
    report = run_doctor(database=None, require_mysql=True)

    assert report.exit_code == 2
    assert report.check("mysql").status is DoctorStatus.UNAVAILABLE


def test_doctor_reports_database_health_and_unavailability():
    healthy = run_doctor(database=HealthyDatabase())
    broken = run_doctor(database=BrokenDatabase())

    assert healthy.check("mysql").status is DoctorStatus.PASSED
    assert broken.check("mysql").status is DoctorStatus.UNAVAILABLE


def test_doctor_skips_repository_migration_check_for_wheel_install(monkeypatch, tmp_path):
    module_path = tmp_path / "env" / "Lib" / "site-packages" / "harness_engine" / "doctor.py"
    monkeypatch.setattr(doctor, "__file__", str(module_path))

    report = run_doctor(database=None)

    assert report.check("schema_file").status is DoctorStatus.SKIPPED
    assert report.exit_code == 0


def test_doctor_reports_explicit_missing_migration_path_as_unavailable(tmp_path):
    report = run_doctor(database=None, migration_path=tmp_path / "missing.sql")

    assert report.check("schema_file").status is DoctorStatus.UNAVAILABLE


def test_doctor_requires_valid_v002_beside_explicit_v001(tmp_path):
    v1 = tmp_path / "V001__core_schema.sql"
    v1.write_text("CREATE TABLE schema_version", encoding="utf-8")
    v2 = tmp_path / "V002__conversation_messages.sql"
    assert run_doctor(database=None, migration_path=v1).check("schema_file").status is DoctorStatus.UNAVAILABLE
    v2.write_text("invalid", encoding="utf-8")
    assert run_doctor(database=None, migration_path=v1).check("schema_file").status is DoctorStatus.UNAVAILABLE
    v2.write_text("CREATE TABLE messages\nINSERT INTO schema_version (version) VALUES (2)", encoding="utf-8")
    assert run_doctor(database=None, migration_path=v1).check("schema_file").status is DoctorStatus.PASSED


def test_mysql_health_requires_v2_and_accepts_later_versions():
    import pytest

    from harness_engine.persistence.errors import DatabaseUnavailableError
    from harness_engine.persistence.mysql import MySqlDatabase

    class Cursor:
        def __init__(self, version): self.version = version
        def execute(self, sql): assert sql.strip().upper().startswith("SELECT ")
        def fetchone(self): return {"version": self.version}
        def close(self): pass
    class Connection:
        def __init__(self, version): self.version = version
        def cursor(self, **kwargs): return Cursor(self.version)
        def commit(self): pass
        def rollback(self): pass
        def close(self): pass
    with pytest.raises(DatabaseUnavailableError, match="(?i)(V002|version 2)"):
        MySqlDatabase(lambda: Connection(1)).health()
    assert MySqlDatabase(lambda: Connection(2)).health() == "schema_version=2"
    assert MySqlDatabase(lambda: Connection(3)).health() == "schema_version=3"


def test_doctor_reports_missing_migrations_in_source_checkout(monkeypatch, tmp_path):
    root = tmp_path / "checkout"
    module_path = root / "harness-engine" / "src" / "harness_engine" / "doctor.py"
    module_path.parent.mkdir(parents=True)
    (root / "harness-engine" / "pyproject.toml").write_text("[project]", encoding="utf-8")
    monkeypatch.setattr(doctor, "__file__", str(module_path))

    report = run_doctor(database=None)

    assert report.check("schema_file").status is DoctorStatus.UNAVAILABLE
