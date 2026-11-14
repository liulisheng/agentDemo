"""Explicit engine registry used by the CLI and application layer."""

from collections.abc import Callable

from .core import HarnessEngine


class EngineRegistry:
    def __init__(self) -> None:
        self._factories: dict[str, Callable[[], HarnessEngine]] = {}

    def register(self, name: str, factory: Callable[[], HarnessEngine]) -> None:
        normalized = name.strip().lower()
        if not normalized:
            raise ValueError("engine name must not be blank")
        if normalized in self._factories:
            raise ValueError(f"engine {normalized!r} already registered")
        self._factories[normalized] = factory

    def create(self, name: str) -> HarnessEngine:
        try:
            factory = self._factories[name.strip().lower()]
        except KeyError as exc:
            raise KeyError(f"unknown engine: {name}") from exc
        return factory()

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._factories))
