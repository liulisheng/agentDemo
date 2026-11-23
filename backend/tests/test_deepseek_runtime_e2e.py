"""验证 API 配置的 DeepSeek Profile 会沿固定快照进入 Harness Runtime。"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import ClassVar

from fastapi.testclient import TestClient

from agent_platform.adapters.runtime.fake import FakeRuntimeAdapter
from agent_platform.api.app import _build_runtime_adapter, create_app
from agent_platform.application.engine_providers import EngineProviderRegistry
from tests._runtime_config_helpers import configure_api_deployment


class _FakeSession:
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id

    def run(self, prompt: str) -> SimpleNamespace:
        return SimpleNamespace(
            final_response=f"本地 Harness Mock：{prompt}",
            finish_reason="completed",
            request_id="local-mock-request",
        )


class _DeterministicDeepSeekHarness:
    instances: ClassVar[list[_DeterministicDeepSeekHarness]] = []

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        type(self).instances.append(self)

    def start_session(self, session_id: str) -> _FakeSession:
        return _FakeSession(session_id)

    def resume_session(self, session_id: str) -> _FakeSession:
        return _FakeSession(session_id)

    def close(self) -> None:
        return None


class _RecordingFakeRuntime(FakeRuntimeAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def run(self, request):
        self.calls += 1
        return super().run(request)


def test_profile_route_session_and_run_use_deepseek_harness_adapter(
    monkeypatch, tmp_path
) -> None:
    """用确定性 SDK 替身验证 Profile 到 Run/Trace 的 API 链路。"""

    _DeterministicDeepSeekHarness.instances.clear()
    monkeypatch.setitem(
        sys.modules,
        "deepseek_harness",
        SimpleNamespace(DeepSeekHarness=_DeterministicDeepSeekHarness),
    )
    monkeypatch.setenv("AGENT_PLATFORM_DEEPSEEK_PROVIDER", "local-acceptance")
    monkeypatch.setenv("AGENT_PLATFORM_DEEPSEEK_MODEL", "acceptance-model")
    monkeypatch.delenv("AGENT_PLATFORM_DEEPSEEK_LOCAL_CONFIG", raising=False)
    monkeypatch.setenv(
        "AGENT_PLATFORM_MODEL_SECRET_TENANT_A_LOCAL_MODEL_KEY", "local-acceptance-key"
    )

    deepseek_runtime = _build_runtime_adapter("deepseek", tmp_path / "harness-state")
    providers = EngineProviderRegistry.for_runtime(deepseek_runtime)
    fake_runtime = _RecordingFakeRuntime()
    client = TestClient(
        create_app(
            runtime_adapter=fake_runtime,
            engine_provider_registry=providers,
        )
    )
    admin = {"X-Fake-User": "admin-local"}

    provider_response = client.get("/api/v1/engine-providers", headers=admin)
    assert provider_response.status_code == 200
    assert [item["engine_name"] for item in provider_response.json()["items"]] == [
        "deepseek_harness"
    ]
    assert provider_response.json()["items"][0]["execution_mode"] == "NATIVE"

    profile_response = client.post(
        "/api/v1/execution-profiles",
        headers=admin,
        json={"engine_name": "deepseek_harness"},
    )
    assert profile_response.status_code == 201, profile_response.text
    profile = profile_response.json()
    assert profile["status"] == "PUBLISHED"
    assert profile["engine_available"] is True

    connection_response = client.post(
        "/api/v1/model-connections",
        headers=admin,
        json={
            "name": "DeepSeek Harness 本地 Mock",
            "provider_kind": "openai-compatible",
            "protocol": "openai-chat-completions",
            "endpoint": "http://model.mock/v1",
            "secret_id": "local-model-key",
            "catalog_mode": "MANUAL",
        },
    )
    assert connection_response.status_code == 201, connection_response.text
    connection_id = connection_response.json()["connection_id"]
    model_response = client.post(
        f"/api/v1/model-connections/{connection_id}/models",
        headers=admin,
        json={"model_id": "acceptance-model", "display_name": "确定性 Mock"},
    )
    assert model_response.status_code == 201, model_response.text
    route_response = client.post(
        "/api/v1/model-routes",
        headers=admin,
        json={
            "alias": "DeepSeek Harness 本地验收路由",
            "connection_id": connection_id,
            "catalog_model_id": "acceptance-model",
            "model_name": "acceptance-model",
        },
    )
    assert route_response.status_code == 201, route_response.text
    route = route_response.json()

    employee_response = client.post(
        "/api/v1/digital-employees",
        headers=admin,
        json={"name": "Harness 验收助手", "role_name": "验收助手", "description": "固定引擎快照"},
    )
    assert employee_response.status_code == 201, employee_response.text
    employee_id = employee_response.json()["id"]
    skill_response = client.post(
        "/api/v1/skills",
        headers=admin,
        json={
            "name": "Harness 验收步骤",
            "skill_type": "instruction",
            "instruction": "使用配置好的模型路由回答请求。",
        },
    )
    assert skill_response.status_code == 201, skill_response.text
    skill_id = skill_response.json()["id"]
    skill_publish = client.post(f"/api/v1/skills/{skill_id}/publish", headers=admin)
    assert skill_publish.status_code == 200, skill_publish.text
    version_response = client.post(
        f"/api/v1/digital-employees/{employee_id}/versions",
        headers=admin,
        json={
            "system_prompt": "仅用于本地 Harness 验收。",
            "skill_ids": [skill_id],
            "tool_ids": [],
        },
    )
    assert version_response.status_code == 201, version_response.text
    publish_response = client.post(
        f"/api/v1/digital-employees/{employee_id}/versions/1/publish",
        headers=admin,
    )
    assert publish_response.status_code == 200, publish_response.text
    deployment_response = client.post(
        "/api/v1/deployments",
        headers=admin,
        json={"employee_id": employee_id, "version": 1},
    )
    assert deployment_response.status_code == 201, deployment_response.text
    configure_api_deployment(
        client,
        admin,
        deployment_response.json(),
        route_id=route["route_id"],
    )

    session_response = client.post(
        "/api/v1/sessions",
        headers=admin,
        json={"deployment_id": deployment_response.json()["id"]},
    )
    assert session_response.status_code == 201, session_response.text
    session = session_response.json()
    run_response = client.post(
        f"/api/v1/sessions/{session['id']}/runs",
        headers=admin,
        json={"prompt": "确认 Harness 已接通", "idempotency_key": "deepseek-harness-e2e"},
    )
    assert run_response.status_code == 201, run_response.text
    run = run_response.json()
    trace_response = client.get(f"/api/v1/runs/{run['id']}/trace", headers=admin)
    assert trace_response.status_code == 200, trace_response.text
    trace = trace_response.json()

    assert run["status"] == "succeeded"
    assert run["result"] == "本地 Harness Mock：确认 Harness 已接通"
    assert run["snapshot"]["engine_name"] == "deepseek_harness"
    assert run["snapshot"]["execution_profile_id"] == profile["id"]
    assert run["snapshot"]["model_route_id"] == route["route_id"]
    assert run["snapshot"]["model_route_snapshot"]["model_id"] == "acceptance-model"
    assert trace["runtime_reference"]["engine_name"] == "deepseek"
    assert _DeterministicDeepSeekHarness.instances
    assert fake_runtime.calls == 0
    harness_environment = _DeterministicDeepSeekHarness.instances[0].kwargs["env"]
    assert harness_environment["AGENT_PLATFORM_MODEL_API_KEY"] == "local-acceptance-key"
    assert "local-acceptance-key" not in run_response.text
    client.close()
