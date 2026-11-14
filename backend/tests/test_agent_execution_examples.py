from agent_platform.application.context import BusinessContextAssembler
from agent_platform.domain.context import BusinessContextPolicy
from agent_platform.ports.workspaces import WorkspaceMode, WorkspaceProfile


def test_log_and_code_execution_profiles_have_declared_environment_contracts() -> None:
    log_profile = WorkspaceProfile("log", mode=WorkspaceMode.NO_FILES, remote_data_tools=True)
    code_profile = WorkspaceProfile("code", mode=WorkspaceMode.EPHEMERAL, files=True, repository=True)
    snapshot = BusinessContextAssembler(BusinessContextPolicy(max_tokens=20)).assemble(
        run_id="run-1", trace_id="trace-1", agent_ref="employee-1", skill_refs=("skill-1",),
        recent_messages=("分析日志",), memory_refs=("tenant-memory-1",), tool_result_refs=()
    )
    assert log_profile.mode == WorkspaceMode.NO_FILES
    assert code_profile.repository is True
    assert snapshot.sources
