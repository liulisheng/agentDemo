"""确认 control-plane Compose 中 Worker 使用独立命令和共享持久化卷。"""

from pathlib import Path


def test_control_plane_compose_declares_the_run_worker_service() -> None:
    compose = (
        Path(__file__).parents[2] / "deploy" / "control-plane" / "docker-compose.yml"
    ).read_text(encoding="utf-8")

    worker = compose.split("\n  worker:\n", maxsplit=1)
    assert len(worker) == 2
    worker_service = worker[1].split("\n  web:\n", maxsplit=1)[0]
    assert 'command: ["agent-platform-run-worker"]' in worker_service
    assert "AGENT_PLATFORM_ID: ${AGENT_PLATFORM_ID:-plt-local}" in worker_service
    assert "control_plane_objects:/var/lib/agent-platform" in worker_service
    assert "service_completed_successfully" in worker_service
    assert '"127.0.0.1:${HTTP_PORT:-8088}:8080"' not in worker_service


def test_control_plane_compose_declares_the_approval_action_worker_service() -> None:
    compose = (
        Path(__file__).parents[2] / "deploy" / "control-plane" / "docker-compose.yml"
    ).read_text(encoding="utf-8")

    approval_worker = compose.split("\n  approval-worker:\n", maxsplit=1)
    assert len(approval_worker) == 2
    service = approval_worker[1].split("\n  web:\n", maxsplit=1)[0]
    assert 'command: ["agent-platform-approval-worker"]' in service
    assert "AGENT_PLATFORM_TENANT_ID: ${AGENT_PLATFORM_TENANT_ID:-tenant-a}" in service
    assert "control_plane_objects:/var/lib/agent-platform" in service


def test_backend_image_includes_the_engine_neutral_harness_package() -> None:
    dockerfile = (
        Path(__file__).parents[2] / "deploy" / "control-plane" / "Dockerfile.backend"
    ).read_text(encoding="utf-8")

    assert "COPY harness-engine /app/harness-engine" in dockerfile
    assert "'/app/harness-engine[deepseek,mcp]'" in dockerfile
