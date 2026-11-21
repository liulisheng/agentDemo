from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from agent_platform.adapters.identity.fake import FakeIdentityProvider
from agent_platform.application.catalog_service import InMemoryCatalogRepository
from agent_platform.domain.common import DomainError
from agent_platform.domain.identity import IdentityContext
from agent_platform.domain.model_routes import (
    ModelConnection,
    ModelTestKind,
    ModelTestRecord,
    ModelTestResult,
    ModelTestState,
    UsageStatus,
)
from agent_platform.workers.run_worker import RunWorker


def _record(identity: IdentityContext, test_id: str = "test-model-job") -> ModelTestRecord:
    return ModelTestRecord(
        test_id=test_id,
        tenant_id=identity.tenant_id,
        connection_id="connection-model-job",
        test_kind=ModelTestKind.CONNECTION,
        state=ModelTestState.QUEUED,
        snapshot={"operation": "SYNC_MODELS"},
        created_by_user_id=identity.user_id,
    )


def _repository(identity: IdentityContext) -> InMemoryCatalogRepository:
    repository = InMemoryCatalogRepository()
    repository.save_model_connection(
        identity,
        ModelConnection(
            connection_id="connection-model-job",
            tenant_id=identity.tenant_id,
            name="异步测试连接",
            provider_kind="openai-compatible",
            protocol="openai-chat-completions",
            endpoint="http://localhost:63072/v1",
        ),
    )
    return repository


def test_model_test_claim_finish_and_tenant_scope_are_fenced() -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    repository = _repository(identity)
    record = _record(identity)
    repository.save_model_test_record(identity, record)

    claimed = repository.claim_next_model_test(
        identity.platform_id, "worker-a", lease_seconds=30
    )
    assert claimed is not None
    assert claimed.state is ModelTestState.RUNNING
    assert claimed.worker_id == "worker-a"
    assert claimed.fencing_token == 1
    with pytest.raises(ValueError, match="only queued"):
        claimed.claim("worker-b", now=datetime.now(UTC), lease_seconds=30)

    other_tenant = FakeIdentityProvider().resolve_identity("admin-other")
    with pytest.raises(DomainError) as not_found:
        repository.get_model_test_record(other_tenant, record.test_id)
    assert not_found.value.code == "RESOURCE_NOT_FOUND"

    worker_identity = IdentityContext(
        identity.platform_id,
        identity.tenant_id,
        "worker-a",
        frozenset(),
        auth_source="worker",
    )
    assert repository.heartbeat_model_test(
        worker_identity,
        claimed.test_id,
        "worker-a",
        claimed.fencing_token,
        lease_seconds=30,
    )
    assert not repository.finish_model_test(
        worker_identity,
        replace(claimed, fencing_token=claimed.fencing_token + 1),
        ModelTestResult(ModelTestState.SUCCEEDED),
    )
    assert repository.finish_model_test(
        worker_identity,
        claimed,
        ModelTestResult(
            ModelTestState.SUCCEEDED,
            latency_ms=17,
            provider_request_id="upstream-test-1",
            input_tokens=2,
            output_tokens=3,
            total_tokens=5,
            usage_status=UsageStatus.EXACT,
        ),
    )
    finished = repository.get_model_test_record(identity, record.test_id)
    assert finished.state is ModelTestState.SUCCEEDED
    assert finished.lease_expires_at is None
    assert finished.provider_request_id == "upstream-test-1"
    assert repository.claim_next_model_test(identity.platform_id, "worker-b", lease_seconds=30) is None


def test_expired_model_test_lease_becomes_unknown_without_retry() -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    repository = _repository(identity)
    repository.save_model_test_record(identity, _record(identity))
    claimed = repository.claim_next_model_test(
        identity.platform_id, "worker-a", lease_seconds=1
    )
    assert claimed is not None
    key = (identity.platform_id, identity.tenant_id, claimed.test_id)
    repository.model_test_records[key] = replace(
        claimed,
        lease_expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )

    assert repository.claim_next_model_test(identity.platform_id, "worker-b", lease_seconds=30) is None
    expired = repository.get_model_test_record(identity, claimed.test_id)
    assert expired.state is ModelTestState.UNKNOWN
    assert expired.error_category == "WORKER_LEASE_EXPIRED"
    assert expired.fencing_token == claimed.fencing_token + 1
    worker_identity = IdentityContext(
        identity.platform_id,
        identity.tenant_id,
        "worker-a",
        frozenset(),
        auth_source="worker",
    )
    assert not repository.finish_model_test(
        worker_identity,
        claimed,
        ModelTestResult(ModelTestState.SUCCEEDED),
    )


class _NoRunQueue:
    def claim_next_queued_run(self, platform_id, worker_id, *, lease_seconds):
        return None


class _NoControlPlane:
    pass


class _NoObjectStore:
    pass


def test_existing_run_worker_processes_model_test_and_marks_uncertain_result_unknown() -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    repository = _repository(identity)
    repository.save_model_test_record(identity, _record(identity))
    called: list[ModelTestRecord] = []

    def executor(claimed: ModelTestRecord) -> ModelTestResult:
        called.append(claimed)
        assert claimed.state is ModelTestState.RUNNING
        assert claimed.worker_id == "worker-a"
        return ModelTestResult(
            ModelTestState.SUCCEEDED,
            latency_ms=23,
            input_tokens=1,
            output_tokens=2,
            total_tokens=3,
            usage_status=UsageStatus.ESTIMATED,
        )

    worker = RunWorker(
        identity.platform_id,
        "worker-a",
        _NoRunQueue(),
        _NoControlPlane(),
        _NoObjectStore(),
        lease_seconds=3,
        heartbeat_interval_seconds=0.1,
        model_test_repository=repository,
        model_test_executor=executor,
    )
    assert asyncio.run(worker.run_once())
    assert len(called) == 1
    completed = repository.get_model_test_record(identity, "test-model-job")
    assert completed.state is ModelTestState.SUCCEEDED
    assert completed.usage_status is UsageStatus.ESTIMATED

    repository.save_model_test_record(identity, _record(identity, "test-uncertain-job"))

    def uncertain(_claimed: ModelTestRecord) -> ModelTestResult:
        raise TimeoutError("上游响应丢失，不能判断请求是否完成")

    worker.model_test_executor = uncertain
    assert asyncio.run(worker.run_once())
    unknown = repository.get_model_test_record(identity, "test-uncertain-job")
    assert unknown.state is ModelTestState.UNKNOWN
    assert unknown.error_category == "MODEL_TEST_OUTCOME_UNKNOWN"
    assert asyncio.run(worker.run_once()) is False
