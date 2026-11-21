"""应用模式必须明确，MySQL 依赖不允许静默回退到 Fake。"""

import pytest
from fastapi.testclient import TestClient

from agent_platform.api.app import create_app, create_app_from_environment
from agent_platform.application.catalog_service import InMemoryCatalogRepository


def test_mysql_mode_fails_closed_without_all_persistent_adapters() -> None:
    with pytest.raises(ValueError, match="完整注入"):
        create_app(mode="mysql")


def test_environment_entrypoint_requires_explicit_mode(monkeypatch) -> None:
    monkeypatch.delenv("AGENT_PLATFORM_MODE", raising=False)
    with pytest.raises(RuntimeError, match="显式设置"):
        create_app_from_environment()


def test_environment_fake_mode_is_explicit_and_needs_no_mysql(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_PLATFORM_MODE", "fake")
    monkeypatch.delenv("MYSQL_HOST", raising=False)
    with TestClient(create_app_from_environment()) as client:
        assert client.get("/api/health").json() == {"status": "ok"}


def test_app_factory_injects_the_selected_runtime_adapter() -> None:
    runtime = object()
    app = create_app(mode="fake", runtime_adapter=runtime)

    assert app.state.control_plane_service.runtime is runtime


def test_deepseek_runtime_factory_uses_a_session_home_and_run_scoped_context(
    monkeypatch, tmp_path
) -> None:
    from types import SimpleNamespace

    from agent_platform.api.app import _build_runtime_adapter
    from agent_platform.ports.runtime import (
        RuntimeEmployeeContext,
        RuntimeRequest,
        RuntimeToolDefinition,
    )

    class FakeSession:
        def __init__(self, harness, session_id):
            self.harness = harness
            self.session_id = session_id

        def run(self, prompt):
            return SimpleNamespace(
                final_response=f"fake-deepseek: {prompt}", finish_reason="completed"
            )

    class FakeHarness:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def start_session(self, session_id):
            return FakeSession(self, session_id)

        def resume_session(self, session_id):
            return FakeSession(self, session_id)

        def close(self):
            return None

    monkeypatch.setitem(__import__("sys").modules, "deepseek_harness", SimpleNamespace(DeepSeekHarness=FakeHarness))
    runtime = _build_runtime_adapter("deepseek", tmp_path)
    session_ref = runtime.create_session()
    context = RuntimeEmployeeContext(
        employee_id="employee-dw",
        employee_version=2,
        system_prompt="只处理当前数仓员工任务",
        skill_instructions=("先只读查询",),
    )
    tool = RuntimeToolDefinition(
        "tool-projects-list",
        1,
        "echo",
        "安全只读示例工具",
        {"type": "object", "properties": {"text": {"type": "string"}}},
        "READ",
    )

    result = runtime.run(
        RuntimeRequest(
            run_id="run-dw-1",
            session_ref=session_ref,
            prompt="列出项目",
            skill_instructions=context.skill_instructions,
            allowed_tools=(tool.name,),
            employee_context=context,
            tool_definitions=(tool,),
            harness_home_ref="harness-session:" + "c" * 64,
        )
    )

    assert result.text == "fake-deepseek: 列出项目"
    assert result.runtime_session_ref.startswith("deepseek-")
    assert (tmp_path / "harness-sessions" / ("c" * 64)).is_dir()


def test_deepseek_runtime_factory_loads_explicit_local_provider_config(monkeypatch, tmp_path) -> None:
    import json
    import sys
    from pathlib import Path
    from types import SimpleNamespace
    from typing import ClassVar

    from agent_platform.api.app import _build_runtime_adapter
    from agent_platform.ports.runtime import RuntimeEmployeeContext, RuntimeRequest

    class FakeSession:
        def __init__(self, session_id):
            self.session_id = session_id

        def run(self, prompt):
            return SimpleNamespace(final_response=f"local: {prompt}", finish_reason="completed")

    class FakeHarness:
        instances: ClassVar[list[object]] = []

        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.patch_texts = tuple(
                Path(path).read_text(encoding="utf-8")
                for path in kwargs.get("patches", ())
            )
            type(self).instances.append(self)

        def start_session(self, session_id):
            return FakeSession(session_id)

        def resume_session(self, session_id):
            return FakeSession(session_id)

        def close(self):
            return None

    FakeHarness.instances.clear()
    monkeypatch.setitem(sys.modules, "deepseek_harness", SimpleNamespace(DeepSeekHarness=FakeHarness))
    config_path = tmp_path / "local-provider.json"
    config_path.write_text(
        json.dumps(
            {
                "provider": "local-acceptance",
                "model": "acceptance-model",
                "base_url": "http://host.docker.internal:52139/v1",
                "api_key": "local-acceptance-key",
                "api": "openai-completions",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_PLATFORM_DEEPSEEK_PROVIDER", "local-acceptance")
    monkeypatch.setenv("AGENT_PLATFORM_DEEPSEEK_MODEL", "acceptance-model")
    monkeypatch.setenv("AGENT_PLATFORM_DEEPSEEK_LOCAL_CONFIG", str(config_path))
    runtime = _build_runtime_adapter("deepseek", tmp_path / "state")
    context = RuntimeEmployeeContext(
        employee_id="employee-local",
        employee_version=1,
        system_prompt="只用于本地验收。",
    )

    result = runtime.run(
        RuntimeRequest(
            run_id="run-local-provider",
            session_ref=runtime.create_session(),
            prompt="检查本地模型配置",
            skill_instructions=(),
            allowed_tools=(),
            employee_context=context,
            harness_home_ref="harness-session:" + "f" * 64,
        )
    )

    assert result.text == "local: 检查本地模型配置"
    harness = FakeHarness.instances[0]
    assert harness.kwargs["provider"] == "local-acceptance"
    assert harness.kwargs["model"] == "acceptance-model"
    assert harness.kwargs["env"]["LOCAL_OPENAI_API_KEY"] == "local-acceptance-key"
    provider_patches = [text for text in harness.patch_texts if "apiKeyEnv" in text]
    assert provider_patches and "local-acceptance-key" not in provider_patches[0]


def test_deepseek_runtime_resolves_pinned_tenant_model_secret_inside_worker(
    monkeypatch, tmp_path
) -> None:
    import sys
    from pathlib import Path
    from types import SimpleNamespace
    from typing import ClassVar

    from agent_platform.api.app import _build_runtime_adapter
    from agent_platform.domain.model_routes import ModelRouteSnapshot, SecretRef
    from agent_platform.ports.runtime import RuntimeEmployeeContext, RuntimeRequest

    class FakeSession:
        def __init__(self, session_id):
            self.session_id = session_id

        def run(self, prompt):
            return SimpleNamespace(final_response=f"fake: {prompt}", finish_reason="completed")

    class FakeHarness:
        instances: ClassVar[list[object]] = []

        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.config = SimpleNamespace(env=kwargs.get("env", {}))
            self.child_environment = dict(kwargs.get("env", {}))
            self.patch_texts = tuple(
                Path(path).read_text(encoding="utf-8")
                for path in kwargs.get("patches", ())
                if "provider" in Path(path).name
            )
            type(self).instances.append(self)

        def start_session(self, session_id):
            return FakeSession(session_id)

        def resume_session(self, session_id):
            return FakeSession(session_id)

        def close(self):
            return None

    FakeHarness.instances.clear()
    monkeypatch.setitem(sys.modules, "deepseek_harness", SimpleNamespace(DeepSeekHarness=FakeHarness))
    secret_ref = SecretRef("local-model-key", "tenant-a", "environment")
    monkeypatch.setenv(
        "AGENT_PLATFORM_MODEL_SECRET_TENANT_A_LOCAL_MODEL_KEY",
        "tenant-model-key-test",
    )
    runtime = _build_runtime_adapter("deepseek", tmp_path / "state")
    snapshot = ModelRouteSnapshot(
        tenant_id="tenant-a",
        route_id="route-local",
        route_version=2,
        route_alias="assistant",
        connection_id="connection-local",
        connection_version=3,
        provider_kind="openai-compatible",
        model_id="local-model-a",
        protocol="openai-chat-completions",
        endpoint="http://host.docker.internal:63072/v1",
        secret_ref=secret_ref,
        parameters={"temperature": 0.1},
    )
    result = runtime.run(
        RuntimeRequest(
            run_id="run-tenant-model",
            session_ref=runtime.create_session(),
            prompt="检查本地模型配置",
            skill_instructions=(),
            allowed_tools=(),
            employee_context=RuntimeEmployeeContext(
                employee_id="employee-local-model",
                employee_version=1,
                system_prompt="只用于验证固定模型路由。",
                model_route_id="route-local",
            ),
            harness_home_ref="harness-session:" + "9" * 64,
            model_route_snapshot=snapshot,
        )
    )

    harness = FakeHarness.instances[0]
    assert result.text == "fake: 检查本地模型配置"
    assert harness.kwargs["provider"] == "openai-compatible"
    assert harness.kwargs["model"] == "local-model-a"
    assert harness.child_environment["AGENT_PLATFORM_MODEL_API_KEY"] == "tenant-model-key-test"
    assert harness.patch_texts
    assert "host.docker.internal:63072/v1" in harness.patch_texts[0]
    assert "local-model-a" in harness.patch_texts[0]
    assert "tenant-model-key-test" not in harness.patch_texts[0]
    assert "tenant-model-key-test" not in repr(snapshot)
    assert "local-model-key" not in repr(snapshot)


def test_deepseek_runtime_builds_filtered_profile_proxy_without_persisting_secret(
    monkeypatch, tmp_path
) -> None:
    import json
    import sys
    from pathlib import Path
    from types import SimpleNamespace
    from typing import ClassVar

    from agent_platform.adapters.mcp.stdio import McpToolDefinition, compute_tool_catalog_sha256
    from agent_platform.api.app import _build_runtime_adapter
    from agent_platform.domain.catalog import compute_tool_input_schema_sha256
    from agent_platform.domain.mcp import McpProfileStatus, McpServerProfile
    from agent_platform.ports.runtime import (
        RuntimeEmployeeContext,
        RuntimeMcpServer,
        RuntimeRequest,
        RuntimeToolDefinition,
    )

    class FakeSession:
        def __init__(self, harness, session_id):
            self.harness = harness
            self.session_id = session_id

        def run(self, prompt):
            return SimpleNamespace(final_response=f"fake: {prompt}", finish_reason="completed")

    class FakeHarness:
        instances: ClassVar[list[object]] = []

        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.config = SimpleNamespace(env=kwargs.get("env", {}))
            self.patch_paths = tuple(Path(path) for path in kwargs.get("patches", ()))
            self.patch_texts = tuple(
                Path(path).read_text(encoding="utf-8")
                for path in kwargs.get("patches", ())
            )
            config_paths = {
                config_path
                for path in kwargs.get("patches", ())
                for config_path in Path(path).parent.glob("mcp-proxy-*.json")
            }
            self.proxy_config_texts = tuple(
                path.read_text(encoding="utf-8")
                for path in sorted(config_paths)
            )
            type(self).instances.append(self)

        def start_session(self, session_id):
            return FakeSession(self, session_id)

        def resume_session(self, session_id):
            return FakeSession(self, session_id)

        def close(self):
            return None

    FakeHarness.instances.clear()
    monkeypatch.setitem(
        __import__("sys").modules,
        "deepseek_harness",
        SimpleNamespace(DeepSeekHarness=FakeHarness),
    )
    working_directory = tmp_path / "dolphin-mcp"
    working_directory.mkdir()
    monkeypatch.setenv("DOLPHIN_TOKEN", "profile-secret-token")
    monkeypatch.setenv("DOLPHIN_MCP_CWD", str(working_directory))
    monkeypatch.setenv("AGENT_PLATFORM_MCP_ALLOWED_COMMANDS", json.dumps([sys.executable]))
    monkeypatch.setenv(
        "AGENT_PLATFORM_MCP_ALLOWED_WORKING_DIRECTORY_ROOTS", json.dumps([str(tmp_path)])
    )
    schema = {"type": "object", "properties": {"page": {"type": "integer"}}}
    digest = compute_tool_input_schema_sha256(schema)
    profile = McpServerProfile(
        platform_id="plt-local",
        tenant_id="tenant-a",
        profile_id="profile-dolphin",
        version=2,
        name="Dolphin MCP",
        transport="stdio",
        command=sys.executable,
        args=("-m", "dolphin_mcp"),
        working_directory_ref="env-path://tenant-a/DOLPHIN_MCP_CWD",
        enabled_tool_names=("projects_list", "workflow_create"),
        tool_catalog_sha256=compute_tool_catalog_sha256(
            (
                McpToolDefinition("projects_list", "列项目", schema),
                McpToolDefinition("workflow_create", "建工作流", {"type": "object"}),
            )
        ),
        environment_secret_refs={"DOLPHIN_TOKEN": "env://tenant-a/DOLPHIN_TOKEN"},
        status=McpProfileStatus.ACTIVE,
    )
    runtime_server = RuntimeMcpServer(
        profile=profile,
        run_allowed_tool_names=("projects_list",),
        expected_tool_schema_sha256={"projects_list": digest},
    )
    employee = RuntimeEmployeeContext(
        employee_id="employee-dw",
        employee_version=2,
        system_prompt="只查询当前数仓项目",
    )
    tool = RuntimeToolDefinition(
        tool_id="tool-projects-list",
        tool_version=1,
        name="projects_list",
        description="列出项目",
        input_schema=schema,
        risk_level="READ",
        remote_tool_name="projects_list",
    )
    runtime = _build_runtime_adapter("deepseek", tmp_path)

    result = runtime.run(
        RuntimeRequest(
            run_id="run-profile-proxy",
            session_ref=runtime.create_session(),
            prompt="列出项目",
            skill_instructions=(),
            allowed_tools=(tool.name,),
            employee_context=employee,
            tool_definitions=(tool,),
            harness_home_ref="harness-session:" + "e" * 64,
            mcp_servers=(runtime_server,),
        )
    )

    assert result.text == "fake: 列出项目"
    fake_harness = FakeHarness.instances[0]
    proxy_patch = next(
        text
        for path, text in zip(fake_harness.patch_paths, fake_harness.patch_texts, strict=True)
        if "mcp-proxy.patch" in str(path)
    )
    assert len(fake_harness.proxy_config_texts) == 1
    assert "mcp_proxy.py" in proxy_patch
    proxy_config = json.loads(fake_harness.proxy_config_texts[0])
    assert proxy_config["run_allowed_tool_names"] == ["projects_list"]
    assert proxy_config["profile_tool_names"] == ["projects_list", "workflow_create"]
    assert "profile-secret-token" not in proxy_patch
    assert "profile-secret-token" not in fake_harness.proxy_config_texts[0]
    assert "profile-secret-token" not in fake_harness.config.env.values()


def test_environment_mysql_mode_requires_explicit_connection_config(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_PLATFORM_MODE", "mysql")
    monkeypatch.setenv("AGENT_PLATFORM_IDENTITY_PROVIDER", "fake")
    for name in ("MYSQL_HOST", "MYSQL_DATABASE", "MYSQL_USER", "MYSQL_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(RuntimeError, match="缺少环境变量"):
        create_app_from_environment()


def test_mysql_application_closes_resources_on_shutdown() -> None:
    class Resource:
        closed = 0

        def close(self) -> None:
            self.closed += 1

    resource = Resource()
    app = create_app(
        mode="mysql",
        catalog_repository=InMemoryCatalogRepository(),
        execution_repository=object(),
        conversation_repository=object(),
        mcp_repository=object(),
        resource_center_repository=object(),
        object_store=object(),
        lifecycle_resources=(resource,),
    )
    with TestClient(app) as client:
        assert client.get("/api/health").json() == {"status": "ok"}
    assert resource.closed == 1
