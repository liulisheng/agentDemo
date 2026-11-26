from agent_platform.application.task_outputs import TaskOutputContextService
from agent_platform.domain.orchestration import TaskOutput


def test_task_output_context_excludes_runtime_private_state() -> None:
    value = TaskOutput("task-1", "分析摘要", artifact_ids=("artifact-1",), source_engine="deepseek_harness")

    context = TaskOutputContextService().build_context([value])

    assert context == (
        {
            "task_id": "task-1",
            "summary": "分析摘要",
            "structured_output_ref": None,
            "artifact_ids": ["artifact-1"],
            "source_engine": "deepseek_harness",
            "source_model": None,
            "content_sha256": None,
        },
    )
    assert "runtime_session_ref" not in context[0]
