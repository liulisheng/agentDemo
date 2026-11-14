"""Workspace、Sandbox 与 Runtime 环境的隔离端口和示例能力实现。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from secrets import token_hex
from typing import Protocol


class WorkspaceMode(StrEnum):
    NO_FILES = "no-files"
    EPHEMERAL = "ephemeral"
    PERSISTENT = "persistent"


def _text(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must not be blank")
    return value.strip()


@dataclass(frozen=True, slots=True)
class WorkspaceProfile:
    """声明 Agent 所需的文件、仓库、配额和持久化边界。"""

    profile_id: str
    mode: WorkspaceMode | str = WorkspaceMode.NO_FILES
    project_id: str | None = None
    files: bool = False
    repository: bool = False
    remote_data_tools: bool = False
    network_policy: str = "none"
    quota: Mapping[str, int] = field(default_factory=dict)
    persistent: bool = False
    allowed_host_paths: tuple[str, ...] = ()
    allowed_devices: tuple[str, ...] = ()
    privileged: bool = False
    long_lived_credentials: bool = False

    def __post_init__(self) -> None:
        _text(self.profile_id, "profile_id")
        mode = WorkspaceMode(self.mode)
        if mode is WorkspaceMode.PERSISTENT and not self.persistent:
            object.__setattr__(self, "persistent", True)
        if self.allowed_host_paths:
            raise ValueError("host paths must be mediated by the platform")
        if self.allowed_devices:
            raise ValueError("device access is not allowed")
        if self.privileged:
            raise ValueError("privileged workspace is not allowed")
        if self.long_lived_credentials:
            raise ValueError("long-lived credentials are not allowed")
        if any(value < 0 for value in self.quota.values()):
            raise ValueError("quota values must not be negative")


@dataclass(frozen=True, slots=True)
class WorkspaceRef:
    """平台创建的 opaque 工作区引用；调用方不得推断其主机路径。"""

    opaque_ref: str
    tenant_id: str
    project_id: str | None
    run_id: str
    mode: WorkspaceMode
    lease_id: str

    def __post_init__(self) -> None:
        for name in ("opaque_ref", "tenant_id", "run_id", "lease_id"):
            _text(getattr(self, name), name)


@dataclass(frozen=True, slots=True)
class SandboxProfile:
    """声明沙箱资源、网络和凭据边界。"""

    profile_id: str
    network_policy: str = "none"
    cpu_limit: int = 1
    memory_mb: int = 512
    allowed_host_paths: tuple[str, ...] = ()
    devices: tuple[str, ...] = ()
    privileged: bool = False
    long_lived_credentials: bool = False

    def __post_init__(self) -> None:
        _text(self.profile_id, "profile_id")
        if self.allowed_host_paths:
            raise ValueError("host paths must be mediated by the platform")
        if self.devices:
            raise ValueError("device access is not allowed")
        if self.privileged:
            raise ValueError("privileged sandbox is not allowed")
        if self.long_lived_credentials:
            raise ValueError("long-lived credentials are not allowed")
        if self.cpu_limit <= 0 or self.memory_mb <= 0:
            raise ValueError("resource limits must be positive")


@dataclass(frozen=True, slots=True)
class SandboxSpec:
    """一次运行对沙箱的最小请求，不包含宿主机实现细节。"""

    run_id: str
    workspace_ref: WorkspaceRef | None = None
    network_policy: str = "none"
    privileged: bool = False
    devices: tuple[str, ...] = ()
    host_paths: tuple[str, ...] = ()
    secret_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _text(self.run_id, "run_id")
        if self.privileged or self.devices or self.host_paths:
            raise ValueError("sandbox request exceeds platform policy")
        for secret_ref in self.secret_refs:
            _text(secret_ref, "secret_ref")


@dataclass(frozen=True, slots=True)
class SandboxRef:
    """一次沙箱租约的 opaque 引用。"""

    opaque_ref: str
    tenant_id: str
    project_id: str | None
    run_id: str
    lease_id: str


@dataclass(frozen=True, slots=True)
class RuntimeEnvironmentRef:
    """平台交给 RuntimeAdapter 的环境引用，不暴露工作区内部状态。"""

    workspace_ref: str | None
    sandbox_ref: str
    engine_name: str
    run_id: str

    def __post_init__(self) -> None:
        _text(self.sandbox_ref, "sandbox_ref")
        _text(self.engine_name, "engine_name")
        _text(self.run_id, "run_id")


class SecretProvider(Protocol):
    """按 SecretRef 短时解析凭据，禁止端口接收明文长期凭据。"""

    def resolve(self, secret_ref: str, *, ttl_seconds: int = 300) -> Mapping[str, str]:
        ...


class FakeSecretProvider:
    """测试用 SecretProvider，仅返回预置短时值，不把 SecretRef 暴露给日志。"""

    def __init__(self, values: Mapping[str, Mapping[str, str]] | None = None) -> None:
        self._values = {key: dict(value) for key, value in (values or {}).items()}

    def resolve(self, secret_ref: str, *, ttl_seconds: int = 300) -> Mapping[str, str]:
        _text(secret_ref, "secret_ref")
        if ttl_seconds <= 0 or ttl_seconds > 3600:
            raise ValueError("secret TTL must be between 1 and 3600 seconds")
        if secret_ref not in self._values:
            raise KeyError("secret reference not found")
        return dict(self._values[secret_ref])


class WorkspaceProvider(Protocol):
    """工作区生命周期与租户、项目授权端口。"""

    def create(self, profile: WorkspaceProfile, *, tenant_id: str, project_id: str | None, run_id: str) -> WorkspaceRef:
        ...

    def mount(self, ref: WorkspaceRef, *, tenant_id: str, project_id: str | None) -> str:
        ...

    def release(self, ref: WorkspaceRef) -> None:
        ...


class SandboxProvider(Protocol):
    """沙箱生命周期端口，具体容器或进程实现由基础设施提供。"""

    def create(self, profile: SandboxProfile, spec: SandboxSpec, *, tenant_id: str, project_id: str | None) -> SandboxRef:
        ...

    def release(self, ref: SandboxRef) -> None:
        ...


class FakeWorkspaceProvider:
    """测试用工作区 Provider，只模拟授权和租约，不执行 Runtime shell。"""

    def __init__(self) -> None:
        self._refs: dict[str, WorkspaceRef] = {}

    def create(self, profile: WorkspaceProfile, *, tenant_id: str, project_id: str | None, run_id: str) -> WorkspaceRef:
        _text(tenant_id, "tenant_id")
        if profile.project_id is not None and profile.project_id != project_id:
            raise PermissionError("workspace profile project does not match request")
        ref = WorkspaceRef(f"workspace:{token_hex(12)}", tenant_id, project_id, run_id, WorkspaceMode(profile.mode), f"lease:{token_hex(8)}")
        self._refs[ref.opaque_ref] = ref
        return ref

    def mount(self, ref: WorkspaceRef, *, tenant_id: str, project_id: str | None) -> str:
        current = self._refs.get(ref.opaque_ref)
        if current is None or current.tenant_id != tenant_id or current.project_id != project_id:
            raise PermissionError("workspace is outside the authorized scope")
        return ref.opaque_ref

    def release(self, ref: WorkspaceRef) -> None:
        self._refs.pop(ref.opaque_ref, None)


class FakeSandboxProvider:
    """测试用沙箱 Provider，验证租户边界和 opaque 引用。"""

    def __init__(self) -> None:
        self._refs: dict[str, SandboxRef] = {}

    def create(self, profile: SandboxProfile, spec: SandboxSpec, *, tenant_id: str, project_id: str | None) -> SandboxRef:
        if spec.workspace_ref is not None and (spec.workspace_ref.tenant_id != tenant_id or spec.workspace_ref.project_id != project_id):
            raise PermissionError("workspace is outside the sandbox authorization scope")
        ref = SandboxRef(f"sandbox:{token_hex(12)}", tenant_id, project_id, spec.run_id, f"lease:{token_hex(8)}")
        self._refs[ref.opaque_ref] = ref
        return ref

    def release(self, ref: SandboxRef) -> None:
        self._refs.pop(ref.opaque_ref, None)


@dataclass(frozen=True, slots=True)
class GitCheckoutRequest:
    """代码 Agent 的仓库绑定，默认只读且固定提交。"""

    repository_url: str
    commit: str
    branch: str | None = None
    read_only: bool = True

    def __post_init__(self) -> None:
        _text(self.repository_url, "repository_url")
        _text(self.commit, "commit")
        if self.branch is not None:
            _text(self.branch, "branch")

    def can_write_branch(self, branch: str) -> bool:
        return not self.read_only and branch not in {"main", "master"}


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    """代码运行产物或 diff 的平台引用。"""

    artifact_type: str
    ref: str
    tenant_id: str
    project_id: str | None = None


class LogQueryCapability:
    """只读日志查询示例能力，不提供宿主 shell。"""

    def query(self, resource: str, query: str) -> list[Mapping[str, str]]:
        _text(resource, "resource")
        _text(query, "query")
        return []


class OperationsCapability:
    """运维写操作必须先通过审批检查。"""

    def __init__(self, approval_checker: Callable[[str], bool]) -> None:
        self._approval_checker = approval_checker

    def execute(self, action: str, resource: str) -> str:
        _text(action, "action")
        _text(resource, "resource")
        if action in {"restart", "delete", "configure", "scale"} and not self._approval_checker(action):
            raise PermissionError(f"operation requires approval: {action}")
        return "approved"
