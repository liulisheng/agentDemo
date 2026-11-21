import hashlib
import json
from types import MappingProxyType

import pytest

from agent_platform.domain.catalog import (
    LifecycleStatus,
    ToolVersion,
    compute_tool_input_schema_sha256,
)
from agent_platform.infrastructure.mysql_catalog import MySqlCatalogRepository


def test_mcp_schema_digest_accepts_the_immutable_schema_exposed_by_tool_versions() -> None:
    schema = MappingProxyType(
        {
            "type": "object",
            "properties": {"project_name": {"type": "string"}},
            "required": ["project_name"],
        }
    )
    expected = hashlib.sha256(
        json.dumps(
            dict(schema),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()

    assert compute_tool_input_schema_sha256(schema) == expected


def test_mcp_tool_version_pins_server_schema_and_write_risk() -> None:
    schema = {
        "type": "object",
        "properties": {"project_id": {"type": "string"}},
        "required": ["project_id"],
        "additionalProperties": False,
    }
    expected_schema_digest = hashlib.sha256(
        json.dumps(
            schema,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()

    tool = ToolVersion(
        tool_id="tool-workflow-create",
        tenant_id="tenant-1",
        name="create_workflow",
        description="创建 OFFLINE 工作流",
        input_schema=schema,
        protocol="mcp",
        status=LifecycleStatus.PUBLISHED,
        mcp_server_profile_id="profile-dolphin",
        mcp_server_profile_version=3,
        remote_tool_name="create_workflow",
        input_schema_sha256=expected_schema_digest,
        mcp_risk_level="WRITE",
        preview_tool_name="create_workflow_preview",
        idempotency_support=False,
    )

    assert tool.remote_tool_name == "create_workflow"
    assert tool.mcp_server_profile_version == 3
    assert tool.input_schema_sha256 == expected_schema_digest
    assert tool.mcp_risk_level == "WRITE"
    assert tool.preview_tool_name == "create_workflow_preview"


def test_mcp_tool_version_rejects_partial_profile_binding() -> None:
    with pytest.raises(ValueError, match="profile"):
        ToolVersion(
            tool_id="tool-partial",
            tenant_id="tenant-1",
            name="read_projects",
            description="读取项目",
            input_schema={"type": "object"},
            protocol="mcp",
            mcp_server_profile_id="profile-dolphin",
        )


def test_non_mcp_tool_cannot_bind_a_remote_mcp_name() -> None:
    with pytest.raises(ValueError, match="mcp"):
        ToolVersion(
            tool_id="tool-builtin",
            tenant_id="tenant-1",
            name="echo",
            description="回显",
            input_schema={"type": "object"},
            protocol="builtin",
            remote_tool_name="remote_echo",
        )


def test_mysql_tool_row_restores_mcp_profile_and_risk_fields() -> None:
    schema = {"type": "object", "properties": {"project_id": {"type": "string"}}}
    digest = hashlib.sha256(
        json.dumps(
            schema,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    row = (
        "tool-1",
        "tenant-1",
        "create_workflow",
        "创建工作流",
        json.dumps(schema),
        2,
        "PUBLISHED",
        "mcp",
        "mcp",
        None,
        False,
        "profile-1",
        3,
        "create_workflow",
        digest,
        "preview_workflow",
        "WRITE",
        False,
    )

    tool = MySqlCatalogRepository._load_tool(row)

    assert tool.mcp_server_profile_id == "profile-1"
    assert tool.mcp_server_profile_version == 3
    assert tool.remote_tool_name == "create_workflow"
    assert tool.input_schema_sha256 == digest
    assert tool.preview_tool_name == "preview_workflow"
    assert tool.mcp_risk_level == "WRITE"
