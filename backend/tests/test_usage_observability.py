import pytest

from agent_platform.adapters.identity.fake import FakeIdentityProvider
from agent_platform.application.usage import InMemoryTraceRepository, InMemoryUsageLedger
from agent_platform.domain.common import DomainError
from agent_platform.domain.observability import SpanType, TraceSpan
from agent_platform.domain.usage import ModelCallRecord, UsageStatus


def test_usage_ledger_keeps_completed_records_immutable_and_aggregates() -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    ledger = InMemoryUsageLedger()
    record = ModelCallRecord(
        call_id="call-1",
        run_id="run-1",
        tenant_id=identity.tenant_id,
        user_id=identity.user_id,
        employee_id="employee-1",
        session_id="session-1",
        input_tokens=2,
        output_tokens=3,
        total_tokens=5,
        usage_status=UsageStatus.EXACT,
        provider_request_id="provider-1",
        cost_amount=0.1,
        currency="CNY",
        pricing_version="price-1",
    )
    ledger.record(identity, record)
    with pytest.raises(DomainError) as error:
        ledger.record(identity, record)
    assert error.value.code == "CONFLICT"
    assert ledger.aggregate(identity)["total_tokens"] == 5


def test_trace_repository_is_tenant_scoped() -> None:
    provider = FakeIdentityProvider()
    identity = provider.resolve_identity("admin-local")
    other = provider.resolve_identity("admin-other")
    repository = InMemoryTraceRepository()
    span = TraceSpan("span-1", "trace-1", None, "run-1", SpanType.RUN, "succeeded")
    repository.append(identity, "run-1", span)
    assert repository.list(identity, "run-1") == (span,)
    assert repository.list(other, "run-1") == ()


def test_usage_ledger_type_and_span_parent_contract() -> None:
    from agent_platform.domain.observability import TraceId
    from agent_platform.domain.usage import UsageLedger

    trace_id: TraceId = "trace-1"
    ledger = UsageLedger("plt-local", "tenant-a", "run-1", 5, 0.1, 1)
    assert trace_id.startswith("trace-")
    assert ledger.call_count == 1


def test_usage_adjustment_preserves_original_record() -> None:
    from agent_platform.domain.usage import UsageAdjustment
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    ledger = InMemoryUsageLedger()
    record = ModelCallRecord("call-a", "run-a", identity.tenant_id, identity.user_id, "employee", "session", 1, 1, 2, UsageStatus.EXACT, "p", 0.1, "CNY", "v1")
    ledger.record(identity, record)
    ledger.add_adjustment(identity, UsageAdjustment("adj-1", "call-a", "provider correction", 0.2, 1))
    assert ledger.aggregate(identity)["total_tokens"] == 2
    assert ledger.adjustments(identity)[0].original_call_id == "call-a"
