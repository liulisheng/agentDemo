"""Runtime 工厂注册表，把供应商适配器与控制面生命周期解耦。"""

from __future__ import annotations

from collections.abc import Callable

from agent_platform.ports.runtime import RuntimeAdapter, RuntimeRequest, RuntimeResult

from .fake import FakeRuntimeAdapter


class UnsupportedRuntimeError(RuntimeError):
    """目标 Runtime 未安装或不支持请求能力。"""


class _UnavailableRuntime:
    def __init__(self, name: str) -> None:
        self.name = name

    def create_session(self) -> str:
        raise UnsupportedRuntimeError(f"runtime {self.name!r} is unavailable")

    def resume_session(self, session_ref: str) -> str:
        raise UnsupportedRuntimeError(f"runtime {self.name!r} is unavailable")

    def run(self, request: RuntimeRequest) -> RuntimeResult:
        raise UnsupportedRuntimeError(f"runtime {self.name!r} is unavailable")

    def cancel(self, session_ref: str) -> bool:
        raise UnsupportedRuntimeError(f"runtime {self.name!r} is unavailable")


class RuntimeRegistry:
    """按配置名称选择 Runtime，未安装能力显式报错而不静默降级。"""

    def __init__(self) -> None:
        self._factories: dict[str, Callable[[], RuntimeAdapter]] = {}

    @classmethod
    def default(cls) -> RuntimeRegistry:
        registry = cls()
        registry.register("fake", FakeRuntimeAdapter)
        registry.register("deepseek", lambda: _UnavailableRuntime("deepseek"))
        registry.register("codex", lambda: _UnavailableRuntime("codex"))
        registry.register("generic-cli", lambda: _UnavailableRuntime("generic-cli"))
        return registry

    def register(self, name: str, factory: Callable[[], RuntimeAdapter]) -> None:
        normalized = name.strip().lower()
        if not normalized:
            raise ValueError("runtime name must not be blank")
        if normalized in self._factories:
            raise ValueError(f"runtime {normalized!r} already registered")
        self._factories[normalized] = factory

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._factories))

    def get(self, name: str) -> RuntimeAdapter:
        try:
            return self._factories[name.strip().lower()]()
        except KeyError as error:
            raise UnsupportedRuntimeError(f"runtime {name!r} is not registered") from error
