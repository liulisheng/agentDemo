from dataclasses import replace
from datetime import UTC, datetime, timedelta

from harness_engine import persistence_demo
from harness_engine.core import RunStatus, TenantContext
from harness_engine.engines.fake import FakeEngine
from harness_engine.persistence.errors import ResourceNotFoundError
from harness_engine.persistence.models import Lease, Page, RunState, SessionStatus
from harness_engine.persistence_demo import build_demo_engine, run_persistent_demo
from harness_engine.session_service import PersistentSessionService


class MemorySessionRepository:
    def __init__(self):
        self.records = {}

    def create(self, tenant, record):
        self.records[record.control_session_id] = record
        return record

    def get(self, tenant, control_session_id):
        record = self.records.get(control_session_id)
        if record is None or record.tenant.scope != tenant.scope:
            raise ResourceNotFoundError("session not found")
        return record

    def list(self, tenant, *, limit, cursor=None):
        return Page(items=tuple(self.records.values())[:limit])

    def update_status(self, tenant, control_session_id, status):
        raise NotImplementedError


class MemoryRunRepository:
    def __init__(self):
        self.records = {}

    def create_or_get(self, tenant, record):
        existing = next(
            (r for r in self.records.values() if r.idempotency_key == record.idempotency_key),
            None,
        )
        if existing:
            return existing
        self.records[record.run_id] = record
        return record

    def get(self, tenant, run_id):
        return self.records[run_id]

    def list(self, tenant, *, session_id=None, limit, cursor=None):
        return Page(items=tuple(self.records.values())[:limit])

    def claim(self, tenant, run_id, worker_id, *, lease_seconds=30):
        record = self.records[run_id]
        token = record.fencing_token + 1
        expires = datetime.now(UTC) + timedelta(seconds=lease_seconds)
        self.records[run_id] = replace(
            record,
            state=RunState.STARTING,
            worker_id=worker_id,
            fencing_token=token,
            lease_expires_at=expires,
        )
        return Lease(run_id, worker_id, token, expires)

    def transition(self, tenant, run_id, fencing_token, state, *, error_code=None, result_ref=None):
        record = self.records[run_id]
        self.records[run_id] = replace(
            record,
            state=state,
            fencing_token=fencing_token,
            error_code=error_code,
            result_ref=result_ref,
            finished_at=datetime.now(UTC) if state is RunState.SUCCEEDED else record.finished_at,
        )
        return self.records[run_id]

    def mark_unknown(self, *args, **kwargs):
        raise NotImplementedError

    def claim_next(self, *args, **kwargs):
        raise NotImplementedError

    def request_cancel(self, *args, **kwargs):
        raise NotImplementedError

    def expire_leases(self, *args, **kwargs):
        raise NotImplementedError

    def heartbeat(self, *args, **kwargs):
        raise NotImplementedError


def test_persistent_demo_executes_fake_run_and_reads_terminal_state():
    user = TenantContext("platform", "tenant", "user", roles=("owner",), auth_source="demo")
    worker = TenantContext("platform", "tenant", "worker", roles=("worker",), auth_source="worker")
    sessions = MemorySessionRepository()
    runs = MemoryRunRepository()
    service = PersistentSessionService(sessions, runs)
    engine = FakeEngine()

    report = run_persistent_demo(
        service,
        engine,
        user,
        worker,
        prompt="persistent flow",
        idempotency_key="demo-key",
        worker_id="demo-worker",
    )

    assert report["engine"] == "fake"
    assert report["result_text"] == "fake: persistent flow"
    assert report["result_status"] == RunStatus.SUCCEEDED.value
    assert report["persisted_run_state"] == RunState.SUCCEEDED.value
    assert report["persisted_session_status"] == SessionStatus.ACTIVE.value
    assert report["idempotent_submission"] is True
    assert report["run_id"] in runs.records
    engine.close()


def test_build_demo_engine_passes_deepseek_config(monkeypatch, tmp_path):
    config = tmp_path / "local-openai.json"
    config.write_text("{}", encoding="utf-8")
    captured = {}
    sentinel = object()

    def fake_deepseek_engine(**kwargs):
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(persistence_demo, "DeepSeekHarnessEngine", fake_deepseek_engine)

    engine = build_demo_engine(
        "deepseek",
        local_config=config,
        dsh_home=tmp_path / "harness-home",
        workspace=tmp_path,
        profile="sdk-minimal",
        provider="deepseek-official",
        model="deepseek-v4-flash",
        max_tokens=128,
    )

    assert engine is sentinel
    assert captured == {
        "local_config": config,
        "dsh_home": tmp_path / "harness-home",
        "cwd": tmp_path,
        "profile": "sdk-minimal",
        "provider": "deepseek-official",
        "model": "deepseek-v4-flash",
        "max_tokens": 128,
    }
