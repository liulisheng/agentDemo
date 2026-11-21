"""平台 Run 生命周期状态必须能表达持久化队列与审批阶段。"""

import pytest

from agent_platform.domain.execution import RuntimeStatus


@pytest.mark.parametrize(
    "status",
    ("queued", "starting", "running", "awaiting_approval", "cancelling"),
)
def test_runtime_status_includes_persisted_nonterminal_run_states(status: str) -> None:
    assert RuntimeStatus(status).value == status
