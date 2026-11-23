"""按 ExecutionProfile 解析已注册 Runtime，并校验路由协议与能力边界。"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from agent_platform.adapters.runtime.fake import FakeRuntimeAdapter
from agent_platform.domain.catalog import ExecutionProfile
from agent_platform.domain.common import DomainError
from agent_platform.domain.model_routes import ModelRouteSnapshot
from agent_platform.ports.runtime import RuntimeAdapter, RuntimeCapabilities


@dataclass(frozen=True, slots=True)
class EngineProviderCapabilities:
    """ExecutionProfile 可要求的引擎生命周期和执行能力。"""

    resume: bool = False
    cancellation: bool = False
    tool_calls: bool = False
    mcp: bool = False
    workspace: bool = False
    sandbox: bool = False
    approvals: bool = False
    model_route_required: bool = False
    route_secret_required: bool = False

    @classmethod
    def from_runtime(cls, value: RuntimeCapabilities) -> EngineProviderCapabilities:
        """把 RuntimeAdapter 的运行时报告固定为可供配置校验的能力摘要。"""

        return cls(
            resume=value.resume,
            cancellation=value.cancellation,
            tool_calls=value.tool_calls,
            mcp=value.mcp,
            workspace=value.workspace,
            sandbox=value.sandbox,
            approvals=value.approvals,
        )

    def to_public_dict(self) -> dict[str, bool]:
        """只返回无凭据的稳定能力标志。"""

        return {
            "resume": self.resume,
            "cancellation": self.cancellation,
            "tool_calls": self.tool_calls,
            "mcp": self.mcp,
            "workspace": self.workspace,
            "sandbox": self.sandbox,
            "approvals": self.approvals,
            "model_route_required": self.model_route_required,
            "route_secret_required": self.route_secret_required,
        }


@dataclass(frozen=True, slots=True)
class RegisteredEngineProvider:
    """平台允许用户选择的 Runtime 及其经过验证的协议边界。"""

    engine_name: str
    display_name: str
    adapter: RuntimeAdapter
    supported_model_protocols: frozenset[str]
    capabilities: EngineProviderCapabilities
    enabled: bool = True
    validation_note: str | None = None
    simulation: bool = False

    def to_public_dict(self) -> dict[str, object]:
        """供模型管理页显示可选 EngineProvider，不暴露进程命令或凭据。"""

        return {
            "engine_name": self.engine_name,
            "display_name": self.display_name,
            "supported_model_protocols": sorted(self.supported_model_protocols),
            "capabilities": self.capabilities.to_public_dict(),
            "execution_mode": "SIMULATION" if self.simulation else "NATIVE",
        }


def unsupported_profile_runtime_fields(profile: ExecutionProfile) -> tuple[str, ...]:
    """列出当前 Worker 尚未读取的 Profile 环境策略字段。"""

    unsupported: list[str] = []
    if profile.workspace_profile_id is not None:
        unsupported.append("workspace_profile")
    if profile.sandbox_profile_id is not None:
        unsupported.append("sandbox_profile")
    if profile.approval_policy != "none":
        unsupported.append("approval_policy")
    return tuple(unsupported)


def normalize_engine_provider_name(engine_name: str) -> str:
    """将历史 DeepSeek 名称归一到唯一可见的 Provider ID。"""

    normalized = engine_name.strip().casefold().replace("-", "_")
    if not normalized:
        raise ValueError("engine_name must not be blank")
    # Profile/RunSnapshot 可能仍保存旧 deepseek；选择器只公布 deepseek_harness。
    return "deepseek_harness" if normalized == "deepseek" else normalized


def validate_profile_worker_policy(profile: ExecutionProfile) -> None:
    """拒绝 Worker 尚未接线的 Profile 级 Workspace/Sandbox/审批字段。"""

    unsupported = unsupported_profile_runtime_fields(profile)
    if unsupported:
        raise DomainError(
            "ENGINE_CAPABILITY_UNSUPPORTED",
            "当前 Worker 尚未应用 Profile 的 Workspace/Sandbox/附加审批策略",
            {"engine_name": profile.engine_name, "unsupported_capabilities": list(unsupported)},
        )


class EngineProviderRegistry:
    """注册经过适配器边界验证的引擎；缺失时明确拒绝，不使用其他 Runtime。"""

    def __init__(self) -> None:
        self._providers: dict[str, RegisteredEngineProvider] = {}

    @classmethod
    def for_runtime(
        cls,
        adapter: RuntimeAdapter,
        *,
        simulate_known_engines: bool = False,
    ) -> EngineProviderRegistry:
        """从已装配的 Runtime 构造注册表；假模式模拟项会显式标记。"""

        registry = cls()
        configured_name = getattr(adapter, "engine_name", None)
        if not isinstance(configured_name, str) or not configured_name.strip():
            raise ValueError("RuntimeAdapter must expose a stable engine_name")
        runtime_name = configured_name.strip().casefold()
        all_openai_protocols = {
            "openai-compatible",
            "openai-chat-completions",
            "openai-completions",
            "openai-responses",
        }
        if runtime_name == "fake":
            registry.register(
                "fake",
                adapter,
                display_name="Fake Runtime",
                model_protocols=all_openai_protocols,
                simulation=True,
            )
            if not simulate_known_engines:
                return registry
            registry.register(
                "codex_cli",
                FakeRuntimeAdapter(),
                display_name="Codex CLI（演示）",
                model_protocols=all_openai_protocols,
                simulation=True,
            )
            registry.register(
                "deepseek_harness",
                FakeRuntimeAdapter(),
                display_name="DeepSeek Harness（演示）",
                model_protocols={
                    "openai-compatible",
                    "openai-chat-completions",
                    "openai-completions",
                },
                simulation=True,
            )
            return registry
        if runtime_name in {"deepseek", "deepseek_harness"}:
            protocols = {
                "openai-compatible",
                "openai-chat-completions",
                "openai-completions",
            }
            registry.register(
                "deepseek_harness",
                adapter,
                display_name="DeepSeek Harness",
                model_protocols=protocols,
            )
            return registry
        if runtime_name == "codex_cli":
            runtime_capabilities = EngineProviderCapabilities.from_runtime(
                adapter.capabilities
            )
            registry.register(
                "codex_cli",
                adapter,
                display_name="Codex CLI",
                model_protocols={"openai-responses"},
                capabilities=EngineProviderCapabilities(
                    resume=runtime_capabilities.resume,
                    cancellation=runtime_capabilities.cancellation,
                    tool_calls=runtime_capabilities.tool_calls,
                    mcp=runtime_capabilities.mcp,
                    workspace=runtime_capabilities.workspace,
                    sandbox=runtime_capabilities.sandbox,
                    approvals=runtime_capabilities.approvals,
                    model_route_required=True,
                    route_secret_required=True,
                ),
            )
            return registry
        raise ValueError(
            f"Runtime {runtime_name!r} requires an explicit EngineProvider registration"
        )

    @staticmethod
    def _normalize(engine_name: str) -> str:
        return normalize_engine_provider_name(engine_name)

    def register(
        self,
        engine_name: str,
        adapter: RuntimeAdapter,
        *,
        display_name: str,
        model_protocols: Iterable[str],
        capabilities: EngineProviderCapabilities | None = None,
        enabled: bool = True,
        validation_note: str | None = None,
        simulation: bool = False,
    ) -> None:
        """显式登记一个适配器；enabled=False 用于保留未通过验证的运维记录。"""

        normalized = self._normalize(engine_name)
        if normalized in self._providers:
            raise ValueError(f"EngineProvider {normalized!r} already registered")
        if not display_name.strip():
            raise ValueError("display_name must not be blank")
        protocols = frozenset(protocol.strip() for protocol in model_protocols if protocol.strip())
        self._providers[normalized] = RegisteredEngineProvider(
            engine_name=normalized,
            display_name=display_name.strip(),
            adapter=adapter,
            supported_model_protocols=protocols,
            capabilities=capabilities
            or EngineProviderCapabilities.from_runtime(adapter.capabilities),
            enabled=enabled,
            validation_note=validation_note.strip() if validation_note else None,
            simulation=simulation,
        )

    def available(self) -> tuple[dict[str, object], ...]:
        """返回启用项的安全摘要；禁用 Provider 不会出现在选择器中。"""

        return tuple(
            provider.to_public_dict()
            for _name, provider in sorted(self._providers.items())
            if provider.enabled
        )

    def available_names(self) -> tuple[str, ...]:
        """供控制面测试和内部选择器读取不含已停用项的稳定 ID。"""

        return tuple(
            sorted(
                name
                for name, provider in self._providers.items()
                if provider.enabled
            )
        )

    def resolve(self, engine_name: str) -> RegisteredEngineProvider:
        """解析精确 EngineProvider；禁止把缺少的 Provider 路由到其他引擎。"""

        normalized = self._normalize(engine_name)
        provider = self._providers.get(normalized)
        if provider is None or not provider.enabled:
            raise DomainError(
                "ENGINE_PROVIDER_UNAVAILABLE",
                "ExecutionProfile 指向的 EngineProvider 未注册或未启用",
                {"engine_name": normalized},
            )
        return provider

    def validate(
        self,
        profile: ExecutionProfile,
        route: ModelRouteSnapshot | None,
        *,
        requires_tools: bool = False,
        requires_mcp: bool = False,
    ) -> RegisteredEngineProvider:
        """在新 Session 前校验选定的 ModelRoute 协议与 Profile 声明能力。"""

        provider = self.resolve(profile.engine_name)
        if provider.capabilities.model_route_required and route is None:
            raise DomainError(
                "RUNTIME_CONFIGURATION_REQUIRED",
                "该 EngineProvider 要求配置独立 ModelRoute",
                {"engine_name": provider.engine_name},
            )
        if (
            route is not None
            and provider.capabilities.route_secret_required
            and route.secret_ref is None
        ):
            raise DomainError(
                "ENGINE_CONFIGURATION_REQUIRED",
                "该 EngineProvider 的 ModelRoute 必须配置接收方 SecretRef",
                {"engine_name": provider.engine_name, "model_route_id": route.route_id},
            )
        if route is not None and route.protocol not in provider.supported_model_protocols:
            raise DomainError(
                "ENGINE_MODEL_INCOMPATIBLE",
                "当前 EngineProvider 不支持所选 ModelRoute 协议",
                {
                    "engine_name": provider.engine_name,
                    "model_route_id": route.route_id,
                    "protocol": route.protocol,
                },
            )
        missing: list[str] = []
        if profile.tool_policy and not provider.capabilities.tool_calls:
            missing.append("tool_policy")
        if requires_tools and not provider.capabilities.tool_calls:
            missing.append("tools")
        if requires_mcp and not provider.capabilities.mcp:
            missing.append("mcp")
        if profile.workspace_profile_id is not None and not provider.capabilities.workspace:
            missing.append("workspace")
        if profile.sandbox_profile_id is not None and not provider.capabilities.sandbox:
            missing.append("sandbox")
        if profile.approval_policy != "none" and not provider.capabilities.approvals:
            missing.append("approvals")
        if missing:
            raise DomainError(
                "ENGINE_CAPABILITY_UNSUPPORTED",
                "ExecutionProfile 要求的能力未由 EngineProvider 提供",
                {
                    "engine_name": provider.engine_name,
                    "capability": missing[0],
                    "unsupported_capabilities": missing,
                },
            )
        return provider
