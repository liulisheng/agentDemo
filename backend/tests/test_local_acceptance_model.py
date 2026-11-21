"""本地验收模型只对可识别意图发出受限工具调用。"""

from __future__ import annotations

import importlib.util
import json
import sys
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest


@pytest.fixture
def local_model(monkeypatch):
    monkeypatch.setattr(ThreadingHTTPServer, "__init__", lambda self, *_args, **_kwargs: None)
    monkeypatch.setattr(ThreadingHTTPServer, "serve_forever", lambda self: None)
    path = Path(__file__).parents[2] / "deploy" / "control-plane" / "local_acceptance_model.py"
    spec = importlib.util.spec_from_file_location("local_acceptance_model_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _function(name: str, required: list[str]) -> dict[str, object]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": "本地工具",
            "parameters": {"type": "object", "properties": {}, "required": required},
        },
    }


def _append_tool_result(messages, call_id: str, name: str, arguments: dict[str, object], result: object) -> None:
    messages.extend(
        [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {"name": name, "arguments": json.dumps(arguments)},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": call_id, "content": json.dumps(result)},
        ]
    )


def test_natural_workflow_list_starts_with_the_read_only_project_query(local_model, capsys) -> None:
    request = {
        "tools": [
            _function("dolphinscheduler_projects_list", []),
            _function("dolphinscheduler_workflows_list", ["project_code"]),
        ],
        "messages": [{"role": "user", "content": "帮我列出有哪些工作流"}],
    }

    selected = local_model._select_tool(request)

    assert selected == ("dolphinscheduler_projects_list", {})
    assert "帮我列出有哪些工作流" not in capsys.readouterr().out


def test_natural_workflow_list_uses_the_project_code_from_read_result(local_model, capsys) -> None:
    request = {
        "tools": [
            _function("dolphinscheduler_projects_list", []),
            _function("dolphinscheduler_workflows_list", ["project_code"]),
        ],
        "messages": [
            {"role": "user", "content": "帮我列出有哪些工作流"},
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "project-call-1",
                        "type": "function",
                        "function": {"name": "dolphinscheduler_projects_list", "arguments": "{}"},
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "project-call-1",
                "content": json.dumps({"projects": [{"code": 12345, "name": "验收项目"}]}),
            },
        ],
    }

    selected = local_model._select_tool(request)

    assert selected == ("dolphinscheduler_workflows_list", {"project_code": 12345})
    assert "验收项目" not in capsys.readouterr().out


def test_natural_workflow_create_reads_resources_before_preview(local_model, capsys) -> None:
    names = {
        "projects": "dolphinscheduler_projects_list",
        "datasources": "dolphinscheduler_datasources_list",
        "workflows": "dolphinscheduler_workflows_list",
        "preview": "dolphinscheduler_workflow_create_multi_preview",
    }
    request = {
        "tools": [
            _function(names["projects"], []),
            _function(names["datasources"], ["datasource_type"]),
            _function(names["workflows"], ["project_code"]),
            _function(names["preview"], ["project_code", "workflow_name", "nodes"]),
        ],
        "messages": [{"role": "user", "content": "帮我创建一个 ODS 到 DWD 的工作流"}],
    }

    selected = local_model._select_tool(request)
    assert selected == (names["projects"], {})
    _append_tool_result(
        request["messages"], "projects-call", names["projects"], {},
        {"projects": [{"code": 12345, "name": "验收项目"}]},
    )
    selected = local_model._select_tool(request)
    assert selected == (names["datasources"], {"datasource_type": "MYSQL"})
    _append_tool_result(
        request["messages"], "datasources-call", names["datasources"], {"datasource_type": "MYSQL"},
        {"datasources": [{"id": 8, "name": "warehouse", "type": "MYSQL"}]},
    )
    selected = local_model._select_tool(request)
    assert selected == (names["workflows"], {"project_code": 12345})
    _append_tool_result(
        request["messages"], "workflows-call", names["workflows"], {"project_code": 12345},
        {"workflows": []},
    )
    selected = local_model._select_tool(request)

    assert selected is not None and selected[0] == names["preview"]
    assert selected[1]["project_code"] == 12345
    assert selected[1]["nodes"][1]["task_params"]["datasource"] == 8
    assert "帮我创建" not in capsys.readouterr().out


def test_natural_workflow_list_summary_uses_tool_results(local_model, capsys) -> None:
    request = {
        "messages": [
            {"role": "user", "content": "帮我列出有哪些工作流"},
            {
                "role": "assistant",
                "tool_calls": [{"id": "projects-call", "function": {"name": "dolphinscheduler_projects_list", "arguments": "{}"}}],
            },
            {"role": "tool", "tool_call_id": "projects-call", "content": '{"projects":[{"code":12345,"name":"验收项目"}]}'},
            {
                "role": "assistant",
                "tool_calls": [{"id": "workflows-call", "function": {"name": "dolphinscheduler_workflows_list", "arguments": '{"project_code":12345}'}}],
            },
            {"role": "tool", "tool_call_id": "workflows-call", "content": '{"workflows":[{"name":"ODS 到 DWD","releaseState":"OFFLINE"}]}'},
        ]
    }

    text = local_model._final_response_text(request)

    assert "验收项目" in text
    assert "ODS 到 DWD" in text
    assert "OFFLINE" in text
    assert "验收项目" not in capsys.readouterr().out
