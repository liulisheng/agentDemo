"""按验收标记发起一条固定工具调用的 OpenAI Chat Completions 测试端点。"""

from __future__ import annotations

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from itertools import count

_calls = count(1)


def _current_user_message(messages: list[object]) -> tuple[int, str]:
    last_user_index = max(
        (
            index
            for index, item in enumerate(messages)
            if isinstance(item, dict) and item.get("role") == "user"
        ),
        default=-1,
    )
    if last_user_index < 0 or not isinstance(messages[last_user_index], dict):
        return last_user_index, ""
    return last_user_index, str(messages[last_user_index].get("content", ""))


def _tool_results_since_current_user(
    messages: list[object], last_user_index: int
) -> dict[str, dict[str, object]]:
    """只读取当前 turn 的工具结果，避免把上一轮已读资源当作新结果。"""

    call_id_to_name: dict[str, str] = {}
    results: dict[str, dict[str, object]] = {}
    for item in messages[last_user_index + 1 :]:
        if not isinstance(item, dict):
            continue
        for call in item.get("tool_calls", []) if isinstance(item.get("tool_calls"), list) else []:
            if not isinstance(call, dict):
                continue
            function = call.get("function", {})
            if (
                isinstance(function, dict)
                and isinstance(function.get("name"), str)
                and isinstance(call.get("id"), str)
            ):
                call_id_to_name[str(call["id"])] = str(function["name"])
        if item.get("role") != "tool":
            continue
        name = item.get("name")
        if not isinstance(name, str):
            call_id = item.get("tool_call_id")
            name = call_id_to_name.get(str(call_id)) if call_id is not None else None
        content = item.get("content")
        if not isinstance(name, str) or not isinstance(content, str):
            continue
        try:
            value = json.loads(content)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            results[name] = value
    return results


def _result_for_tool(
    results: dict[str, dict[str, object]], remote_suffix: str
) -> dict[str, object] | None:
    return next(
        (value for name, value in results.items() if name.endswith(remote_suffix)),
        None,
    )


def _is_natural_workflow_list(text: str) -> bool:
    return "工作流" in text and any(
        phrase in text for phrase in ("列出", "有哪些", "查看", "查询", "列一下")
    )


def _is_natural_workflow_create(text: str) -> bool:
    return "工作流" in text and any(
        phrase in text for phrase in ("创建", "新建", "生成", "帮我建")
    ) and not _is_natural_workflow_list(text)


def _select_tool(request: dict[str, object]) -> tuple[str, dict[str, object]] | None:
    tools = request.get("tools", [])
    messages = request.get("messages", [])
    if not isinstance(tools, list) or not isinstance(messages, list):
        return None
    available = [
        str(item.get("function", {}).get("name"))
        for item in tools
        if isinstance(item, dict) and isinstance(item.get("function"), dict)
    ]
    last_user_index, current_user_text = _current_user_message(messages)
    results = _tool_results_since_current_user(messages, last_user_index)
    called: set[str] = set()
    for item in messages[last_user_index + 1 :]:
        if not isinstance(item, dict):
            continue
        for call in item.get("tool_calls", []) if isinstance(item.get("tool_calls"), list) else []:
            if not isinstance(call, dict):
                continue
            function = call.get("function", {})
            if isinstance(function, dict) and isinstance(function.get("name"), str):
                called.add(str(function["name"]))
        if item.get("role") == "tool":
            tool_name = item.get("name")
            if isinstance(tool_name, str):
                called.add(tool_name)

    def available_name(suffix: str, required_fields: tuple[str, ...] = ()) -> str | None:
        for item in tools:
            if (
                isinstance(item, dict)
                and isinstance(item.get("function"), dict)
                and str(item["function"].get("name", "")).endswith(suffix)
            ):
                return str(item["function"]["name"])
        for item in tools:
            if not isinstance(item, dict) or not isinstance(item.get("function"), dict):
                continue
            function = item["function"]
            name = str(function.get("name", ""))
            alias = name.rsplit("__", maxsplit=1)[-1]
            alias_prefix = re.sub(r"_[0-9a-f]{12}$", "", alias)
            parameters = function.get("parameters", {})
            required = parameters.get("required", []) if isinstance(parameters, dict) else []
            if suffix.startswith(alias_prefix) and set(required_fields) <= set(required):
                return name
        return None

    action: tuple[str, dict[str, object]] | None = None
    if "LOCAL_ACCEPTANCE_CREATE_PROJECT" in current_user_text:
        name = available_name("dolphinscheduler_project_create_preview", ("project_name",))
        if name and name not in called:
            action = (name, {"project_name": "AgentDemo Acceptance", "description": "本地隔离验收项目"})
    elif "LOCAL_ACCEPTANCE_CREATE_DATASOURCE" in current_user_text:
        name = available_name(
            "dolphinscheduler_datasource_create_preview",
            ("datasource_type", "name", "password_env_alias"),
        )
        if name and name not in called:
            action = (
                name,
                {
                    "datasource_type": "MYSQL",
                    "name": "agentdemo_warehouse",
                    "note": "本地隔离验收数仓",
                    "host": "mysql-warehouse",
                    "port": 3306,
                    "database": "dolphin_warehouse_agent_acceptance",
                    "username": "dolphin_agent_runtime",
                    "password_env_alias": "WAREHOUSE",
                },
            )
    elif _is_natural_workflow_list(current_user_text):
        project_result = _result_for_tool(results, "dolphinscheduler_projects_list")
        if project_result is None:
            name = available_name("dolphinscheduler_projects_list")
            if name and name not in called:
                action = (name, {})
        else:
            projects = project_result.get("projects", [])
            project_code = (
                int(projects[0]["code"])
                if isinstance(projects, list)
                and projects
                and isinstance(projects[0], dict)
                and isinstance(projects[0].get("code"), int)
                else None
            )
            workflow_result = _result_for_tool(results, "dolphinscheduler_workflows_list")
            if project_code is not None and workflow_result is None:
                name = available_name(
                    "dolphinscheduler_workflows_list", ("project_code",)
                )
                if name and name not in called:
                    action = (name, {"project_code": project_code})
    elif (
        "LOCAL_ACCEPTANCE_BUILD_WORKFLOW" in current_user_text
        or _is_natural_workflow_create(current_user_text)
    ):
        read_tools = (
            "dolphinscheduler_projects_list",
            "dolphinscheduler_datasources_list",
            "dolphinscheduler_workflows_list",
        )
        for suffix in read_tools:
            if _result_for_tool(results, suffix) is not None:
                continue
            name = available_name(suffix)
            if name and name not in called:
                args: dict[str, object] = {}
                if suffix.endswith("datasources_list"):
                    args["datasource_type"] = "MYSQL"
                if suffix.endswith("workflows_list"):
                    try:
                        marker = next(
                            line for line in current_user_text.splitlines() if line.startswith("project_code=")
                        )
                        args["project_code"] = int(marker.split("=", 1)[1].split()[0])
                    except (StopIteration, ValueError):
                        project_result = _result_for_tool(
                            results, "dolphinscheduler_projects_list"
                        ) or {}
                        projects = project_result.get("projects", [])
                        args["project_code"] = (
                            int(projects[0]["code"])
                            if isinstance(projects, list)
                            and projects
                            and isinstance(projects[0], dict)
                            and isinstance(projects[0].get("code"), int)
                            else 0
                        )
                action = (name, args)
                break
        if action is None:
            name = available_name(
                "dolphinscheduler_workflow_create_multi_preview",
                ("project_code", "workflow_name", "nodes"),
            )
            if name and name not in called:
                values: dict[str, str] = {}
                for line in current_user_text.splitlines():
                    if "=" in line:
                        key, value = line.split("=", 1)
                        values[key.strip()] = value.strip()
                project_result = _result_for_tool(results, "dolphinscheduler_projects_list") or {}
                projects = project_result.get("projects", [])
                datasource_result = _result_for_tool(results, "dolphinscheduler_datasources_list") or {}
                datasources = datasource_result.get("datasources", [])
                project_code = int(
                    values.get(
                        "project_code",
                        str(projects[0].get("code", 0)) if isinstance(projects, list) and projects and isinstance(projects[0], dict) else "0",
                    )
                )
                datasource_id = int(
                    values.get(
                        "datasource_id",
                        str(datasources[0].get("id", 0)) if isinstance(datasources, list) and datasources and isinstance(datasources[0], dict) else "0",
                    )
                )
                if project_code <= 0 or datasource_id <= 0:
                    return None
                action = (
                    name,
                    {
                        "project_code": project_code,
                        "workflow_name": "ods_test_user_to_dwd_test_user_acceptance",
                        "description": "将 ODS 测试用户规范化写入 DWD；仅创建离线工作流。",
                        "nodes": [
                            {
                                "node_id": "extract_ods_test_user",
                                "name": "ODS 用户基础清洗",
                                "task_type": "SQL",
                                "task_params": {
                                    "type": "MYSQL",
                                    "datasource": datasource_id,
                                    "sql": "SELECT user_id, user_name, register_time FROM ods_test_user",
                                },
                                "depends_on": [],
                            },
                            {
                                "node_id": "load_dwd_test_user",
                                "name": "写入 DWD 用户明细",
                                "task_type": "SQL",
                                "task_params": {
                                    "type": "MYSQL",
                                    "datasource": datasource_id,
                                    "sql": "INSERT INTO dwd_test_user (user_id, user_name, register_date) SELECT user_id, user_name, DATE(register_time) FROM ods_test_user",
                                },
                                "depends_on": ["extract_ods_test_user"],
                            },
                        ],
                    },
                )
    if action is None:
        return None
    return action


def _final_response_text(request: dict[str, object]) -> str:
    """为本地 smoke provider 返回可核对的结果，避免无工具调用却显示成功。"""

    messages = request.get("messages", [])
    if not isinstance(messages, list):
        return "本地验收模型未读取到对话内容，未调用 MCP 工具。"
    last_user_index, current_user_text = _current_user_message(messages)
    results = _tool_results_since_current_user(messages, last_user_index)
    if _is_natural_workflow_list(current_user_text):
        projects = (_result_for_tool(results, "dolphinscheduler_projects_list") or {}).get("projects", [])
        workflow_result = _result_for_tool(results, "dolphinscheduler_workflows_list")
        if not isinstance(projects, list) or not projects:
            return "DolphinScheduler 当前没有可读取的项目；本次只执行了只读查询。"
        project = projects[0] if isinstance(projects[0], dict) else {}
        project_name = str(project.get("name", "项目"))
        if workflow_result is None:
            return f"已读取项目 {project_name}；工作流列表查询未完成。"
        workflows = workflow_result.get("workflows", [])
        names: list[str] = []
        if isinstance(workflows, list):
            for item in workflows:
                if not isinstance(item, dict):
                    continue
                definition = item.get("workflowDefinition", item)
                if isinstance(definition, dict):
                    name = definition.get("name")
                    state = definition.get("releaseState")
                    if isinstance(name, str):
                        names.append(f"{name}（{state or '状态未知'}）")
        if not names:
            return f"已查询项目 {project_name}；目前没有工作流。"
        return f"项目 {project_name} 有 {len(names)} 个工作流：" + "、".join(names)
    if _is_natural_workflow_create(current_user_text):
        preview_result = _result_for_tool(
            results, "dolphinscheduler_workflow_create_multi_preview"
        )
        if preview_result is not None:
            return "两节点 ODS→DWD 工作流预览已生成，等待提交人审核；当前未创建或运行。"
    if any(marker in current_user_text for marker in (
        "LOCAL_ACCEPTANCE_CREATE_PROJECT",
        "LOCAL_ACCEPTANCE_CREATE_DATASOURCE",
        "LOCAL_ACCEPTANCE_BUILD_WORKFLOW",
    )):
        if any(
            _result_for_tool(results, suffix) is not None
            for suffix in (
                "dolphinscheduler_project_create_preview",
                "dolphinscheduler_datasource_create_preview",
                "dolphinscheduler_workflow_create_multi_preview",
            )
        ):
            return "验收预览已生成并等待提交人审核；远端写入尚未发生。"
    return "本地验收模型未识别此任务，因此没有调用 MCP 工具，也没有执行任何远端操作。"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(length))
        selected = _select_tool(request)
        print(
            json.dumps(
                {
                    "selected_tool": selected[0] if selected is not None else None,
                    "selected_argument_keys": sorted(selected[1]) if selected is not None else [],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        if selected is None:
            message: dict[str, object] = {
                "role": "assistant",
                "content": _final_response_text(request),
            }
            finish_reason = "stop"
        else:
            name, arguments = selected
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": f"call-local-{next(_calls)}",
                        "type": "function",
                        "function": {
                            "name": name,
                            "arguments": json.dumps(arguments, ensure_ascii=False),
                        },
                    }
                ],
            }
            finish_reason = "tool_calls"
        response_id = f"chatcmpl-local-{next(_calls)}"
        if request.get("stream"):
            delta: dict[str, object] = {key: value for key, value in message.items() if key != "role"}
            chunks = [
                {
                    "id": response_id,
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "acceptance-model",
                    "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
                },
                {
                    "id": response_id,
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "acceptance-model",
                    "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
                },
            ]
            body = "".join(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n" for chunk in chunks)
            body += "data: [DONE]\n\n"
            content_type = "text/event-stream"
        else:
            body = json.dumps(
                {
                    "id": response_id,
                    "object": "chat.completion",
                    "created": 1,
                    "model": "acceptance-model",
                    "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
                    "usage": {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28},
                },
                ensure_ascii=False,
            )
            content_type = "application/json"
        encoded = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, _format: str, *_args: object) -> None:
        return


ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
