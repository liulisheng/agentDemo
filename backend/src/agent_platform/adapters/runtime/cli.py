"""受控 Generic CLI Runtime 契约，不允许业务请求直接执行宿主命令。"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from agent_platform.domain.common import DomainError


@dataclass(frozen=True, slots=True)
class CliRuntimeConfig:
    """CLI 参数、工作目录和超时的静态策略。"""

    command: tuple[str, ...]
    allowed_workdirs: tuple[str, ...]
    timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if not self.command or any(not item or any(char in item for char in "\x00\n\r") for item in self.command):
            raise ValueError("command must be a non-empty argument array")
        if any(any(char.isspace() for char in item) for item in self.command[:1]):
            raise ValueError("command executable must not contain whitespace")
        if not self.allowed_workdirs:
            raise ValueError("allowed_workdirs must not be empty")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")


@dataclass(frozen=True, slots=True)
class CliExecutionResult:
    exit_code: int
    stdout: str
    stderr: str


class GenericCliRuntimeAdapter:
    """使用参数数组和受控 cwd 执行 CLI，永远不启用 shell。"""

    def __init__(
        self,
        config: CliRuntimeConfig,
        *,
        runner: Callable[[tuple[str, ...], str, float], tuple[int, str, str]] | None = None,
    ) -> None:
        self.config = config
        self._runner = runner or self._subprocess_runner

    def validate_workdir(self, workdir: str) -> str:
        resolved = str(Path(workdir).resolve())
        allowed = {str(Path(item).resolve()) for item in self.config.allowed_workdirs}
        if resolved not in allowed:
            raise DomainError("AUTHORIZATION_DENIED", "工作目录不在 Runtime 授权范围内")
        return resolved

    def execute(self, prompt: str, workdir: str) -> CliExecutionResult:
        if not prompt.strip():
            raise ValueError("prompt must not be blank")
        resolved = self.validate_workdir(workdir)
        command = (*self.config.command, prompt)
        exit_code, stdout, stderr = self._runner(command, resolved, self.config.timeout_seconds)
        return CliExecutionResult(exit_code, stdout, stderr)

    @staticmethod
    def _subprocess_runner(
        command: tuple[str, ...], cwd: str, timeout: float
    ) -> tuple[int, str, str]:
        completed = subprocess.run(
            list(command),
            cwd=cwd,
            shell=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return completed.returncode, completed.stdout, completed.stderr

class CodexAppServerAdapter(GenericCliRuntimeAdapter):
    """Codex app-server 的参数数组契约，实际协议由供应商进程负责。"""


class CodexExecAdapter(GenericCliRuntimeAdapter):
    """Codex exec 的一次性受控进程契约。"""


class HermesAdapter(GenericCliRuntimeAdapter):
    """Hermes 专用 CLI 的安全进程契约。"""
