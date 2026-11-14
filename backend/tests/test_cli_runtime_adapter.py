from pathlib import Path

import pytest

from agent_platform.adapters.runtime.cli import CliRuntimeConfig, GenericCliRuntimeAdapter
from agent_platform.domain.common import DomainError


def test_cli_config_rejects_shell_like_arguments_and_unapproved_workdir(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        CliRuntimeConfig(command=("sh -c 'danger'",), allowed_workdirs=(str(tmp_path),))

    adapter = GenericCliRuntimeAdapter(
        CliRuntimeConfig(command=("echo",), allowed_workdirs=(str(tmp_path),))
    )
    with pytest.raises(DomainError) as error:
        adapter.validate_workdir("/tmp")
    assert error.value.code == "AUTHORIZATION_DENIED"


def test_cli_runner_receives_argument_array_without_shell(tmp_path: Path) -> None:
    calls: list[tuple[tuple[str, ...], str]] = []

    def runner(command: tuple[str, ...], cwd: str, timeout: float) -> tuple[int, str, str]:
        calls.append((command, cwd))
        return 0, "ok", ""

    adapter = GenericCliRuntimeAdapter(
        CliRuntimeConfig(command=("echo", "hello"), allowed_workdirs=(str(tmp_path),)),
        runner=runner,
    )
    result = adapter.execute("hello", str(tmp_path))

    assert result.exit_code == 0
    assert calls == [(("echo", "hello", "hello"), str(tmp_path))]


def test_codex_and_hermes_adapters_reuse_safe_cli_contract(tmp_path: Path) -> None:
    from agent_platform.adapters.runtime.cli import (
        CodexAppServerAdapter,
        CodexExecAdapter,
        HermesAdapter,
    )

    config = CliRuntimeConfig(("agent",), (str(tmp_path),))
    assert isinstance(CodexAppServerAdapter(config), GenericCliRuntimeAdapter)
    assert isinstance(CodexExecAdapter(config), GenericCliRuntimeAdapter)
    assert isinstance(HermesAdapter(config), GenericCliRuntimeAdapter)
