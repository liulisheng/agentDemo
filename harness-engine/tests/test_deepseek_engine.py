import hashlib
import json
import stat
from datetime import UTC
from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace
from typing import ClassVar

import pytest

from harness_engine.core import (
    EmployeeContext,
    EngineCapabilities,
    EngineNotStartedError,
    EngineRequest,
    EngineToolDefinition,
    HarnessEvent,
)
from harness_engine.engines.deepseek import DeepSeekHarnessEngine
from harness_engine.mcp_proxy import McpProxyConfig, McpProxyLaunch


class FakeHarness:
    instances: ClassVar[list["FakeHarness"]] = []

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.config = SimpleNamespace(env=kwargs.get("env", {}))
        self.closed = False
        self.calls: list[tuple[str, str | None]] = []
        self.started: list[str] = []
        self.resumed: list[str] = []
        type(self).instances.append(self)

    def start_session(self, session_id: str):
        self.started.append(session_id)
        return FakeSession(self, session_id)

    def run(self, prompt: str, *, session_id: str | None = None):
        self.calls.append((prompt, session_id))
        return SimpleNamespace(
            final_response=f"sdk: {prompt}",
            finish_reason="completed",
        )

    def close(self) -> None:
        self.closed = True

    def resume_session(self, session_id: str):
        self.resumed.append(session_id)
        return FakeSession(self, session_id)


class FakeSession:
    def __init__(self, harness: FakeHarness, session_id: str) -> None:
        self.harness = harness
        self.session_id = session_id

    def run(self, prompt: str):
        return self.harness.run(prompt, session_id=self.session_id)


def test_deepseek_engine_maps_sdk_result_and_closes(monkeypatch, tmp_path):
    FakeHarness.instances.clear()
    monkeypatch.setitem(
        __import__("sys").modules,
        "deepseek_harness",
        SimpleNamespace(DeepSeekHarness=FakeHarness),
    )

    engine = DeepSeekHarnessEngine(
        dsh_home=tmp_path / "dsh-home",
        cwd=tmp_path,
        profile="sdk-minimal",
        provider="openai",
        model="gpt-6-luna",
        patches=(str(tmp_path / "provider.patch.yml"),),
    )

    engine.start()
    result = engine.run(EngineRequest(prompt="hello", session_id="session-1"))
    engine.close()
    engine.close()

    assert result.text == "sdk: hello"
    assert result.finish_reason == "completed"
    assert result.capabilities == EngineCapabilities(
        streaming=False, resume=True, approvals=True, mcp=True, cancellation=True
    )
    assert result.completed_at.tzinfo == UTC
    assert FakeHarness.instances[0].calls == [("hello", "session-1")]
    assert FakeHarness.instances[0].resumed == ["session-1"]
    assert FakeHarness.instances[0].closed is True
    assert FakeHarness.instances[0].kwargs["provider"] == "openai"
    assert FakeHarness.instances[0].kwargs["model"] == "gpt-6-luna"


def test_deepseek_engine_accepts_explicit_dsh_bin_from_environment(monkeypatch, tmp_path):
    FakeHarness.instances.clear()
    monkeypatch.setitem(
        __import__("sys").modules,
        "deepseek_harness",
        SimpleNamespace(DeepSeekHarness=FakeHarness),
    )
    monkeypatch.setenv("DSH_BIN", "/opt/harness/bin/dsh")

    DeepSeekHarnessEngine(dsh_home=tmp_path / "dsh-home")

    assert FakeHarness.instances[0].kwargs["dsh_bin"] == "/opt/harness/bin/dsh"


def test_deepseek_engine_generates_mcp_patch_and_disables_sdk_builtin_tools(
    monkeypatch, tmp_path
):
    FakeHarness.instances.clear()
    monkeypatch.setitem(
        __import__("sys").modules,
        "deepseek_harness",
        SimpleNamespace(DeepSeekHarness=FakeHarness),
    )

    engine = DeepSeekHarnessEngine(
        dsh_home=tmp_path / "dsh-home",
        mcp_tools=("echo",),
    )

    patch_paths = FakeHarness.instances[0].kwargs["patches"]
    assert len(patch_paths) == 2
    patch_text = Path(patch_paths[0]).read_text(encoding="utf-8")
    assert "@deepseek-ai/dsh-mcp-client" in patch_text
    assert 'serverName: "python-tools"' in patch_text
    assert "transport: stdio" in patch_text
    assert "command:" in patch_text
    assert '"-m"' in patch_text
    assert '"harness_engine.mcp_server"' in patch_text
    assert '"--enable"' in patch_text
    assert '"echo"' in patch_text
    tool_policy = Path(patch_paths[1]).read_text(encoding="utf-8")
    assert "terminal-bash" in tool_policy
    assert "persistent-bash" in tool_policy
    assert "disabled: true" in tool_policy
    engine.close()


def test_deepseek_runs_share_session_home_but_keep_mcp_overlays_isolated(
    monkeypatch, tmp_path
):
    FakeHarness.instances.clear()
    monkeypatch.setitem(
        __import__("sys").modules,
        "deepseek_harness",
        SimpleNamespace(DeepSeekHarness=FakeHarness),
    )
    session_home = tmp_path / "shared-session-home"
    first_tool = EngineToolDefinition(
        "echo",
        "回显文本",
        {"type": "object", "properties": {"text": {"type": "string"}}},
        "READ",
    )
    second_tool = EngineToolDefinition(
        "list_projects",
        "列出项目",
        {"type": "object", "properties": {}},
        "READ",
    )
    first_context = EmployeeContext(
        employee_id="employee-a",
        employee_version=1,
        system_prompt="员工 A 指令",
    )
    second_context = EmployeeContext(
        employee_id="employee-b",
        employee_version=4,
        system_prompt="员工 B 指令",
    )
    first = DeepSeekHarnessEngine(
        dsh_home=session_home,
        employee_context=first_context,
        tool_definitions=(first_tool,),
    )
    first_patch = Path(FakeHarness.instances[0].kwargs["patches"][0])
    first_patch_contents = first_patch.read_text(encoding="utf-8")
    second = DeepSeekHarnessEngine(
        dsh_home=session_home,
        employee_context=second_context,
        tool_definitions=(second_tool,),
    )
    second_patch = Path(FakeHarness.instances[1].kwargs["patches"][0])

    assert first_patch != second_patch
    assert first_patch.parent != session_home
    assert "echo" in first_patch_contents and "list_projects" not in first_patch_contents
    assert "list_projects" in second_patch.read_text(encoding="utf-8")
    assert "echo" not in second_patch.read_text(encoding="utf-8")

    first.start()
    second.start()
    first.close()
    second.close()
    assert not first_patch.exists()
    assert not second_patch.exists()


def test_deepseek_engine_rejects_run_after_close(monkeypatch, tmp_path):
    monkeypatch.setitem(
        __import__("sys").modules,
        "deepseek_harness",
        SimpleNamespace(DeepSeekHarness=FakeHarness),
    )

    engine = DeepSeekHarnessEngine(dsh_home=tmp_path / "dsh-home")
    with pytest.raises(EngineNotStartedError, match="started"):
        engine.run(EngineRequest(prompt="before start"))

    engine.start()
    engine.close()

    with pytest.raises(RuntimeError, match="closed"):
        engine.run(EngineRequest(prompt="hello"))


def test_deepseek_engine_maps_sessions_and_reports_idle_cancel(
    monkeypatch, tmp_path
):
    FakeHarness.instances.clear()
    monkeypatch.setitem(
        __import__("sys").modules,
        "deepseek_harness",
        SimpleNamespace(DeepSeekHarness=FakeHarness),
    )

    engine = DeepSeekHarnessEngine(dsh_home=tmp_path / "dsh-home")
    with pytest.raises(EngineNotStartedError):
        engine.create_session()

    engine.start()
    session = engine.create_session()
    assert session.engine_name == "deepseek"
    assert FakeHarness.instances[0].started == [session.session_id]
    assert engine.capabilities.resume is True
    resumed = engine.resume_session(session)
    assert resumed.session_id == session.session_id
    assert FakeHarness.instances[0].resumed == [session.session_id]

    cancelled = engine.cancel(session.session_id)
    assert cancelled.status.value == "not_running"


def test_deepseek_cancel_closes_the_run_scoped_harness_and_unblocks_prompt(
    monkeypatch, tmp_path
):
    class BlockingHarness(FakeHarness):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.run_started = Event()
            self.closed_event = Event()

        def run(self, prompt: str, *, session_id: str | None = None):
            self.run_started.set()
            if not self.closed_event.wait(timeout=2):
                raise TimeoutError("Harness close did not stop the active prompt")
            raise RuntimeError("Harness transport closed")

        def close(self) -> None:
            self.closed_event.set()
            super().close()

    BlockingHarness.instances.clear()
    monkeypatch.setitem(
        __import__("sys").modules,
        "deepseek_harness",
        SimpleNamespace(DeepSeekHarness=BlockingHarness),
    )
    engine = DeepSeekHarnessEngine(dsh_home=tmp_path / "session-home")
    engine.start()
    session = engine.create_session()
    outcomes = []
    thread = Thread(
        target=lambda: outcomes.append(
            engine.run(
                EngineRequest(
                    prompt="阻塞请求",
                    session=session,
                    run_id="run-cancel",
                )
            )
        )
    )
    thread.start()
    assert BlockingHarness.instances[0].run_started.wait(timeout=1)

    try:
        result = engine.cancel(session.session_id)
    finally:
        engine.close()
        thread.join(timeout=2)

    assert result.confirmed is True
    assert not thread.is_alive()
    assert outcomes[0].status.value == "cancelled"
    assert BlockingHarness.instances[0].closed is True


def test_deepseek_engine_converts_vendor_events_to_neutral_events(
    monkeypatch, tmp_path
):
    class EventHarness(FakeHarness):
        def run(self, prompt: str, *, session_id: str | None = None):
            return SimpleNamespace(
                final_response=f"sdk: {prompt}",
                finish_reason="completed",
                run_id="run-1",
                events=[
                    SimpleNamespace(
                        type="skill_started",
                        session_id=session_id,
                        payload={"skill": "summarize"},
                    )
                ],
            )

    monkeypatch.setitem(
        __import__("sys").modules,
        "deepseek_harness",
        SimpleNamespace(DeepSeekHarness=EventHarness),
    )

    engine = DeepSeekHarnessEngine(dsh_home=tmp_path / "dsh-home")
    engine.start()
    seen = []
    engine.run(
        EngineRequest(prompt="hello", session_id="session-1"),
        on_event=seen.append,
    )

    assert len(seen) == 1
    assert isinstance(seen[0], HarnessEvent)
    assert seen[0].event_type == "skill_started"
    assert seen[0].payload == {"skill": "summarize"}


def test_deepseek_engine_supplies_employee_and_skill_context_as_system_prompt(
    monkeypatch, tmp_path
):
    FakeHarness.instances.clear()
    monkeypatch.setitem(
        __import__("sys").modules,
        "deepseek_harness",
        SimpleNamespace(DeepSeekHarness=FakeHarness),
    )

    DeepSeekHarnessEngine(
        dsh_home=tmp_path / "dsh-home",
        system_prompt="只处理数仓工作流。",
        skill_instructions=("先查询项目，再生成预览。",),
        skill_files=(("SKILL.md", "创建工作流必须保持 OFFLINE。"),),
    )

    assert FakeHarness.instances[0].kwargs["env"]["DSH_SYSTEM_PROMPT"] == (
        "只处理数仓工作流。\n\n"
        "Skill instructions:\n先查询项目，再生成预览。\n\n"
        "Skill files:\n[SKILL.md]\n创建工作流必须保持 OFFLINE。"
    )


def test_deepseek_run_accepts_context_matching_its_run_scoped_harness_configuration(
    monkeypatch, tmp_path
):
    FakeHarness.instances.clear()
    monkeypatch.setitem(
        __import__("sys").modules,
        "deepseek_harness",
        SimpleNamespace(DeepSeekHarness=FakeHarness),
    )
    employee_context = EmployeeContext(
        employee_id="employee-1",
        employee_version=2,
        system_prompt="只处理当前员工任务",
        skill_instructions=("调用前核对项目",),
    )
    tool = EngineToolDefinition(
        "echo",
        "回显文本",
        {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
        "READ",
    )
    engine = DeepSeekHarnessEngine(
        dsh_home=tmp_path / "session-home",
        system_prompt=employee_context.system_prompt,
        skill_instructions=employee_context.skill_instructions,
        mcp_tools=(tool.name,),
        employee_context=employee_context,
        tool_definitions=(tool,),
    )
    engine.start()
    result = engine.run(
        EngineRequest(
            prompt="只处理当前对话",
            session_id="run-session-1",
            employee_context=employee_context,
            tool_definitions=(tool,),
        )
    )

    assert result.text == "sdk: 只处理当前对话"
    assert FakeHarness.instances[0].kwargs["env"]["DSH_SYSTEM_PROMPT"] == (
        "只处理当前员工任务\n\nSkill instructions:\n调用前核对项目"
    )
    assert FakeHarness.instances[0].calls == [("只处理当前对话", "run-session-1")]


def test_deepseek_for_run_request_uses_opaque_session_home_and_run_context(
    monkeypatch, tmp_path
):
    FakeHarness.instances.clear()
    monkeypatch.setitem(
        __import__("sys").modules,
        "deepseek_harness",
        SimpleNamespace(DeepSeekHarness=FakeHarness),
    )
    employee_context = EmployeeContext(
        employee_id="employee-1",
        employee_version=3,
        system_prompt="只运行当前固定员工版本",
        skill_instructions=("先只读查询",),
    )
    tool = EngineToolDefinition(
        "echo",
        "回显文本",
        {"type": "object", "properties": {"text": {"type": "string"}}},
        "READ",
    )
    request = EngineRequest(
        prompt="查询项目",
        session_id="deepseek-existing-session",
        run_id="run-fixed-context",
        employee_context=employee_context,
        tool_definitions=(tool,),
        harness_home_ref="harness-session:" + "b" * 64,
    )

    engine = DeepSeekHarnessEngine.for_run_request(
        request,
        harness_state_root=tmp_path,
    )

    assert FakeHarness.instances[0].kwargs["dsh_home"] == str(
        tmp_path / "harness-sessions" / ("b" * 64)
    )
    assert FakeHarness.instances[0].kwargs["cwd"] == str(
        tmp_path / "harness-sessions" / ("b" * 64)
    )
    assert stat.S_IMODE((tmp_path / "harness-sessions").stat().st_mode) == 0o700
    assert stat.S_IMODE(
        (tmp_path / "harness-sessions" / ("b" * 64)).stat().st_mode
    ) == 0o700
    assert FakeHarness.instances[0].kwargs["env"]["DSH_SYSTEM_PROMPT"].startswith(
        "只运行当前固定员工版本"
    )
    assert "echo" in Path(FakeHarness.instances[0].kwargs["patches"][0]).read_text(
        encoding="utf-8"
    )
    engine.close()


def test_deepseek_run_patch_launches_filtered_stdio_proxy_without_writing_secrets(
    monkeypatch, tmp_path
):
    FakeHarness.instances.clear()
    monkeypatch.setitem(
        __import__("sys").modules,
        "deepseek_harness",
        SimpleNamespace(DeepSeekHarness=FakeHarness),
    )
    schema = {
        "type": "object",
        "properties": {"page": {"type": "integer"}},
        "additionalProperties": False,
    }
    canonical_catalog = json.dumps(
        [{"name": "projects_list", "input_schema": schema}],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(canonical_catalog.encode("utf-8")).hexdigest()
    schema_digest = hashlib.sha256(
        json.dumps(schema, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    config = McpProxyConfig(
        command="/usr/bin/python3",
        args=("-m", "dolphinscheduler_mcp"),
        cwd="/srv/dolphin-mcp",
        profile_tool_names=("projects_list",),
        run_allowed_tool_names=("projects_list",),
        profile_catalog_sha256=digest,
        expected_tool_schema_sha256={"projects_list": schema_digest},
        environment_names=("DS_TOKEN",),
    )
    launch = McpProxyLaunch(
        server_name="dolphin",
        config=config,
        environment={"DS_TOKEN": "fake-secret-value"},
    )
    tool = EngineToolDefinition(
        name="projects_list",
        description="列出项目",
        input_schema=schema,
        risk_level="READ",
        remote_tool_name="projects_list",
    )

    engine = DeepSeekHarnessEngine(
        dsh_home=tmp_path / "session-home",
        tool_definitions=(tool,),
        mcp_proxy_launches=(launch,),
    )
    patch_path = Path(FakeHarness.instances[0].kwargs["patches"][0])
    overlay = patch_path.parent
    patch_text = patch_path.read_text(encoding="utf-8")
    config_text = (overlay / "mcp-proxy-dolphin.json").read_text(encoding="utf-8")

    assert "mcp_proxy.py" in patch_text
    assert "projects_list" in config_text
    assert "workflow_create" not in config_text
    assert "DS_TOKEN" in patch_text
    assert "fake-secret-value" not in patch_text + config_text
    assert "fake-secret-value" in " ".join(FakeHarness.instances[0].kwargs["env"].values())
    assert stat.S_IMODE((overlay / "mcp-proxy-dolphin.json").stat().st_mode) == 0o600
    engine.close()
    assert "fake-secret-value" not in FakeHarness.instances[0].config.env.values()
    assert not overlay.exists()


def test_deepseek_sdk_process_receives_only_model_and_run_mcp_environment(
    monkeypatch, tmp_path
):
    FakeHarness.instances.clear()
    monkeypatch.setitem(
        __import__("sys").modules,
        "deepseek_harness",
        SimpleNamespace(DeepSeekHarness=FakeHarness),
    )
    monkeypatch.setenv("MYSQL_PASSWORD", "database-secret")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "model-secret")
    engine = DeepSeekHarnessEngine(
        dsh_home=tmp_path / "session-home",
        env={"HARNESS_MCP_TEMP_TOKEN": "run-mcp-secret"},
    )

    child_environment = FakeHarness.instances[0].kwargs["env"]
    assert child_environment["MYSQL_PASSWORD"] == ""
    assert child_environment["DEEPSEEK_API_KEY"] == "model-secret"
    assert child_environment["HARNESS_MCP_TEMP_TOKEN"] == "run-mcp-secret"
    assert "database-secret" not in repr(child_environment)
    engine.close()


def test_deepseek_for_run_request_uses_pinned_provider_config_and_env_credential(
    monkeypatch, tmp_path
):
    from harness_engine import core

    assert hasattr(core, "EngineModelConfig"), "EngineModelConfig is missing"
    assert "model_config" in EngineRequest.__dataclass_fields__, "EngineRequest must carry ModelRoute config"
    FakeHarness.instances.clear()
    monkeypatch.setitem(
        __import__("sys").modules,
        "deepseek_harness",
        SimpleNamespace(DeepSeekHarness=FakeHarness),
    )
    employee_context = EmployeeContext(
        employee_id="employee-model-config",
        employee_version=1,
        system_prompt="只验证模型连接。",
    )
    model_config = core.EngineModelConfig(
        tenant_id="tenant-a",
        route_id="route-a",
        route_version=2,
        connection_id="connection-a",
        connection_version=4,
        provider_kind="openai-compatible",
        model_id="local-model-a",
        protocol="openai-chat-completions",
        endpoint="http://host.docker.internal:63072/v1",
        parameters={"temperature": 0.2},
    )
    request = EngineRequest(
        prompt="只回复 READY",
        session_id="session-model-config",
        harness_home_ref="harness-session:" + "a" * 64,
        employee_context=employee_context,
        model_config=model_config,
    )
    local_config_path = tmp_path / "legacy-provider.json"
    local_config_path.write_text(
        json.dumps(
            {
                "provider": "legacy-local-provider",
                "model": "legacy-model",
                "base_url": "http://legacy.invalid/v1",
                "api_key": "legacy-local-config-key",
                "api": "openai-completions",
            }
        ),
        encoding="utf-8",
    )
    engine = DeepSeekHarnessEngine.for_run_request(
        request,
        harness_state_root=tmp_path,
        profile="sdk-minimal",
        env={"AGENT_PLATFORM_MODEL_API_KEY": "provider-key-test"},
        local_config=local_config_path,
    )

    engine.start()
    result = engine.run(request)
    harness = FakeHarness.instances[-1]
    patch_text = "\n".join(
        Path(path).read_text(encoding="utf-8")
        for path in harness.kwargs.get("patches", ())
    )
    child_environment = dict(harness.kwargs["env"])
    engine.close()

    assert result.text == "sdk: 只回复 READY"
    assert harness.kwargs["provider"] == "openai-compatible"
    assert harness.kwargs["model"] == "local-model-a"
    assert child_environment["AGENT_PLATFORM_MODEL_API_KEY"] == "provider-key-test"
    assert "legacy-local-config-key" not in child_environment.values()
    assert "AGENT_PLATFORM_MODEL_API_KEY" not in harness.kwargs["env"]
    assert "host.docker.internal:63072/v1" in patch_text
    assert "local-model-a" in patch_text
    assert "temperature" in patch_text
    assert "provider-key-test" not in patch_text
