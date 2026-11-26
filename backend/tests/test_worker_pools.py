import pytest

from agent_platform.application.worker_pools import WorkerPoolRegistry
from agent_platform.domain.common import DomainError
from agent_platform.domain.teams import TeamMember


def test_worker_pool_claims_only_matching_engine_queue() -> None:
    pools = WorkerPoolRegistry()
    pools.register("codex_cli", max_concurrency=2)
    pools.register("deepseek_harness", max_concurrency=2)
    pools.enqueue("codex_cli", "run-codex")
    pools.enqueue("deepseek_harness", "run-deepseek")

    lease = pools.claim("codex_cli", "worker-codex")

    assert lease.run_id == "run-codex"
    assert lease.engine_name == "codex_cli"
    assert pools.queue_depth("deepseek_harness") == 1


def test_unhealthy_pool_rejects_claim_without_fallback() -> None:
    pools = WorkerPoolRegistry()
    pools.register("codex_cli", max_concurrency=1)
    pools.set_health("codex_cli", False)
    pools.enqueue("codex_cli", "run-1")

    with pytest.raises(DomainError, match="Worker Pool"):
        pools.claim("codex_cli", "worker-1")


def test_capability_matching_is_explicit() -> None:
    member = TeamMember(
        "member-1",
        "employee-1",
        1,
        "deployment-1",
        engine_name="codex_cli",
        capabilities=("workspace", "tool_calls"),
    )
    pools = WorkerPoolRegistry()

    assert pools.member_supports(member, ("workspace", "tool_calls")) is True
    assert pools.member_supports(member, ("mcp",)) is False
