from datetime import UTC, datetime
from pathlib import Path

import pytest

from harness_engine import cli
from harness_engine.core import EngineCapabilities, EngineResult


class RecordingEngine:
    capabilities = EngineCapabilities()
    last_kwargs: dict[str, object] | None = None
    last_instance: "RecordingEngine | None" = None

    def __init__(self, **kwargs: object) -> None:
        type(self).last_kwargs = kwargs
        type(self).last_instance = self
        self.started = False
        self.closed = False

    def start(self):
        self.started = True

    def run(self, request):
        assert self.started is True
        return EngineResult(
            text=f"recorded: {request.prompt}",
            finish_reason="completed",
            completed_at=datetime.now(UTC),
            capabilities=self.capabilities,
        )

    def close(self):
        self.closed = True


def test_cli_selects_deepseek_engine(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli, "DeepSeekHarnessEngine", RecordingEngine)

    exit_code = cli.main(
        [
            "--engine",
            "deepseek",
            "--dsh-home",
            str(tmp_path / "dsh-home"),
            "--provider",
            "openai",
            "--model",
            "gpt-6-luna",
            "hello",
        ]
    )

    assert exit_code == 0
    assert capsys.readouterr().out.strip() == "recorded: hello"
    assert RecordingEngine.last_instance is not None
    assert RecordingEngine.last_instance.started is True
    assert RecordingEngine.last_instance.closed is True
    assert RecordingEngine.last_kwargs == {
        "dsh_home": tmp_path / "dsh-home",
        "cwd": Path.cwd(),
        "provider": "openai",
        "model": "gpt-6-luna",
        "profile": None,
        "max_tokens": None,
        "mcp_tools": (),
        "local_config": Path(".artifacts/local-openai.json"),
    }


def test_cli_closes_engine_when_run_fails(monkeypatch, tmp_path):
    class FailingEngine(RecordingEngine):
        def run(self, request):
            assert self.started is True
            raise RuntimeError("run failed")

    monkeypatch.setattr(cli, "DeepSeekHarnessEngine", FailingEngine)

    with pytest.raises(RuntimeError, match="run failed"):
        cli.main(
            [
                "--engine",
                "deepseek",
                "--dsh-home",
                str(tmp_path / "dsh-home"),
                "hello",
            ]
        )

    assert FailingEngine.last_instance is not None
    assert FailingEngine.last_instance.closed is True


def test_cli_startup_only_starts_and_closes_without_running(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "DeepSeekHarnessEngine", RecordingEngine)

    exit_code = cli.main(
        [
            "--engine",
            "deepseek",
            "--dsh-home",
            str(tmp_path / "dsh-home"),
            "--startup-only",
            "hello",
        ]
    )

    assert exit_code == 0
    assert RecordingEngine.last_instance is not None
    assert RecordingEngine.last_instance.started is True
    assert RecordingEngine.last_instance.closed is True


def test_cli_passes_repeated_mcp_tools_to_deepseek(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli, "DeepSeekHarnessEngine", RecordingEngine)

    exit_code = cli.main(
        [
            "--engine",
            "deepseek",
            "--dsh-home",
            str(tmp_path / "dsh-home"),
            "--mcp-tool",
            "echo",
            "--mcp-tool",
            "other",
            "hello",
        ]
    )

    assert exit_code == 0
    assert capsys.readouterr().out.strip() == "recorded: hello"
    assert RecordingEngine.last_kwargs is not None
    assert RecordingEngine.last_kwargs["mcp_tools"] == ("echo", "other")
