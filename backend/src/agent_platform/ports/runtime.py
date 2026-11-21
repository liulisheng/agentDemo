"""平台与供应商 Runtime 之间的最小生命周期端口。"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Protocol

from agent_platform.domain.execution import RuntimeStatus, UsageRecord
from agent_platform.domain.mcp import McpServerProfile
from agent_platform.domain.model_routes import ModelRouteSnapshot
from agent_platform.domain.observability import SkillFilePackageSnapshot


@dataclass(frozen=True, slots=True)
class RuntimeCapabilities:
    """调用方可据此判断 Runtime 是否支持恢复、取消、审批和工作区。"""

    streaming: bool = False
    resume: bool = True
    cancellation: bool = False
    approvals: bool = False
    mcp: bool = False
    workspace: bool = False


@dataclass(frozen=True, slots=True)
class RuntimeSkillFile:
    """按固定 Skill 版本读取的文本文件；对象正文不进入 MySQL/Trace。"""

    skill_id: str
    skill_version: int
    path: str
    content: str = field(repr=False)
    content_sha256: str

    def __post_init__(self) -> None:
        if not self.skill_id.strip() or self.skill_version < 1:
            raise ValueError("Skill file requires a fixed skill version")
        if not self.path.strip() or "\x00" in self.path:
            raise ValueError("Skill file path must be nonblank text")
        if not isinstance(self.content, str):
            raise TypeError("Skill file content must be text")
        digest = self.content_sha256.lower()
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError("Skill file digest must be a SHA-256 hex value")
        if hashlib.sha256(self.content.encode("utf-8")).hexdigest() != digest:
            raise ValueError("Skill file content digest does not match")
        object.__setattr__(self, "content_sha256", digest)


@dataclass(frozen=True, slots=True)
class RuntimeEmployeeContext:
    """RunSnapshot 固定的员工指令、Skill 顺序和模型路由，不含凭据。"""

    employee_id: str
    employee_version: int
    system_prompt: str = field(repr=False)
    skill_refs: tuple[tuple[str, int], ...] = ()
    skill_instructions: tuple[str, ...] = field(default=(), repr=False)
    skill_files: tuple[RuntimeSkillFile, ...] = ()
    model_route_id: str | None = None

    def __post_init__(self) -> None:
        if not self.employee_id.strip() or self.employee_version < 1:
            raise ValueError("employee context requires a fixed employee version")
        if not self.system_prompt.strip():
            raise ValueError("employee system_prompt must not be blank")
        refs = tuple((str(resource_id), int(version)) for resource_id, version in self.skill_refs)
        if any(not resource_id.strip() or version < 1 for resource_id, version in refs):
            raise ValueError("skill refs must contain fixed skill versions")
        instructions = tuple(self.skill_instructions)
        if any(not isinstance(item, str) or not item.strip() for item in instructions):
            raise ValueError("skill instructions must be nonblank text")
        files = tuple(self.skill_files)
        if any(not isinstance(item, RuntimeSkillFile) for item in files):
            raise TypeError("skill_files must contain RuntimeSkillFile values")
        if self.model_route_id is not None and not self.model_route_id.strip():
            raise ValueError("model_route_id must be nonblank when provided")
        object.__setattr__(self, "skill_refs", refs)
        object.__setattr__(self, "skill_instructions", instructions)
        object.__setattr__(self, "skill_files", files)


@dataclass(frozen=True, slots=True)
class RuntimeToolDefinition:
    """运行时允许模型看到的精确 ToolVersion 和 JSON Schema。"""

    tool_id: str
    tool_version: int
    name: str
    description: str
    input_schema: Mapping[str, object]
    risk_level: str
    remote_tool_name: str | None = None

    def __post_init__(self) -> None:
        if not self.tool_id.strip() or self.tool_version < 1:
            raise ValueError("tool definition requires a fixed ToolVersion")
        if not self.name.strip() or any(character.isspace() for character in self.name):
            raise ValueError("tool name must be a nonblank token")
        if not self.description.strip():
            raise ValueError("tool description must not be blank")
        if self.remote_tool_name is not None and not self.remote_tool_name.strip():
            raise ValueError("remote_tool_name must be nonblank when provided")
        if not isinstance(self.input_schema, Mapping):
            raise TypeError("tool input schema must be an object")
        risk_level = self.risk_level.upper().strip()
        if risk_level not in {"READ", "PREVIEW"}:
            raise ValueError("risk_level must be READ or PREVIEW")
        object.__setattr__(self, "risk_level", risk_level)
        object.__setattr__(self, "input_schema", MappingProxyType(dict(self.input_schema)))


@dataclass(frozen=True, slots=True)
class RuntimeMcpServer:
    """Worker 可启动的精确 Stdio Profile 与本次 Run 的只读/预览工具子集。"""

    profile: McpServerProfile = field(repr=False)
    run_allowed_tool_names: tuple[str, ...]
    expected_tool_schema_sha256: Mapping[str, str]

    def __post_init__(self) -> None:
        allowed = tuple(self.run_allowed_tool_names)
        if not allowed or len(set(allowed)) != len(allowed):
            raise ValueError("MCP Run tool allowlist must be nonempty and unique")
        if not set(allowed) <= set(self.profile.enabled_tool_names):
            raise ValueError("MCP Run tool allowlist must be a subset of the Profile")
        digests = dict(self.expected_tool_schema_sha256)
        if set(digests) != set(allowed):
            raise ValueError("MCP Run tool schemas must match the allowlist")
        for digest in digests.values():
            normalized = str(digest).lower()
            if len(normalized) != 64 or any(char not in "0123456789abcdef" for char in normalized):
                raise ValueError("MCP Run tool schema digest must be SHA-256")
        object.__setattr__(self, "run_allowed_tool_names", allowed)
        object.__setattr__(self, "expected_tool_schema_sha256", MappingProxyType(digests))


@dataclass(frozen=True, slots=True)
class RuntimeRequest:
    """平台传给 Runtime 的业务输入，不包含供应商私有上下文格式。"""

    run_id: str
    session_ref: str | None
    prompt: str
    skill_instructions: Sequence[str]
    allowed_tools: Sequence[str]
    workspace_ref: str | None = None
    thread_ref: str | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)
    system_prompt: str = ""
    skill_file_packages: Sequence[SkillFilePackageSnapshot] = ()
    employee_context: RuntimeEmployeeContext | None = None
    tool_definitions: Sequence[RuntimeToolDefinition] = ()
    harness_home_ref: str | None = None
    mcp_servers: Sequence[RuntimeMcpServer] = ()
    model_route_snapshot: ModelRouteSnapshot | None = None
    max_tokens: int | None = None

    def __post_init__(self) -> None:
        for field_name in ("run_id", "prompt"):
            if not getattr(self, field_name).strip():
                raise ValueError(f"{field_name} must not be blank")
        definitions = tuple(self.tool_definitions)
        mcp_servers = tuple(self.mcp_servers)
        if any(not isinstance(item, RuntimeToolDefinition) for item in definitions):
            raise TypeError("tool_definitions must contain RuntimeToolDefinition values")
        if len({item.name for item in definitions}) != len(definitions):
            raise ValueError("tool definition names must be unique")
        if definitions and tuple(self.allowed_tools) != tuple(item.name for item in definitions):
            raise ValueError("allowed_tools must match the typed tool definitions in order")
        if self.employee_context is not None and not isinstance(
            self.employee_context, RuntimeEmployeeContext
        ):
            raise TypeError("employee_context must be a RuntimeEmployeeContext value")
        if self.model_route_snapshot is not None and not isinstance(
            self.model_route_snapshot, ModelRouteSnapshot
        ):
            raise TypeError("model_route_snapshot must be a ModelRouteSnapshot value")
        if self.max_tokens is not None and self.max_tokens < 1:
            raise ValueError("max_tokens must be positive when provided")
        if (
            self.employee_context is not None
            and self.employee_context.model_route_id is not None
            and self.model_route_snapshot is not None
            and self.employee_context.model_route_id != self.model_route_snapshot.route_id
        ):
            raise ValueError("model route snapshot must match the employee context")
        if self.harness_home_ref is not None and re.fullmatch(
            r"harness-session:[0-9a-f]{64}", self.harness_home_ref
        ) is None:
            raise ValueError("harness_home_ref must be an opaque harness-session reference")
        if any(not isinstance(server, RuntimeMcpServer) for server in mcp_servers):
            raise TypeError("mcp_servers must contain RuntimeMcpServer values")
        if len({(server.profile.profile_id, server.profile.version) for server in mcp_servers}) != len(
            mcp_servers
        ):
            raise ValueError("MCP Profile references must be unique within a Run")
        object.__setattr__(self, "tool_definitions", definitions)
        object.__setattr__(self, "mcp_servers", mcp_servers)


@dataclass(frozen=True, slots=True)
class RuntimeEvent:
    """被适配器规范化的 Runtime 事件。"""

    event_type: str
    span_type: str
    payload: Mapping[str, object] = field(default_factory=dict, repr=False)


@dataclass(frozen=True, slots=True)
class RuntimeResult:
    """与引擎无关的执行结果和 opaque 引用。"""

    text: str
    status: RuntimeStatus
    runtime_session_ref: str
    runtime_result_ref: str
    usage: UsageRecord
    events: tuple[RuntimeEvent, ...]
    capabilities: RuntimeCapabilities = field(default_factory=RuntimeCapabilities)
    runtime_thread_ref: str | None = None
    runtime_workspace_ref: str | None = None


class RuntimeAdapter(Protocol):
    """所有 Runtime 适配器必须实现的最小生命周期协议。"""

    capabilities: RuntimeCapabilities

    def create_session(self) -> str:
        ...

    def resume_session(self, session_ref: str) -> str:
        ...

    def run(self, request: RuntimeRequest) -> RuntimeResult:
        ...

    def cancel(self, session_ref: str) -> bool:
        ...
