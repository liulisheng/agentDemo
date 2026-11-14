from datetime import UTC
from types import SimpleNamespace
from typing import ClassVar

import pytest

from harness_engine.core import (
    EngineCapabilities,
    EngineNotStartedError,
    EngineRequest,
    HarnessEvent,
    UnsupportedCapabilityError,
)
from harness_engine.engines.deepseek import DeepSeekHarnessEngine


class FakeHarness:
    instances: ClassVar[list["FakeHarness"]] = []

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.closed = False
        self.calls: list[tuple[str, str | None]] = []
        type(self).instances.append(self)

    def run(self, prompt: str, *, session_id: str | None = None):
        self.calls.append((prompt, session_id))
        return SimpleNamespace(
            final_response=f"sdk: {prompt}",
            finish_reason="completed",
        )

    def close(self) -> None:
        self.closed = True


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
        streaming=False, resume=False, approvals=True, mcp=True
    )
    assert result.completed_at.tzinfo == UTC
    assert FakeHarness.instances[0].calls == [("hello", "session-1")]
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


def test_deepseek_engine_generates_mcp_stdio_patch_for_enabled_tools(
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
        mcp_tools=("echo",),
    )

    patch_paths = FakeHarness.instances[0].kwargs["patches"]
    assert len(patch_paths) == 1
    patch_text = (tmp_path / "dsh-home" / "mcp-tools.patch.yml").read_text(
        encoding="utf-8"
    )
    assert "@deepseek-ai/dsh-mcp-client" in patch_text
    assert 'serverName: "python-tools"' in patch_text
    assert "transport: stdio" in patch_text
    assert "command:" in patch_text
    assert '"-m"' in patch_text
    assert '"harness_engine.mcp_server"' in patch_text
    assert '"--enable"' in patch_text
    assert '"echo"' in patch_text


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


def test_deepseek_engine_maps_sessions_and_rejects_unverified_cancel(
    monkeypatch, tmp_path
):
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
    with pytest.raises(UnsupportedCapabilityError, match="resume"):
        engine.resume_session(session)

    with pytest.raises(UnsupportedCapabilityError, match="cancel"):
        engine.cancel(session.session_id)


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
