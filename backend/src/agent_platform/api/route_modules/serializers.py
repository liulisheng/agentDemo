"""目录、执行和 Trace 路由共用的 HTTP 响应转换。"""

from __future__ import annotations

from agent_platform.domain.catalog import (
    Deployment,
    DigitalEmployee,
    DigitalEmployeeVersion,
    SkillVersion,
    ToolVersion,
)
from agent_platform.domain.deployment_runtime import DeploymentRuntimeConfig
from agent_platform.domain.observability import RunRecord, RunTimeline, SessionRecord, TraceSpan


def _tool_response(value: ToolVersion) -> dict[str, object]:
    return {
        "id": value.tool_id,
        "tenant_id": value.tenant_id,
        "name": value.name,
        "description": value.description,
        "version": value.version,
        "status": value.status.value,
        "protocol": value.protocol,
        "requires_recipient_credentials": value.requires_recipient_credentials,
        "credentials_configured": value.auth_secret_ref is not None,
        "input_schema": dict(value.input_schema),
    }


def _skill_response(value: SkillVersion) -> dict[str, object]:
    return {
        "id": value.skill_id,
        "tenant_id": value.tenant_id,
        "name": value.name,
        "skill_type": value.skill_type.value,
        "instruction": value.instruction,
        "allowed_tool_ids": list(value.allowed_tool_ids),
        "allowed_tool_versions": [
            {"tool_id": tool_id, "version": version}
            for tool_id, version in value.allowed_tool_versions
        ],
        "version": value.version,
        "status": value.status.value,
    }


def _employee_response(value: DigitalEmployee) -> dict[str, object]:
    return {
        "id": value.employee_id,
        "tenant_id": value.tenant_id,
        "name": value.name,
        "role_name": value.role_name,
        "description": value.description,
        "status": value.status.value,
        "published_to_gallery": value.published_to_gallery,
    }


def _version_response(value: DigitalEmployeeVersion) -> dict[str, object]:
    return {
        "employee_id": value.employee_id,
        "tenant_id": value.tenant_id,
        "version": value.version,
        "system_prompt": value.system_prompt,
        "skill_ids": list(value.skill_version_ids),
        "tool_ids": list(value.tool_version_ids),
        "skill_version_refs": [
            {"skill_id": skill_id, "version": version}
            for skill_id, version in value.skill_version_refs
        ],
        "tool_version_refs": [
            {"tool_id": tool_id, "version": version}
            for tool_id, version in value.tool_version_refs
        ],
        "status": value.status.value,
    }


def _deployment_response(value: Deployment) -> dict[str, object]:
    """独立输出部署状态与固定版本状态，便于展示历史部署。"""

    return {
        "id": value.deployment_id,
        "tenant_id": value.tenant_id,
        "employee_id": value.employee_id,
        "version": value.employee_version.version,
        "status": value.status.value,
        "runtime_config_version": value.runtime_config_version,
        "version_summary": {
            "version": value.employee_version.version,
            "status": value.employee_version.status.value,
            "skill_count": len(value.employee_version.skill_version_refs),
            "tool_count": len(value.employee_version.tool_version_refs),
        },
    }


def _deployment_runtime_config_response(
    value: Deployment, config: DeploymentRuntimeConfig | None
) -> dict[str, object]:
    """只返回配置修订的资源引用和策略，不序列化路由快照中的 SecretRef。"""

    revision = None
    if config is not None:
        revision = {
            "config_version": config.config_version,
            "mode": config.config_mode.value,
            "execution_profile_id": config.execution_profile_id,
            "execution_profile_version": config.execution_profile_version,
            "model_route_id": config.model_route_id,
            "workspace_profile_id": config.workspace_profile_id,
            "workspace_profile_version": config.workspace_profile_version,
            "sandbox_profile_id": config.sandbox_profile_id,
            "sandbox_profile_version": config.sandbox_profile_version,
            "approval_policy": dict(config.approval_policy),
            "runtime_options": dict(config.runtime_options),
            "created_by": config.created_by,
        }
    return {
        "deployment_id": value.deployment_id,
        "deployment_status": value.status.value,
        "runtime_config_version": value.runtime_config_version,
        "revision": revision,
    }


def _session_response(value: SessionRecord) -> dict[str, object]:
    return {
        "id": value.session_id,
        "deployment_id": value.deployment_id,
        "employee_version": value.employee_version,
        "trace_id": value.trace_id,
        "runtime_session_ref": value.runtime_session_ref,
        "status": value.status,
        "execution_profile_id": value.execution_profile_id,
        "execution_profile_version": value.execution_profile_version,
        "engine_name": value.engine_name,
        "model_route_id": value.model_route_id,
        "model_alias": value.model_alias,
        "runtime_config_version": value.runtime_config_version,
        "runtime_profile_source": value.runtime_profile_source,
        "runtime_model_route_source": value.runtime_model_route_source,
        "runtime_options": value.runtime_options.to_mapping(),
    }


def _run_response(value: RunRecord) -> dict[str, object]:
    return {
        "id": value.run_id,
        "session_id": value.session_id,
        "trace_id": value.trace_id,
        "status": value.status.value,
        "result": value.result_text,
        "result_ref": value.result_ref,
        "error_category": value.error_category,
        "snapshot": {
            "employee_id": value.snapshot.employee_id,
            "employee_version": value.snapshot.employee_version,
            "skill_ids": list(value.snapshot.skill_version_ids),
            "tool_ids": list(value.snapshot.tool_version_ids),
            "execution_profile_id": value.snapshot.execution_profile_id,
            "engine_name": value.snapshot.engine_name,
            "execution_profile_version": value.snapshot.execution_profile_version,
            "model_route_id": value.snapshot.model_route_id,
            "model_alias": value.snapshot.model_alias,
            "runtime_config_version": value.snapshot.runtime_config_version,
            "runtime_profile_source": value.snapshot.runtime_profile_source,
            "runtime_model_route_source": value.snapshot.runtime_model_route_source,
            "runtime_options": value.snapshot.runtime_options.to_mapping(),
            "model_route_snapshot": (
                {
                    "route_id": value.snapshot.model_route_snapshot.route_id,
                    "route_version": value.snapshot.model_route_snapshot.route_version,
                    "route_alias": value.snapshot.model_route_snapshot.route_alias,
                    "provider_kind": value.snapshot.model_route_snapshot.provider_kind,
                    "model_id": value.snapshot.model_route_snapshot.model_id,
                    "protocol": value.snapshot.model_route_snapshot.protocol,
                }
                if value.snapshot.model_route_snapshot is not None
                else None
            ),
            "hub_resource_refs": [
                {
                    "resource_type": item.local_resource_type,
                    "local_resource_id": item.local_resource_id,
                    "local_resource_version": item.local_resource_version,
                    "hub_resource_id": item.hub_resource_id,
                    "hub_version": item.hub_version,
                    "manifest_sha256": item.manifest_sha256,
                    "mode": item.mode,
                }
                for item in value.snapshot.hub_resource_refs
            ],
            "skill_file_packages": [
                {
                    "skill_id": package.skill_id,
                    "skill_version": package.skill_version,
                    "entry_path": package.entry_path,
                    "manifest_sha256": package.manifest_sha256,
                    "files": [
                        {
                            "path": item.file_path,
                            "mime_type": item.mime_type,
                            "size_bytes": item.size_bytes,
                            "sha256": item.content_sha256,
                        }
                        for item in package.files
                    ],
                }
                for package in value.snapshot.skill_file_packages
            ],
        },
        "runtime_reference": (
            {
                "engine_name": value.runtime_reference.engine_name,
                "runtime_session_ref": value.runtime_reference.runtime_session_ref,
                "runtime_result_ref": value.runtime_reference.runtime_result_ref,
            }
            if value.runtime_reference
            else None
        ),
    }


def _span_response(value: TraceSpan) -> dict[str, object]:
    return {
        "id": value.span_id,
        "trace_id": value.trace_id,
        "parent_span_id": value.parent_span_id,
        "run_id": value.run_id,
        "span_type": value.span_type.value,
        "status": value.status,
        "sequence": value.sequence,
        "attributes": dict(value.attributes),
    }


def _timeline_response(value: RunTimeline) -> dict[str, object]:
    return {
        "run": _run_response(value.run),
        "spans": [_span_response(span) for span in value.spans],
        "usage": {
            "input_tokens": value.usage.input_tokens,
            "output_tokens": value.usage.output_tokens,
            "total_tokens": value.usage.total_tokens,
            "reasoning_tokens": value.usage.reasoning_tokens,
            "source": value.usage.source,
            "estimated": value.usage.estimated,
            "cost_amount": value.usage.cost_amount,
            "currency": value.usage.currency,
            "pricing_version": value.usage.pricing_version,
            "provider_request_id": value.usage.provider_request_id,
        },
        "runtime_reference": (
            {
                "engine_name": value.runtime_reference.engine_name,
                "runtime_session_ref": value.runtime_reference.runtime_session_ref,
                "runtime_result_ref": value.runtime_reference.runtime_result_ref,
            }
            if value.runtime_reference
            else None
        ),
    }
