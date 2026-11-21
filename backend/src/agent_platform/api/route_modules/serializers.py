"""目录、执行和 Trace 路由共用的 HTTP 响应转换。"""

from __future__ import annotations

from agent_platform.domain.catalog import (
    Deployment,
    DigitalEmployee,
    DigitalEmployeeVersion,
    SkillVersion,
    ToolVersion,
)
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
        "execution_profile_id": value.execution_profile_id,
        "model_route_id": value.model_route_id,
        "workspace_profile_id": value.workspace_profile_id,
        "workspace_profile_version": value.workspace_profile_version,
        "sandbox_profile_id": value.sandbox_profile_id,
        "sandbox_profile_version": value.sandbox_profile_version,
        "execution_profile_version": value.execution_profile_version,
        "approval_policy": value.approval_policy,
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
        "version_summary": {
            "version": value.employee_version.version,
            "status": value.employee_version.status.value,
            "skill_count": len(value.employee_version.skill_version_refs),
            "tool_count": len(value.employee_version.tool_version_refs),
            "execution_profile_id": value.employee_version.execution_profile_id,
            "execution_profile_version": value.employee_version.execution_profile_version,
            "model_route_id": value.employee_version.model_route_id,
        },
    }


def _session_response(value: SessionRecord) -> dict[str, object]:
    return {
        "id": value.session_id,
        "deployment_id": value.deployment_id,
        "employee_version": value.employee_version,
        "trace_id": value.trace_id,
        "runtime_session_ref": value.runtime_session_ref,
        "status": value.status,
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
