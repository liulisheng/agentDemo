"""Sandbox-neutral contracts and conservative container defaults."""

from dataclasses import dataclass
from typing import Protocol


class SandboxUnavailable(RuntimeError):
    """Raised when no approved execution backend can accept a run."""


@dataclass(frozen=True, slots=True)
class ContainerSecurityPolicy:
    run_as_non_root: bool = True
    allow_privilege_escalation: bool = False
    read_only_rootfs: bool = True
    host_network: bool = False
    host_path: bool = False
    host_pid: bool = False
    privileged: bool = False
    capabilities_drop_all: bool = True

    @classmethod
    def default(cls) -> "ContainerSecurityPolicy":
        return cls()


@dataclass(frozen=True, slots=True)
class SandboxSpec:
    image: str
    cpu_millis: int
    memory_mb: int
    security: ContainerSecurityPolicy = ContainerSecurityPolicy.default()

    def __post_init__(self) -> None:
        if "@sha256:" not in self.image:
            raise ValueError("image must use a digest")
        if self.cpu_millis <= 0:
            raise ValueError("cpu_millis must be positive")
        if self.memory_mb <= 0:
            raise ValueError("memory_mb must be positive")
        if self.security.privileged or self.security.host_network or self.security.host_path:
            raise ValueError("sandbox security policy permits unsafe host access")


@dataclass(frozen=True, slots=True)
class SandboxHandle:
    backend: str
    external_id: str


class SandboxProvider(Protocol):
    name: str

    def create(self, spec: SandboxSpec) -> SandboxHandle: ...

    def stop(self, handle: SandboxHandle) -> None: ...

    def destroy(self, handle: SandboxHandle) -> None: ...
