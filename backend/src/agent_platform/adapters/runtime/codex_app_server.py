"""基于 Codex app-server stdio 协议的受控 Session/Run RuntimeAdapter。"""

from __future__ import annotations

import hashlib
import json
import os
import select
import shlex
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from agent_platform.domain.common import DomainError
from agent_platform.domain.execution import RuntimeStatus, UsageRecord
from agent_platform.domain.model_routes import ModelRouteSnapshot, SecretRef
from agent_platform.ports.runtime import (
    RuntimeCapabilities,
    RuntimeEvent,
    RuntimeRequest,
    RuntimeResult,
)


class CodexAppServerConnection(Protocol):
    """Codex app-server 单连接 JSON-RPC 交换边界，可用受控假服务器测试。"""

    def request(
        self,
        method: str,
        params: Mapping[str, object],
        *,
        timeout_seconds: float | None = None,
    ) -> Mapping[str, object]:
        ...

    def notify(self, method: str, params: Mapping[str, object]) -> None:
        ...

    def wait_for_turn(
        self, thread_id: str, turn_id: str, timeout_seconds: float
    ) -> tuple[str, str, tuple[RuntimeEvent, ...]]:
        ...

    def close(self) -> None:
        ...


@dataclass(frozen=True, slots=True)
class CodexAppServerConfig:
    """配置 Codex app-server 命令、隔离状态根目录和每个 Turn 超时。"""

    command: tuple[str, ...]
    state_root: Path
    timeout_seconds: float = 600.0
    startup_timeout_seconds: float = 15.0

    def __post_init__(self) -> None:
        if not self.command or any(not item.strip() or "\x00" in item for item in self.command):
            raise ValueError("Codex app-server command must be a non-empty argument array")
        if self.timeout_seconds <= 0 or self.startup_timeout_seconds <= 0:
            raise ValueError("Codex app-server timeouts must be positive")

    @classmethod
    def from_environment(cls, state_root: Path) -> CodexAppServerConfig:
        raw = os.environ.get("AGENT_PLATFORM_CODEX_APP_SERVER_COMMAND", "codex app-server --stdio")
        command = tuple(shlex.split(raw))
        return cls(command=command, state_root=state_root)


@dataclass(slots=True)
class _CodexSession:
    thread_id: str
    connection: CodexAppServerConnection
    cwd: Path
    codex_home: Path
    lock: threading.Lock = field(default_factory=threading.Lock)


class _StdioCodexAppServer:
    """每个租户 Session 独占一个无 shell 子进程和 JSON-RPC 流。"""

    def __init__(
        self,
        command: tuple[str, ...],
        cwd: Path,
        environment: Mapping[str, str],
        timeout_seconds: float,
        startup_timeout_seconds: float,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._stdout_buffer = bytearray()
        self._process = subprocess.Popen(
            list(command),
            cwd=str(cwd),
            env=dict(environment),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            shell=False,
            text=False,
            bufsize=0,
        )
        self._request_id = 0
        self._notifications: deque[dict[str, object]] = deque()
        self._write_lock = threading.Lock()
        self._stdout = self._process.stdout
        self._stdin = self._process.stdin
        if self._stdout is None or self._stdin is None:
            self.close()
            raise RuntimeError("Codex app-server stdio pipes were not created")

    def _send(self, value: Mapping[str, object]) -> None:
        line = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        with self._write_lock:
            if self._process.poll() is not None or self._stdin is None:
                raise RuntimeError("Codex app-server exited before the request was sent")
            self._stdin.write((line + "\n").encode("utf-8"))
            self._stdin.flush()

    def _receive(self, timeout_seconds: float) -> dict[str, object]:
        if self._process.poll() is not None or self._stdout is None:
            raise RuntimeError("Codex app-server exited while a request was pending")
        deadline = time.monotonic() + max(0.0, timeout_seconds)
        while b"\n" not in self._stdout_buffer:
            readable, _, _ = select.select(
                [self._stdout], [], [], max(0.0, deadline - time.monotonic())
            )
            if not readable:
                raise TimeoutError("Codex app-server response timed out")
            chunk = os.read(self._stdout.fileno(), 4096)
            if not chunk:
                raise RuntimeError("Codex app-server closed its stdout")
            self._stdout_buffer.extend(chunk)
        line, _, remaining = self._stdout_buffer.partition(b"\n")
        self._stdout_buffer = bytearray(remaining)
        try:
            value = json.loads(line.decode("utf-8"))
        except json.JSONDecodeError as error:
            raise RuntimeError("Codex app-server emitted invalid JSON-RPC") from error
        if not isinstance(value, dict):
            raise TypeError("Codex app-server emitted a non-object JSON-RPC message")
        return value

    def request(
        self,
        method: str,
        params: Mapping[str, object],
        *,
        timeout_seconds: float | None = None,
    ) -> Mapping[str, object]:
        self._request_id += 1
        request_id = self._request_id
        self._send({"id": request_id, "method": method, "params": dict(params)})
        deadline = time.monotonic() + (
            timeout_seconds or self._timeout_seconds
        )
        while time.monotonic() < deadline:
            message = self._receive(deadline - time.monotonic())
            if message.get("id") == request_id:
                error = message.get("error")
                if error is not None:
                    raise RuntimeError(f"Codex app-server {method} request failed")
                result = message.get("result", {})
                if not isinstance(result, dict):
                    raise RuntimeError(f"Codex app-server {method} returned invalid JSON")
                return result
            if isinstance(message.get("method"), str):
                # Dynamic tools and approval requests are not enabled by this adapter.
                # Rejecting them prevents an unattended process from waiting for a UI.
                if "id" in message:
                    self._send(
                        {
                            "id": message["id"],
                            "error": {
                                "code": -32601,
                                "message": "Agent Platform adapter does not support this request",
                            },
                        }
                    )
                else:
                    self._notifications.append(message)
        raise TimeoutError(f"Codex app-server {method} request timed out")

    def notify(self, method: str, params: Mapping[str, object]) -> None:
        self._send({"method": method, "params": dict(params)})

    @staticmethod
    def _turn_payload(
        message: Mapping[str, object], thread_id: str, turn_id: str
    ) -> Mapping[str, object] | None:
        params = message.get("params")
        if not isinstance(params, dict) or params.get("threadId") != thread_id:
            return None
        turn = params.get("turn")
        if not isinstance(turn, dict) or turn.get("id") != turn_id:
            return None
        return turn

    def wait_for_turn(
        self, thread_id: str, turn_id: str, timeout_seconds: float
    ) -> tuple[str, str, tuple[RuntimeEvent, ...]]:
        deadline = time.monotonic() + timeout_seconds
        text_parts: list[str] = []
        events: list[RuntimeEvent] = []
        while time.monotonic() < deadline:
            message = (
                self._notifications.popleft()
                if self._notifications
                else self._receive(deadline - time.monotonic())
            )
            method = message.get("method")
            params = message.get("params")
            if not isinstance(method, str) or not isinstance(params, dict):
                continue
            if params.get("threadId") != thread_id:
                continue
            if method == "item/agentMessage/delta":
                delta = params.get("delta")
                if isinstance(delta, str):
                    text_parts.append(delta)
                    events.append(RuntimeEvent("agent_message_delta", "RUNTIME", {"text": delta}))
                continue
            if method == "item/completed":
                item = params.get("item")
                if isinstance(item, dict) and item.get("type") == "agentMessage":
                    text = item.get("text")
                    if isinstance(text, str) and text:
                        text_parts = [text]
                continue
            if method != "turn/completed":
                continue
            turn = self._turn_payload(message, thread_id, turn_id)
            if turn is None:
                continue
            status = str(turn.get("status", "failed"))
            error = turn.get("error")
            if status == "completed":
                return status, "".join(text_parts), tuple(events)
            message_text = error.get("message") if isinstance(error, dict) else None
            events.append(
                RuntimeEvent(
                    "runtime_error",
                    "RUNTIME",
                    {
                        "error_category": "CODEX_TURN_FAILED",
                        "message": str(message_text or status),
                    },
                )
            )
            return status, "".join(text_parts), tuple(events)
        raise TimeoutError("Codex app-server Turn timed out")

    def close(self) -> None:
        process = getattr(self, "_process", None)
        if process is None or process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
                process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                pass


class CodexAppServerRuntimeAdapter:
    """把 Codex app-server 的线程生命周期映射到平台 Run/Session。"""

    engine_name = "codex_cli"
    capabilities = RuntimeCapabilities(
        resume=True,
        cancellation=False,
        tool_calls=False,
        approvals=False,
        mcp=False,
        workspace=False,
        sandbox=False,
        reasoning=True,
        reasoning_effort=True,
        # Codex app-server 当前没有经过契约验证的预算字段，保存时必须拒绝预算值。
        reasoning_budget=False,
        max_output_tokens=True,
    )

    def __init__(
        self,
        *,
        command: Sequence[str],
        state_root: Path,
        secret_resolver: Callable[[SecretRef], str],
        timeout_seconds: float = 600.0,
        startup_timeout_seconds: float = 15.0,
        connection_factory: Callable[
            [tuple[str, ...], Path, Mapping[str, str], float, float],
            CodexAppServerConnection,
        ]
        | None = None,
    ) -> None:
        command_tuple = tuple(command)
        if not command_tuple or any(not item.strip() for item in command_tuple):
            raise ValueError("Codex app-server command must not be empty")
        if timeout_seconds <= 0 or startup_timeout_seconds <= 0:
            raise ValueError("Codex app-server timeouts must be positive")
        self.command = command_tuple
        self.state_root = Path(state_root).expanduser().resolve()
        self.secret_resolver = secret_resolver
        self.timeout_seconds = timeout_seconds
        self.startup_timeout_seconds = startup_timeout_seconds
        self._connection_factory = connection_factory or (
            lambda cmd, cwd, env, timeout, startup_timeout: _StdioCodexAppServer(
                cmd, cwd, env, timeout, startup_timeout
            )
        )
        self._sessions: dict[str, _CodexSession] = {}
        self._lock = threading.RLock()

    def create_session(self, request: RuntimeRequest | None = None) -> str:
        """Worker 传入 RunSnapshot 时先创建隔离 Codex thread 并固定 opaque ID。"""

        if request is None:
            return f"codex-pending:{uuid4().hex}"
        if request.tool_definitions or request.allowed_tools or request.mcp_servers:
            raise DomainError(
                "ENGINE_CAPABILITY_UNSUPPORTED",
                "Codex app-server adapter 尚未启用平台 Tool/MCP 调度",
                {"engine_name": self.engine_name, "capability": "tools"},
            )
        session = self._start_or_resume(request, None)
        return f"codex-thread:{session.thread_id}"

    def resume_session(self, session_ref: str) -> str:
        """校验平台 opaque 引用；真实 app-server Resume 在下次 Run 时完成。"""

        if not session_ref.startswith(("codex-thread:", "codex-pending:")):
            raise ValueError("Codex session reference must be an adapter-owned token")
        return session_ref

    @staticmethod
    def _session_key(request: RuntimeRequest) -> str:
        reference = request.harness_home_ref or ""
        if not reference.startswith("harness-session:"):
            raise DomainError(
                "ENGINE_CAPABILITY_UNSUPPORTED",
                "Codex app-server 需要平台分配的 Session Home",
            )
        return hashlib.sha256(reference.encode("utf-8")).hexdigest()

    def _build_session_paths(self, request: RuntimeRequest) -> tuple[Path, Path]:
        session_key = self._session_key(request)
        self.state_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.state_root.chmod(0o700)
        codex_home = self.state_root / "homes" / session_key
        workspace = self.state_root / "workspaces" / session_key
        codex_home.mkdir(parents=True, exist_ok=True, mode=0o700)
        workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
        codex_home.chmod(0o700)
        workspace.chmod(0o700)
        return codex_home, workspace

    def _route_environment(
        self,
        request: RuntimeRequest,
        codex_home: Path,
    ) -> tuple[dict[str, str], ModelRouteSnapshot]:
        snapshot = request.model_route_snapshot
        if snapshot is None:
            raise DomainError(
                "ENGINE_MODEL_INCOMPATIBLE",
                "Codex CLI 需要显式 OpenAI Responses ModelRoute",
            )
        if snapshot.protocol != "openai-responses":
            raise DomainError(
                "ENGINE_MODEL_INCOMPATIBLE",
                "Codex CLI 适配器只支持 OpenAI Responses ModelRoute",
                {
                    "engine_name": self.engine_name,
                    "model_route_id": snapshot.route_id,
                    "protocol": snapshot.protocol,
                },
            )
        if snapshot.secret_ref is None:
            raise DomainError(
                "ENGINE_CONFIGURATION_REQUIRED",
                "Codex ModelRoute 必须绑定接收方 SecretRef",
                {"model_route_id": snapshot.route_id},
            )
        try:
            api_key = self.secret_resolver(snapshot.secret_ref)
        except Exception as error:  # SecretProvider 失败发生在 app-server 出站之前
            raise DomainError(
                "ENGINE_CONFIGURATION_REQUIRED",
                "Codex ModelRoute 的接收方凭据无法解析",
                {"model_route_id": snapshot.route_id},
            ) from error
        if not api_key:
            raise DomainError(
                "ENGINE_CONFIGURATION_REQUIRED",
                "Codex ModelRoute 的接收方凭据为空",
                {"model_route_id": snapshot.route_id},
            )
        # CODEX_HOME 按 Session 隔离；配置文件不写凭据，API Key 只注入该子进程环境。
        config_text = "\n".join(
            (
                f"model = {json.dumps(snapshot.model_id, ensure_ascii=False)}",
                'model_provider = "openai"',
                f"openai_base_url = {json.dumps(snapshot.endpoint, ensure_ascii=False)}",
                "",
                "[analytics]",
                "enabled = false",
                "",
                "[shell_environment_policy]",
                'inherit = "core"',
                "ignore_default_excludes = false",
                'exclude = ["OPENAI_API_KEY", "CODEX_HOME", "OPENAI_BASE_URL"]',
                "",
            )
        )
        config_path = codex_home / "config.toml"
        config_path.write_text(config_text, encoding="utf-8")
        config_path.chmod(0o600)
        clean_environment = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(codex_home),
            "CODEX_HOME": str(codex_home),
            "TMPDIR": str(codex_home),
            "LANG": "C.UTF-8",
            "OPENAI_API_KEY": api_key,
        }
        return clean_environment, snapshot

    @staticmethod
    def _instructions(request: RuntimeRequest) -> str:
        context = request.employee_context
        if context is None:
            raise DomainError(
                "INVALID_ARGUMENT",
                "Codex app-server 需要固定 EmployeeContext",
            )
        parts = [context.system_prompt, *context.skill_instructions]
        parts.extend(
            f"Skill 文件 {item.path}：\n{item.content}"
            for item in context.skill_files
        )
        return "\n\n".join(part for part in parts if part.strip())

    def _create_connection(
        self, request: RuntimeRequest
    ) -> tuple[CodexAppServerConnection, Path, Path, ModelRouteSnapshot]:
        codex_home, workspace = self._build_session_paths(request)
        environment, snapshot = self._route_environment(request, codex_home)
        connection = self._connection_factory(
            self.command,
            workspace,
            environment,
            self.timeout_seconds,
            self.startup_timeout_seconds,
        )
        try:
            connection.request(
                "initialize",
                {
                    "clientInfo": {
                        "name": "agent-platform",
                        "title": "Agent Platform",
                        "version": "0.1.0",
                    },
                    "capabilities": {"experimentalApi": True},
                },
                timeout_seconds=self.startup_timeout_seconds,
            )
            connection.notify("initialized", {})
        except Exception:
            connection.close()
            raise
        return connection, codex_home, workspace, snapshot

    def _start_or_resume(
        self,
        request: RuntimeRequest,
        session_ref: str | None,
    ) -> _CodexSession:
        context = request.employee_context
        if context is None:
            raise DomainError("INVALID_ARGUMENT", "Codex app-server 需要固定 EmployeeContext")
        connection, codex_home, workspace, snapshot = self._create_connection(request)
        instructions = self._instructions(request)
        if session_ref is not None and session_ref.startswith("codex-thread:"):
            thread_id = session_ref.removeprefix("codex-thread:")
            params = {
                "threadId": thread_id,
                "model": snapshot.model_id,
                "modelProvider": "openai",
                "cwd": str(workspace),
                "approvalPolicy": "never",
                "sandbox": "workspace-write",
            }
            method = "thread/resume"
        else:
            thread_params: dict[str, object] = {
                "model": snapshot.model_id,
                "modelProvider": "openai",
                "cwd": str(workspace),
                "approvalPolicy": "never",
                "sandbox": "workspace-write",
                "developerInstructions": instructions,
                "ephemeral": False,
                "dynamicTools": [],
            }
            if context.model_route_id is not None:
                thread_params["modelProvider"] = "openai"
            params = thread_params
            method = "thread/start"
        try:
            result = connection.request(method, params)
            thread = result.get("thread")
            if isinstance(thread, dict):
                thread_id = str(thread.get("id", thread_id if method == "thread/resume" else ""))
            else:
                thread_id = thread_id if method == "thread/resume" else ""
            if not thread_id:
                raise RuntimeError(f"Codex app-server {method} response omitted thread ID")
        except Exception:
            connection.close()
            raise
        session = _CodexSession(thread_id, connection, workspace, codex_home)
        with self._lock:
            self._sessions[thread_id] = session
        return session

    def _get_session(self, request: RuntimeRequest) -> _CodexSession:
        session_ref = request.session_ref
        if session_ref is not None and session_ref.startswith("codex-thread:"):
            thread_id = session_ref.removeprefix("codex-thread:")
            with self._lock:
                current = self._sessions.get(thread_id)
            if current is not None:
                return current
            return self._start_or_resume(request, session_ref)
        return self._start_or_resume(request, session_ref)

    def run(self, request: RuntimeRequest) -> RuntimeResult:
        """在 Session 私有 CODEX_HOME 中启动 Turn，并只返回标准化文本和事件。"""

        if request.tool_definitions or request.allowed_tools or request.mcp_servers:
            raise DomainError(
                "ENGINE_CAPABILITY_UNSUPPORTED",
                "Codex app-server adapter 尚未启用平台 Tool/MCP 调度",
                {"engine_name": self.engine_name, "capability": "tools"},
            )
        session = self._get_session(request)
        with session.lock:
            turn_params: dict[str, object] = {
                "threadId": session.thread_id,
                "input": [{"type": "text", "text": request.prompt}],
            }
            if request.runtime_options is not None:
                options = request.runtime_options.resolve()
                if options.reasoning_effort is not None and options.reasoning_effort.value != "AUTO":
                    turn_params["reasoningEffort"] = options.reasoning_effort.value.lower()
                if options.max_output_tokens is not None:
                    turn_params["maxOutputTokens"] = options.max_output_tokens
                if options.response_verbosity is not None:
                    turn_params["verbosity"] = options.response_verbosity.value.lower()
            started = session.connection.request(
                "turn/start",
                turn_params,
            )
            turn = started.get("turn")
            turn_id = str(turn.get("id", "")) if isinstance(turn, dict) else ""
            if not turn_id:
                raise RuntimeError("Codex app-server turn/start response omitted turn ID")
            status_name, result_text, events = session.connection.wait_for_turn(
                session.thread_id, turn_id, self.timeout_seconds
            )
        status = {
            "completed": RuntimeStatus.SUCCEEDED,
            "interrupted": RuntimeStatus.CANCELLED,
            "failed": RuntimeStatus.FAILED,
        }.get(status_name, RuntimeStatus.FAILED)
        runtime_events = (
            RuntimeEvent("run_started", "RUNTIME", {"run_id": request.run_id}),
            *events,
            RuntimeEvent("run_finished", "RESULT", {"status": status.value}),
        )
        input_tokens = max(1, len(request.prompt.split()))
        output_tokens = max(1, len(result_text.split())) if result_text else 0
        return RuntimeResult(
            text=result_text,
            status=status,
            runtime_session_ref=f"codex-thread:{session.thread_id}",
            runtime_result_ref=f"codex-turn:{turn_id}",
            usage=UsageRecord(
                input_tokens,
                output_tokens,
                input_tokens + output_tokens,
                source="ESTIMATED",
                estimated=True,
                pricing_version="codex-estimated-v1",
            ),
            events=tuple(runtime_events),
            capabilities=self.capabilities,
            runtime_thread_ref=session.thread_id,
            runtime_workspace_ref=str(session.cwd),
        )

    def cancel(self, session_ref: str) -> bool:
        """当前 adapter 未实现可靠的并发 interrupt，明确返回不支持。"""

        del session_ref
        return False

    def close(self) -> None:
        """关闭当前进程持有的所有 Codex app-server stdio 连接。"""

        with self._lock:
            sessions = tuple(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            session.connection.close()
