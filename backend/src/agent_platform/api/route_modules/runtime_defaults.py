"""用户和租户分别管理执行引擎默认与模型路由默认。"""

from __future__ import annotations

from fastapi import APIRouter

from agent_platform.api.schemas import (
    ExecutionProfileDefaultRequest,
    ModelRouteDefaultRequest,
    RuntimeStrategyDefaultRequest,
)
from agent_platform.application.engine_providers import unsupported_profile_runtime_fields
from agent_platform.domain.common import DomainError
from agent_platform.domain.deployment_runtime import RuntimeOptions

from .common import Identity, RuntimeDefaults, _check_tenant

router = APIRouter()


def _parse_runtime_options(value: dict[str, object] | None) -> RuntimeOptions | None:
    if value is None:
        return None
    try:
        return RuntimeOptions.from_mapping(value)
    except (TypeError, ValueError) as error:
        raise DomainError("INVALID_ARGUMENT", str(error)) from error


def _engine_binding(binding) -> dict[str, object] | None:
    if binding is None:
        return None
    return {
        "binding_id": binding.binding_id,
        "scope_type": binding.scope_type.value,
        "scope_id": binding.scope_id,
        "profile_id": binding.profile_id,
        "profile_version": binding.profile_version,
        "status": binding.status,
    }


def _model_binding(binding) -> dict[str, object] | None:
    if binding is None:
        return None
    return {
        "binding_id": binding.binding_id,
        "scope_type": binding.scope_type.value,
        "scope_id": binding.scope_id,
        "route_id": binding.route_id,
        "status": binding.status,
    }


def _strategy_binding(binding) -> dict[str, object] | None:
    if binding is None:
        return None
    return {
        "binding_id": binding.binding_id,
        "scope_type": binding.scope_type.value,
        "scope_id": binding.scope_id,
        "runtime_options": binding.runtime_options.to_mapping(),
        "status": binding.status,
    }


def _engine_profile(profile) -> dict[str, object]:
    """仅返回引擎与运行策略摘要，模型由独立 ModelRoute 字段解析。"""

    unsupported_runtime_fields = unsupported_profile_runtime_fields(profile)
    return {
        "profile_id": profile.profile_id,
        "version": profile.version,
        "engine_name": profile.engine_name,
        "tool_policy": list(profile.tool_policy),
        "context_policy": profile.context_policy,
        "workspace_profile_id": profile.workspace_profile_id,
        "workspace_profile_version": profile.workspace_profile_version,
        "sandbox_profile_id": profile.sandbox_profile_id,
        "sandbox_profile_version": profile.sandbox_profile_version,
        "approval_policy": profile.approval_policy,
        "runtime_policy_supported": not unsupported_runtime_fields,
        "unsupported_runtime_fields": list(unsupported_runtime_fields),
        "status": profile.status.value,
    }


def _model_route(route) -> dict[str, object]:
    """模型默认响应只暴露模型和路由摘要，不返回端点或凭据引用。"""

    return {
        "route_id": route.route_id,
        "route_version": route.route_version,
        "alias": route.alias,
        "model_name": route.profile.model_name,
        "provider": route.profile.provider,
        "protocol": route.profile.protocol,
        "status": route.status,
    }


@router.get("/execution-profile-bindings/effective")
def get_effective_engine_default(identity: Identity, service: RuntimeDefaults) -> dict[str, object]:
    resolved = service.resolve_engine_profile_default(identity)
    return {
        "binding": _engine_binding(resolved.binding) if resolved else None,
        "profile": _engine_profile(resolved.profile) if resolved else None,
        "source": resolved.source if resolved else None,
    }


@router.get("/execution-profile-bindings/user")
def get_user_engine_default(identity: Identity, service: RuntimeDefaults) -> dict[str, object]:
    return {"binding": _engine_binding(service.get_engine_profile_default(identity, "user"))}


@router.put("/execution-profile-bindings/user")
def set_user_engine_default(
    request: ExecutionProfileDefaultRequest,
    identity: Identity,
    service: RuntimeDefaults,
) -> dict[str, object]:
    _check_tenant(identity, request)
    binding = service.set_engine_profile_default(
        identity, "user", request.profile_id, request.profile_version
    )
    return {"binding": _engine_binding(binding)}


@router.get("/execution-profile-bindings/tenant")
def get_tenant_engine_default(identity: Identity, service: RuntimeDefaults) -> dict[str, object]:
    return {"binding": _engine_binding(service.get_engine_profile_default(identity, "tenant"))}


@router.put("/execution-profile-bindings/tenant")
def set_tenant_engine_default(
    request: ExecutionProfileDefaultRequest,
    identity: Identity,
    service: RuntimeDefaults,
) -> dict[str, object]:
    _check_tenant(identity, request)
    binding = service.set_engine_profile_default(
        identity, "tenant", request.profile_id, request.profile_version
    )
    return {"binding": _engine_binding(binding)}


@router.get("/model-route-bindings/effective")
def get_effective_model_default(identity: Identity, service: RuntimeDefaults) -> dict[str, object]:
    resolved = service.resolve_model_route_default(identity)
    return {
        "binding": _model_binding(resolved.binding) if resolved else None,
        "route": _model_route(resolved.route) if resolved else None,
        "source": resolved.source if resolved else None,
    }


@router.get("/model-route-bindings/user")
def get_user_model_default(identity: Identity, service: RuntimeDefaults) -> dict[str, object]:
    return {"binding": _model_binding(service.get_model_route_default(identity, "user"))}


@router.put("/model-route-bindings/user")
def set_user_model_default(
    request: ModelRouteDefaultRequest,
    identity: Identity,
    service: RuntimeDefaults,
) -> dict[str, object]:
    _check_tenant(identity, request)
    binding = service.set_model_route_default(identity, "user", request.route_id)
    return {"binding": _model_binding(binding)}


@router.get("/model-route-bindings/tenant")
def get_tenant_model_default(identity: Identity, service: RuntimeDefaults) -> dict[str, object]:
    return {"binding": _model_binding(service.get_model_route_default(identity, "tenant"))}


@router.put("/model-route-bindings/tenant")
def set_tenant_model_default(
    request: ModelRouteDefaultRequest,
    identity: Identity,
    service: RuntimeDefaults,
) -> dict[str, object]:
    _check_tenant(identity, request)
    binding = service.set_model_route_default(identity, "tenant", request.route_id)
    return {"binding": _model_binding(binding)}


@router.get("/runtime-strategy-bindings/effective")
def get_effective_runtime_strategy_default(identity: Identity, service: RuntimeDefaults) -> dict[str, object]:
    resolved = service.resolve_runtime_strategy_default(identity)
    return {
        "binding": _strategy_binding(resolved.binding) if resolved else None,
        "runtime_options": resolved.runtime_options.to_mapping() if resolved else None,
        "source": resolved.source if resolved else None,
    }


@router.get("/runtime-strategy-bindings/user")
def get_user_runtime_strategy_default(identity: Identity, service: RuntimeDefaults) -> dict[str, object]:
    return {"binding": _strategy_binding(service.get_runtime_strategy_default(identity, "user"))}


@router.put("/runtime-strategy-bindings/user")
def set_user_runtime_strategy_default(
    request: RuntimeStrategyDefaultRequest,
    identity: Identity,
    service: RuntimeDefaults,
) -> dict[str, object]:
    _check_tenant(identity, request)
    options = _parse_runtime_options(request.runtime_options)
    binding = service.set_runtime_strategy_default(identity, "user", options)
    return {"binding": _strategy_binding(binding)}


@router.get("/runtime-strategy-bindings/tenant")
def get_tenant_runtime_strategy_default(identity: Identity, service: RuntimeDefaults) -> dict[str, object]:
    return {"binding": _strategy_binding(service.get_runtime_strategy_default(identity, "tenant"))}


@router.put("/runtime-strategy-bindings/tenant")
def set_tenant_runtime_strategy_default(
    request: RuntimeStrategyDefaultRequest,
    identity: Identity,
    service: RuntimeDefaults,
) -> dict[str, object]:
    _check_tenant(identity, request)
    options = _parse_runtime_options(request.runtime_options)
    binding = service.set_runtime_strategy_default(identity, "tenant", options)
    return {"binding": _strategy_binding(binding)}
