from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from textwrap import dedent
from types import SimpleNamespace

import pytest

from harness_engine.mcp_proxy import (
    McpProxyConfig,
    McpProxyPolicyError,
    filter_tool_catalog,
)


def test_stdio_proxy_validates_profile_digest_and_exposes_only_run_allowlist() -> None:
    projects_schema = {"type": "object", "properties": {"page": {"type": "integer"}}}
    create_schema = {"type": "object", "properties": {"name": {"type": "string"}}}
    profile_tools = [
        SimpleNamespace(name="projects_list", description="列项目", input_schema=projects_schema),
        SimpleNamespace(name="workflow_create", description="创建工作流", input_schema=create_schema),
    ]
    canonical_catalog = json.dumps(
        [
            {"name": "projects_list", "input_schema": projects_schema},
            {"name": "workflow_create", "input_schema": create_schema},
        ],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    catalog_digest = hashlib.sha256(canonical_catalog.encode("utf-8")).hexdigest()
    canonical_projects_schema = json.dumps(
        projects_schema,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    projects_schema_digest = hashlib.sha256(canonical_projects_schema.encode("utf-8")).hexdigest()
    config = McpProxyConfig(
        command="/usr/bin/python3",
        args=("-m", "fake_mcp"),
        cwd="/tmp",
        profile_tool_names=("projects_list", "workflow_create"),
        run_allowed_tool_names=("projects_list",),
        profile_catalog_sha256=catalog_digest,
        expected_tool_schema_sha256={"projects_list": projects_schema_digest},
    )

    visible = filter_tool_catalog(profile_tools, config)

    assert tuple(tool.name for tool in visible) == ("projects_list",)
    assert visible[0].input_schema == projects_schema
    with pytest.raises(McpProxyPolicyError, match="allowlist"):
        config.require_allowed("workflow_create")

    mismatched = McpProxyConfig(
        command="/usr/bin/python3",
        args=("-m", "fake_mcp"),
        cwd="/tmp",
        profile_tool_names=("projects_list", "workflow_create"),
        run_allowed_tool_names=("projects_list",),
        profile_catalog_sha256="0" * 64,
        expected_tool_schema_sha256={"projects_list": projects_schema_digest},
    )
    with pytest.raises(McpProxyPolicyError, match="catalog"):
        filter_tool_catalog(profile_tools, mismatched)


def test_stdio_proxy_process_lists_only_allowed_tools_and_rejects_other_calls(tmp_path) -> None:
    pytest.importorskip("mcp")
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    projects_schema = {
        "type": "object",
        "properties": {"page": {"type": "integer"}},
        "additionalProperties": False,
    }
    create_schema = {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "additionalProperties": False,
    }
    canonical_catalog = json.dumps(
        [
            {"name": "projects_list", "input_schema": projects_schema},
            {"name": "workflow_create", "input_schema": create_schema},
        ],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    catalog_digest = hashlib.sha256(canonical_catalog.encode("utf-8")).hexdigest()
    schema_digest = hashlib.sha256(
        json.dumps(
            projects_schema,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    upstream = tmp_path / "fake_upstream.py"
    upstream.write_text(
        dedent(
            '''\
            import anyio
            from mcp.server import Server
            from mcp.server.stdio import stdio_server
            from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool

            async def list_tools(_ctx, _params):
                return ListToolsResult(tools=[
                    Tool(name="projects_list", description="列项目", input_schema={
                        "type": "object",
                        "properties": {"page": {"type": "integer"}},
                        "additionalProperties": False,
                    }),
                    Tool(name="workflow_create", description="创建工作流", input_schema={
                        "type": "object",
                        "properties": {"name": {"type": "string"}},
                        "additionalProperties": False,
                    }),
                ])

            async def call_tool(_ctx, params):
                if params.name == "projects_list":
                    return CallToolResult(content=[TextContent(type="text", text="项目列表")])
                return CallToolResult(content=[TextContent(type="text", text="写调用")])

            server = Server("fake-upstream", on_list_tools=list_tools, on_call_tool=call_tool)

            async def run():
                async with stdio_server() as streams:
                    await server.run(streams[0], streams[1], server.create_initialization_options())

            anyio.run(run)
            '''
        ),
        encoding="utf-8",
    )
    config = McpProxyConfig(
        command=sys.executable,
        args=(str(upstream),),
        cwd=str(tmp_path),
        profile_tool_names=("projects_list", "workflow_create"),
        run_allowed_tool_names=("projects_list",),
        profile_catalog_sha256=catalog_digest,
        expected_tool_schema_sha256={"projects_list": schema_digest},
    )
    config_path = tmp_path / "proxy.json"
    config_path.write_text(json.dumps(config.to_public_dict()), encoding="utf-8")
    config_path.chmod(0o600)
    source_root = Path(__file__).resolve().parents[1] / "src"

    async def exercise_proxy():
        async with stdio_client(
            StdioServerParameters(
                command=sys.executable,
                args=["-m", "harness_engine.mcp_proxy", "--config", str(config_path)],
                env={"PYTHONPATH": str(source_root)},
                cwd=str(tmp_path),
            )
        ) as streams, ClientSession(*streams) as session:
            await session.initialize()
            listing = await session.list_tools()
            assert [tool.name for tool in listing.tools] == ["projects_list"]
            allowed = await session.call_tool("projects_list", {"page": 1})
            assert allowed.content[0].text == "项目列表"
            denied = await session.call_tool("workflow_create", {"name": "bad"})
            assert denied.is_error is True
            assert denied.content[0].text == "工具不在当前 Run allowlist 中"

    anyio = pytest.importorskip("anyio")
    anyio.run(exercise_proxy)
