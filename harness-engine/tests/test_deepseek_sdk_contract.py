from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

import pytest

pytest.importorskip("deepseek_harness")
pytest.importorskip("mcp")

from harness_engine.core import (
    EmployeeContext,
    EngineRequest,
    EngineToolDefinition,
    SkillFileContext,
)
from harness_engine.engines.deepseek import DeepSeekHarnessEngine
from harness_engine.mcp_proxy import McpProxyConfig, McpProxyLaunch


class OpenAICompatibleHandler(BaseHTTPRequestHandler):
    requests: ClassVar[list[dict[str, object]]] = []

    def do_POST(self) -> None:
        size = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(size))
        type(self).requests.append(payload)
        message = {"role": "assistant", "content": "sdk-mcp-contract-ok"}
        response_id = f"chatcmpl-contract-{len(type(self).requests)}"
        if payload.get("stream"):
            chunks = [
                {
                    "id": response_id,
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "contract-model",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"role": "assistant", "content": message["content"]},
                            "finish_reason": None,
                        }
                    ],
                },
                {
                    "id": response_id,
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "contract-model",
                    "choices": [
                        {"index": 0, "delta": {}, "finish_reason": "stop"}
                    ],
                },
            ]
            body = (
                "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks)
                + "data: [DONE]\n\n"
            ).encode("utf-8")
            content_type = "text/event-stream"
        else:
            response = {
                "id": response_id,
                "object": "chat.completion",
                "created": 1,
                "model": "contract-model",
                "choices": [
                    {"index": 0, "message": message, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
            body = json.dumps(response).encode("utf-8")
            content_type = "application/json"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def test_pinned_sdk_receives_each_run_context_tools_and_resumes_session(tmp_path):
    OpenAICompatibleHandler.requests.clear()
    server = ThreadingHTTPServer(("127.0.0.1", 0), OpenAICompatibleHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config = tmp_path / "local-provider.json"
    config.write_text(
        json.dumps(
            {
                "provider": "local-contract",
                "model": "contract-model",
                "base_url": f"http://127.0.0.1:{server.server_port}/v1",
                "api_key": "offline-contract-token",
                "api": "openai-completions",
            }
        ),
        encoding="utf-8",
    )
    source_root = Path(__file__).resolve().parents[1] / "src"
    isolated_env = {
        "PYTHONPATH": os.pathsep.join(
            [str(source_root), os.environ.get("PYTHONPATH", "")]
        ).rstrip(os.pathsep)
    }

    skill_text = "创建的工作流必须保持 OFFLINE。"
    employee_context = EmployeeContext(
        employee_id="employee-dw",
        employee_version=7,
        system_prompt="处理数仓工作流任务。",
        skill_refs=(("skill-dw", 2),),
        skill_instructions=("创建前先读取项目和数据源。",),
        skill_files=(
            SkillFileContext(
                path="SKILL.md",
                content=skill_text,
                content_sha256=hashlib.sha256(skill_text.encode()).hexdigest(),
                skill_id="skill-dw",
                skill_version=2,
            ),
        ),
        model_route_id="route-dw",
    )
    second_employee_context = EmployeeContext(
        employee_id="employee-ops",
        employee_version=3,
        system_prompt="只处理运维员工任务。",
        skill_instructions=("先查看当前运行状态。",),
        model_route_id="route-ops",
    )
    echo_tool = EngineToolDefinition(
        name="echo",
        description="Return the supplied text without side effects.",
        input_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
        risk_level="READ",
        remote_tool_name="echo",
    )
    harness_home_ref = "harness-session:" + "d" * 64

    def new_engine(request: EngineRequest) -> DeepSeekHarnessEngine:
        mcp_launches = ()
        if request.tool_definitions:
            tool = request.tool_definitions[0]
            tool_schema = dict(tool.input_schema)
            canonical_catalog = json.dumps(
                [{"name": tool.name, "input_schema": tool_schema}],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            catalog_digest = hashlib.sha256(canonical_catalog.encode("utf-8")).hexdigest()
            schema_digest = hashlib.sha256(
                json.dumps(
                    tool_schema,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            proxy_config = McpProxyConfig(
                command=sys.executable,
                args=("-m", "harness_engine.mcp_server", "--enable", tool.name),
                cwd=str(source_root.parent),
                profile_tool_names=(tool.name,),
                run_allowed_tool_names=(tool.name,),
                profile_catalog_sha256=catalog_digest,
                expected_tool_schema_sha256={tool.name: schema_digest},
                    environment_names=("PYTHONPATH",),
            )
            mcp_launches = (
                McpProxyLaunch(
                    server_name="python-tools",
                    config=proxy_config,
                        environment={"PYTHONPATH": str(source_root)},
                ),
            )
        return DeepSeekHarnessEngine.for_run_request(
            request,
            harness_state_root=tmp_path,
            profile="sdk-minimal",
            local_config=config,
            env=isolated_env,
            mcp_proxy_launches=mcp_launches,
            initialize_timeout_seconds=15,
            request_timeout_seconds=10,
        )

    try:
        first_request = EngineRequest(
            prompt="生成工作流预览。",
            session_id="harness-pending:contract-session",
            run_id="run-contract-1",
            employee_context=employee_context,
            tool_definitions=(echo_tool,),
            harness_home_ref=harness_home_ref,
        )
        engine = new_engine(first_request)
        engine.start()
        first = engine.run(first_request)
        engine.close()

        assert first.session is not None
        second_request = EngineRequest(
            prompt="继续并总结结果。",
            session=first.session,
            run_id="run-contract-2",
            employee_context=second_employee_context,
            tool_definitions=(),
            harness_home_ref=harness_home_ref,
        )
        resumed_engine = new_engine(second_request)
        resumed_engine.start()
        second = resumed_engine.run(second_request)
        resumed_engine.close()

        assert first.text == "sdk-mcp-contract-ok", (
            first.finish_reason,
            len(OpenAICompatibleHandler.requests),
            OpenAICompatibleHandler.requests[:1],
        )
        assert second.text == "sdk-mcp-contract-ok"
        assert second.session is not None
        assert second.session.session_id == first.session.session_id
        assert len(OpenAICompatibleHandler.requests) == 2

        first_request = OpenAICompatibleHandler.requests[0]
        system_text = "\n".join(
            str(message.get("content", ""))
            for message in first_request["messages"]
            if message.get("role") == "system"
        )
        assert "处理数仓工作流任务。" in system_text
        assert "创建前先读取项目和数据源。" in system_text
        assert "创建的工作流必须保持 OFFLINE。" in system_text

        tools = first_request["tools"]
        assert [item["function"]["name"] for item in tools] == [
            "mcp__python-tools__echo"
        ]
        echo = next(
            tool for tool in tools
            if tool["function"]["name"] == "mcp__python-tools__echo"
        )
        assert echo["function"]["parameters"]["properties"]["text"]["type"] == "string"

        second_request_body = OpenAICompatibleHandler.requests[1]
        assert second_request_body.get("tools", []) == []
        second_system_text = "\n".join(
            str(message.get("content", ""))
            for message in second_request_body["messages"]
            if message.get("role") == "system"
        )
        assert "只处理运维员工任务。" in second_system_text
        assert "只处理当前固定员工任务" not in second_system_text
        assert all(
            tool["function"]["name"] != "mcp__python-tools__echo"
            for tool in second_request_body.get("tools", [])
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
