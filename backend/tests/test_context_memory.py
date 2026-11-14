from agent_platform.adapters.identity.fake import FakeIdentityProvider
from agent_platform.application.context import BusinessContextAssembler
from agent_platform.application.memory import InMemoryMemoryStore, MemoryScope
from agent_platform.domain.common import DomainError
from agent_platform.domain.context import BusinessContextPolicy


def test_context_assembler_obeys_budget_and_records_excluded_sources() -> None:
    policy = BusinessContextPolicy(max_tokens=5, compaction_strategy="truncate")
    snapshot = BusinessContextAssembler(policy).assemble(
        run_id="run-1",
        trace_id="trace-1",
        agent_ref="employee-v1",
        skill_refs=("skill-1",),
        recent_messages=("one two", "three four five six"),
        memory_refs=("memory-1",),
        tool_result_refs=("tool-result-1",),
    )

    assert snapshot.total_tokens <= 5
    assert snapshot.sources
    assert snapshot.excluded_sources


def test_memory_is_tenant_scoped_and_explicitly_written() -> None:
    provider = FakeIdentityProvider()
    identity = provider.resolve_identity("admin-local")
    other = provider.resolve_identity("admin-other")
    store = InMemoryMemoryStore()
    item = store.write(
        identity,
        scope=MemoryScope.TENANT,
        scope_id=identity.tenant_id,
        text="偏好中文",
        source_run_id="run-1",
    )

    assert store.read(identity, MemoryScope.TENANT, identity.tenant_id)[0].item_id == item.item_id
    assert store.read(other, MemoryScope.TENANT, identity.tenant_id) == ()
    try:
        store.write(other, MemoryScope.TENANT, identity.tenant_id, "越权", "run-2")
    except DomainError as error:
        assert error.code == "RESOURCE_NOT_FOUND"
    else:
        raise AssertionError("cross-tenant memory write must fail")
