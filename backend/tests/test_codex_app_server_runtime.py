from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_platform.adapters.runtime.codex_app_server import CodexAppServerRuntimeAdapter
from agent_platform.adapters.runtime.fake import FakeRuntimeAdapter
from agent_platform.api.app import create_app
from agent_platform.application.engine_providers import (
    EngineProviderCapabilities,
    EngineProviderRegistry,
)
from agent_platform.domain.common import DomainError
from agent_platform.domain.execution import RuntimeStatus
from agent_platform.domain.model_routes import ModelRouteSnapshot, SecretRef
from agent_platform.ports.runtime import (
    RuntimeEmployeeContext,
    RuntimeRequest,
    RuntimeToolDefinition,
)


class _FakeAppServerConnection:
    def __init__(self) -> None:
        self.requests: list[tuple[str, dict[str, object]]] = []
        self.notifications: list[tuple[str, dict[str, object]]] = []
        self.closed = False

    def request(
        self,
        method: str,
        params: dict[str, object],
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, object]:
        del timeout_seconds
        self.requests.append((method, params))
        if method in {"thread/start", "thread/resume"}:
            return {"thread": {"id": "codex-thread-1"}}
        if method == "turn/start":
            return {"turn": {"id": "codex-turn-1"}}
        return {}

    def notify(self, method: str, params: dict[str, object]) -> None:
        self.notifications.append((method, params))

    def wait_for_turn(self, thread_id: str, turn_id: str, timeout_seconds: float):
        assert (thread_id, turn_id) == ("codex-thread-1", "codex-turn-1")
        assert timeout_seconds > 0
        return "completed", "Codex answer", ()

    def close(self) -> None:
        self.closed = True


def _route(protocol: str = "openai-responses") -> ModelRouteSnapshot:
    return ModelRouteSnapshot(
        tenant_id="tenant-a",
        route_id="route-a",
        route_version=2,
        route_alias="Codex Route",
        connection_id="connection-a",
        connection_version=3,
        provider_kind="openai-compatible",
        model_id="gpt-codex-local",
        protocol=protocol,
        endpoint="https://model.invalid/v1",
        secret_ref=SecretRef("tenant-model-key", scope="tenant-a", provider="environment"),
    )


def _request(*, session_ref: str | None = None, tools=()) -> RuntimeRequest:
    tool_definitions = tuple(tools)
    return RuntimeRequest(
        run_id="run-a",
        session_ref=session_ref,
        harness_home_ref="harness-session:" + "a" * 64,
        prompt="检查项目结构",
        skill_instructions=("优先只读检查。",),
        allowed_tools=tuple(item.name for item in tool_definitions),
        system_prompt="你是代码审查员工。",
        employee_context=RuntimeEmployeeContext(
            employee_id="employee-a",
            employee_version=4,
            system_prompt="你是代码审查员工。",
            skill_refs=(("skill-a", 2),),
            skill_instructions=("优先只读检查。",),
        ),
        model_route_snapshot=_route(),
        tool_definitions=tool_definitions,
    )


def test_codex_app_server_pins_route_and_resumes_the_same_thread(tmp_path: Path) -> None:
    connections: list[_FakeAppServerConnection] = []
    launched: list[tuple[dict[str, str], Path]] = []

    def create_connection(
        _command, cwd: Path, environment: dict[str, str], _timeout, _startup_timeout
    ):
        connection = _FakeAppServerConnection()
        connections.append(connection)
        launched.append((environment, cwd))
        return connection

    adapter = CodexAppServerRuntimeAdapter(
        command=("codex", "app-server", "--stdio"),
        state_root=tmp_path,
        secret_resolver=lambda reference: f"resolved:{reference.secret_id}",
        connection_factory=create_connection,
    )

    first_result = adapter.run(_request())

    assert first_result.status is RuntimeStatus.SUCCEEDED
    assert first_result.text == "Codex answer"
    assert first_result.runtime_session_ref == "codex-thread:codex-thread-1"
    assert first_result.runtime_thread_ref == "codex-thread-1"
    assert launched[0][0]["OPENAI_API_KEY"] == "resolved:tenant-model-key"
    assert launched[0][0]["CODEX_HOME"] != str(Path.home())
    assert launched[0][1].is_relative_to(tmp_path)
    start = next(params for method, params in connections[0].requests if method == "thread/start")
    assert start["model"] == "gpt-codex-local"
    assert start["modelProvider"] == "openai"
    assert start["sandbox"] == "workspace-write"
    assert "你是代码审查员工" in str(start["developerInstructions"])
    assert "优先只读检查" in str(start["developerInstructions"])
    config_text = (Path(launched[0][0]["CODEX_HOME"]) / "config.toml").read_text()
    assert 'openai_base_url = "https://model.invalid/v1"' in config_text
    assert '"OPENAI_API_KEY"' in config_text
    assert "resolved:tenant-model-key" not in config_text

    second_result = adapter.run(_request(session_ref=first_result.runtime_session_ref))
    assert second_result.status is RuntimeStatus.SUCCEEDED
    assert sum(method == "turn/start" for method, _ in connections[0].requests) == 2

    adapter.close()
    assert connections[0].closed

    restarted_connection = _FakeAppServerConnection()
    restarted = CodexAppServerRuntimeAdapter(
        command=("codex", "app-server", "--stdio"),
        state_root=tmp_path,
        secret_resolver=lambda reference: f"resolved:{reference.secret_id}",
        connection_factory=lambda *_args: restarted_connection,
    )
    resumed_result = restarted.run(_request(session_ref=first_result.runtime_session_ref))
    assert resumed_result.status is RuntimeStatus.SUCCEEDED
    assert any(method == "thread/resume" for method, _ in restarted_connection.requests)
    restarted.close()


def test_codex_adapter_rejects_unsupported_route_or_platform_tools(tmp_path: Path) -> None:
    adapter = CodexAppServerRuntimeAdapter(
        command=("codex", "app-server", "--stdio"),
        state_root=tmp_path,
        secret_resolver=lambda _reference: "secret",
        connection_factory=lambda *_args: _FakeAppServerConnection(),
    )
    incompatible = _request()
    incompatible = RuntimeRequest(
        run_id=incompatible.run_id,
        session_ref=incompatible.session_ref,
        harness_home_ref=incompatible.harness_home_ref,
        prompt=incompatible.prompt,
        skill_instructions=incompatible.skill_instructions,
        allowed_tools=incompatible.allowed_tools,
        system_prompt=incompatible.system_prompt,
        employee_context=incompatible.employee_context,
        model_route_snapshot=_route("openai-chat-completions"),
    )
    with pytest.raises(DomainError) as protocol_error:
        adapter.run(incompatible)
    assert protocol_error.value.code == "ENGINE_MODEL_INCOMPATIBLE"

    tool_request = _request(
        tools=(
            RuntimeToolDefinition(
                tool_id="tool-a",
                tool_version=1,
                name="read_data",
                description="读取租户业务数据",
                input_schema={"type": "object"},
                risk_level="READ",
            ),
        )
    )
    with pytest.raises(DomainError) as capability_error:
        adapter.run(tool_request)
    assert capability_error.value.code == "ENGINE_CAPABILITY_UNSUPPORTED"


def test_registered_codex_provider_is_selected_for_its_profile_and_route(tmp_path: Path) -> None:
    connection = _FakeAppServerConnection()
    adapter = CodexAppServerRuntimeAdapter(
        command=("codex", "app-server", "--stdio"),
        state_root=tmp_path,
        secret_resolver=lambda reference: f"resolved:{reference.secret_id}",
        connection_factory=lambda *_args: connection,
    )
    runtime_capabilities = EngineProviderCapabilities.from_runtime(adapter.capabilities)
    registry = EngineProviderRegistry()
    registry.register(
        "fake",
        FakeRuntimeAdapter(),
        display_name="Fake Runtime",
        model_protocols={"openai-responses", "openai-chat-completions"},
        simulation=True,
    )
    registry.register(
        "codex_cli",
        adapter,
        display_name="Codex CLI",
        model_protocols={"openai-responses"},
        capabilities=EngineProviderCapabilities(
            resume=runtime_capabilities.resume,
            cancellation=runtime_capabilities.cancellation,
            tool_calls=runtime_capabilities.tool_calls,
            mcp=runtime_capabilities.mcp,
            workspace=runtime_capabilities.workspace,
            sandbox=runtime_capabilities.sandbox,
            approvals=runtime_capabilities.approvals,
            model_route_required=True,
            route_secret_required=True,
        ),
    )
    client = TestClient(create_app(engine_provider_registry=registry))
    admin = {"X-Fake-User": "admin-local"}
    providers = client.get("/api/v1/engine-providers", headers=admin)
    assert providers.status_code == 200
    codex_provider = next(
        item for item in providers.json()["items"] if item["engine_name"] == "codex_cli"
    )
    assert codex_provider["execution_mode"] == "NATIVE"
    profile = client.post(
        "/api/v1/execution-profiles",
        headers=admin,
        json={"engine_name": "codex_cli"},
    )
    assert profile.status_code == 201
    model_connection = client.post(
        "/api/v1/model-connections",
        headers=admin,
        json={
            "name": "Codex 模型连接",
            "provider_kind": "openai-compatible",
            "protocol": "openai-responses",
            "endpoint": "https://model.invalid/v1",
            "secret_id": "tenant-model-key",
            "catalog_mode": "MANUAL",
        },
    )
    assert model_connection.status_code == 201, model_connection.text
    assert client.post(
        f"/api/v1/model-connections/{model_connection.json()['connection_id']}/models",
        headers=admin,
        json={"model_id": "gpt-codex-local", "display_name": "Codex Model"},
    ).status_code == 201
    route = client.post(
        "/api/v1/model-routes",
        headers=admin,
        json={
            "alias": "Codex 模型",
            "connection_id": model_connection.json()["connection_id"],
            "catalog_model_id": "gpt-codex-local",
            "model_name": "gpt-codex-local",
        },
    )
    assert route.status_code == 201
    skill = client.post(
        "/api/v1/skills",
        headers=admin,
        json={"name": "Codex Skill", "skill_type": "instruction", "instruction": "审阅代码"},
    ).json()
    assert client.post(f"/api/v1/skills/{skill['id']}/publish", headers=admin).status_code == 200
    employee = client.post(
        "/api/v1/digital-employees",
        headers=admin,
        json={"name": "Codex 员工", "role_name": "审查员", "description": "只读代码检查"},
    ).json()
    version = client.post(
        f"/api/v1/digital-employees/{employee['id']}/versions",
        headers=admin,
        json={"system_prompt": "审阅用户提供的内容", "skill_ids": [skill["id"]]},
    )
    assert version.status_code == 201
    assert client.post(
        f"/api/v1/digital-employees/{employee['id']}/versions/1/publish", headers=admin
    ).status_code == 200
    deployment = client.post(
        "/api/v1/deployments",
        headers=admin,
        json={"employee_id": employee["id"], "version": 1},
    ).json()
    configured = client.put(
        f"/api/v1/deployments/{deployment['id']}/runtime-config",
        headers=admin,
        json={
            "expected_config_version": None,
            "mode": "OVERRIDE",
            "execution_profile_id": profile.json()["id"],
            "execution_profile_version": profile.json()["version"],
            "model_route_id": route.json()["route_id"],
        },
    )
    assert configured.status_code == 200
    session = client.post(
        "/api/v1/sessions",
        headers=admin,
        json={"deployment_id": deployment["id"]},
    )
    assert session.status_code == 201, session.text
    assert session.json()["engine_name"] == "codex_cli"
    run = client.post(
        f"/api/v1/sessions/{session.json()['id']}/runs",
        headers=admin,
        json={"prompt": "查看项目结构", "idempotency_key": "codex-runtime-1"},
    )
    assert run.status_code == 201, run.text
    assert run.json()["result"] == "Codex answer"
    assert run.json()["runtime_reference"]["engine_name"] == "codex_cli"
    assert run.json()["snapshot"]["model_route_snapshot"]["model_id"] == "gpt-codex-local"
    assert "tenant-model-key" not in run.text
    assert "secret_ref" not in run.text
    assert "https://model.invalid/v1" not in run.text
    assert connection.requests[1][0] == "thread/start"
    assert connection.requests[2][0] == "turn/start"

    tool = client.post(
        "/api/v1/tools",
        headers=admin,
        json={"name": "read_ticket", "description": "读取工单", "input_schema": {"type": "object"}},
    )
    assert tool.status_code == 201
    assert client.post(
        f"/api/v1/tools/{tool.json()['id']}/publish", headers=admin
    ).status_code == 200
    tool_skill = client.post(
        "/api/v1/skills",
        headers=admin,
        json={
            "name": "Platform Tool Skill",
            "skill_type": "tool-bundle",
            "instruction": "读取指定工单",
            "allowed_tool_ids": [tool.json()["id"]],
        },
    ).json()
    assert client.post(
        f"/api/v1/skills/{tool_skill['id']}/publish", headers=admin
    ).status_code == 200
    tool_employee = client.post(
        "/api/v1/digital-employees",
        headers=admin,
        json={"name": "Codex Tool 员工", "role_name": "客服", "description": "需要平台工具"},
    ).json()
    tool_version = client.post(
        f"/api/v1/digital-employees/{tool_employee['id']}/versions",
        headers=admin,
        json={
            "system_prompt": "使用受控 Tool",
            "skill_ids": [tool_skill["id"]],
            "tool_ids": [tool.json()["id"]],
        },
    )
    assert tool_version.status_code == 201
    assert client.post(
        f"/api/v1/digital-employees/{tool_employee['id']}/versions/1/publish",
        headers=admin,
    ).status_code == 200
    tool_deployment = client.post(
        "/api/v1/deployments",
        headers=admin,
        json={"employee_id": tool_employee["id"], "version": 1},
    ).json()
    assert client.put(
        f"/api/v1/deployments/{tool_deployment['id']}/runtime-config",
        headers=admin,
        json={
            "expected_config_version": None,
            "mode": "OVERRIDE",
            "execution_profile_id": profile.json()["id"],
            "execution_profile_version": profile.json()["version"],
            "model_route_id": route.json()["route_id"],
        },
    ).status_code == 200
    unsupported_tools = client.post(
        "/api/v1/sessions",
        headers=admin,
        json={"deployment_id": tool_deployment["id"]},
    )
    assert unsupported_tools.status_code == 409
    assert unsupported_tools.json()["code"] == "ENGINE_CAPABILITY_UNSUPPORTED"

    def unavailable_secret(_reference):
        raise RuntimeError("secret-provider-unavailable")

    adapter.secret_resolver = unavailable_secret
    credential_employee = client.post(
        "/api/v1/digital-employees",
        headers=admin,
        json={"name": "Codex 凭据员工", "role_name": "审查员", "description": "验证凭据故障"},
    ).json()
    credential_version = client.post(
        f"/api/v1/digital-employees/{credential_employee['id']}/versions",
        headers=admin,
        json={"system_prompt": "审阅用户输入", "skill_ids": [skill["id"]]},
    )
    assert credential_version.status_code == 201
    assert client.post(
        f"/api/v1/digital-employees/{credential_employee['id']}/versions/1/publish",
        headers=admin,
    ).status_code == 200
    credential_deployment = client.post(
        "/api/v1/deployments",
        headers=admin,
        json={"employee_id": credential_employee["id"], "version": 1},
    ).json()
    assert client.put(
        f"/api/v1/deployments/{credential_deployment['id']}/runtime-config",
        headers=admin,
        json={
            "expected_config_version": None,
            "mode": "OVERRIDE",
            "execution_profile_id": profile.json()["id"],
            "execution_profile_version": profile.json()["version"],
            "model_route_id": route.json()["route_id"],
        },
    ).status_code == 200
    credential_session = client.post(
        "/api/v1/sessions",
        headers=admin,
        json={"deployment_id": credential_deployment["id"]},
    )
    assert credential_session.status_code == 201
    credential_run = client.post(
        f"/api/v1/sessions/{credential_session.json()['id']}/runs",
        headers=admin,
        json={"prompt": "凭据不可用", "idempotency_key": "codex-runtime-secret-failure"},
    )
    assert credential_run.status_code == 201, credential_run.text
    assert credential_run.json()["status"] == "failed"
    assert credential_run.json()["error_category"] == "ENGINE_CONFIGURATION_REQUIRED"
    assert len(connection.requests) == 3
    client.close()
    adapter.close()
