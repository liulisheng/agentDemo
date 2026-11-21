# 官方 Harness SDK 最小验证记录

状态：已完成本地最小运行时、真实模型 prompt、本地 MCP 工具调用，以及 SDK 会话创建/恢复与员工上下文契约验证；生产审批和容器沙箱链路仍待验证。更新时间：2026-11-19。

兼容策略：预发布 SDK 只通过 `engines/deepseek` 适配器使用；项目公共接口不得暴露其类型。每次升级必须重新运行 [真实验证矩阵](verification-matrix.md)，并记录 SDK/runtime 成对版本和回归结果。

## 验证目标

确认官方 Python SDK 能在目标平台完成以下最小链路：启动打包运行时、初始化指定 Harness home、创建会话、提交文本、读取结果、关闭运行时。验证不等于确认生产沙箱安全，也不覆盖模型质量。

## 固定测试条件

- Python 3.11+。
- 显式临时 `DSH_HOME`，禁止使用用户默认目录。
- 独立临时工作目录。
- 记录 `deepseek-harness-sdk` 与 `deepseek-harness-runtime-bin` 的精确版本。
- 默认先使用 Fake 或不会产生模型请求的初始化/doctor 路径；真实模型调用必须显式设置凭据并单独记录费用。

## 最小验证步骤

1. 创建干净虚拟环境，安装官方 SDK 和匹配 runtime wheel。
2. 导入 `DeepSeekHarness`、`HarnessConfig` 或官方当前公开的等价类型，记录公开构造参数。
3. 使用显式 `dsh_home` 和 `cwd` 初始化；确认不会读取 `~/.dsh`。
4. 执行官方最小文本示例，记录 session id、finish reason、final response 和通知是否可收到。
5. 关闭上下文管理器，确认子进程退出且临时目录可删除。
6. 在相同 Harness home 下恢复刚才的 session，确认恢复失败时不会偷偷创建新会话。
7. 若启用审批，验证没有 responder 时高风险请求会拒绝；该步骤不执行破坏性工具。

## 通过标准

- SDK 和 runtime 版本匹配且可重复安装。
- 初始化失败包含可定位的配置或运行时错误。
- 正常执行返回完整 `RunResult`，不会只返回部分流事件。
- close 后没有遗留 Harness 子进程。
- resume 使用原 session id；历史缺失、目录不兼容和占用状态都有明确错误。
- 所有底座类型都被限制在 DeepSeek 适配器内部，核心协议不依赖官方 SDK。

## 本次验证结果

验证环境为 Windows x64、Python 3.12.3。使用独立虚拟环境安装了 `deepseek-harness-sdk==0.2.2rc2`，pip 自动安装匹配的 `deepseek-harness-runtime-bin==0.2.2rc2`。

- `import deepseek_harness`：通过。
- 使用显式临时 `dsh_home`、工作目录和 `sdk-minimal` profile 启动：通过。
- 发送 `Reply with exactly: deepseek-harness-ok`：通过。
- `final_response`：`deepseek-harness-ok`。
- `finish_reason`：`completed`。
- 事件数：14；通知数：16。
- `close()`：通过；Demo 以 context manager 关闭 runtime。

在同一 Python 3.12 环境安装 `mcp==2.3.0`，并在临时 `DSH_HOME` 的
`sdk-minimal` profile 安装匹配的 `@deepseek-ai/dsh-mcp-client@0.2.1-rc.2` 后，
通过 `DeepSeekHarnessEngine(mcp_tools=("echo",))` 生成 stdio patch。真实 Harness
发现 `mcp__python-tools__echo`，调用结果为 `{"text": "mcp-real-ok"}`，随后正常
关闭 runtime。该结果覆盖本地跨进程 MCP 发现与调用，不覆盖目标 Linux/Docker/Kubernetes
环境或审批策略。

本次结果证明官方打包运行时可以由 Python SDK 在本机启动、执行一次真实模型 prompt 并关闭。它不证明会话恢复、审批、MCP 或 Docker/Kubernetes 容器内运行已经通过。当前使用的是 `0.2.1rc2` 开发预览版本，项目实施时必须重新确认是否采用该版本，并在目标 Linux 节点验证。

可重复执行的 Demo 位于 `harness-engine/src/harness_engine/deepseek_demo.py`，使用说明见 [DeepSeek Harness 最小验证 Demo](deepseek-harness-demo.md)。

## macOS 隔离员工上下文与 Session 契约（2026-11-19）

为 Harness Worker 的上下文注入契约创建了隔离虚拟环境，安装项目固定的 deepseek-harness-sdk==0.2.2rc2、匹配 runtime wheel、mcp==2.3.0 和测试依赖。使用临时 Harness home、临时 Session 和本机 OpenAI-compatible 测试端点；没有读取或使用本机 .artifacts/local-openai.json，没有真实 API Key，也没有向外部模型发送请求。

新增可重复测试 harness-engine/tests/test_deepseek_sdk_contract.py，通过真实 SDK/runtime 进程验证：

- Employee system prompt、Skill 指令和 Skill 文件内容进入模型请求的 system message。
- 官方 MCP Stdio client 启动本机 echo MCP server，并在模型请求中暴露其真实 JSON Schema；模型工具名为 mcp__python-tools__echo。
- SDK 返回的 Session ID 可以从首个 Harness 进程关闭后恢复，并在同一隔离 home 中继续第二轮请求。
- 本机假模型服务捕获到的只有固定合成提示，不包含员工或租户数据。

复验命令：

~~~bash
PYTHONPATH=harness-engine/src /path/to/sdk-check-venv/bin/python -m pytest harness-engine/tests/test_deepseek_sdk_contract.py -q
PYTHONPATH=harness-engine/src python3 -m pytest harness-engine/tests/test_deepseek_engine.py -q
~~~

本次结果：SDK 契约测试 1 passed；DeepSeek Engine 适配测试 7 passed。该验证覆盖固定版本 SDK 的 prompt/schema/Session 接口和本机 Stdio 子进程，不覆盖 StaffDeck DolphinScheduler MCP 的实际 command/args/cwd/env，也不覆盖 Worker 容器内的安全隔离或真实业务写操作。

## 当前容器环境检查

本机为 Windows + WSL2；Docker CLI 已安装，但 Docker Desktop Linux Engine 当前无法连接。因此本机没有完成 Docker 容器或 Kubernetes Pod 中的 Harness、MCP、恢复和产物链路验证。该项应在 Docker Engine 或 Kubernetes 节点上执行。microVM 后端不在当前阶段范围内。

## 2026-11-19 RunSnapshot 类型化传递进展

`harness-engine.core.EngineRequest` 已加入 `EmployeeContext`、带内容摘要的 `SkillFileContext` 和仅允许 READ/PREVIEW 的 `EngineToolDefinition`；`FakeEngine` 的 Trace 事件仅记录员工标识/版本与允许工具名，不记录系统指令、Skill 正文或 JSON Schema。Backend 的 `RuntimeRequest` 与 `AgentResolver` 按固定 RunSnapshot 恢复员工/Skill/Tool/MCP Profile 版本；`HarnessRuntimeAdapter` 为每个 Run 调用请求级 Engine factory 并在结束后关闭该 Engine。

DeepSeek SDK 仍把 MCP/tools 和 system prompt 放在 Harness 初始化配置中；每 Run 独立 Harness 将员工指令和工具配置写入唯一临时 overlay，并使用 Session 稳定 Harness Home 保留历史。Home 引用由平台/租户/Session ID 的 SHA-256 生成，仅映射到 `AGENT_PLATFORM_DATA_ROOT/harness-sessions/<digest>`。MySQL V007 lease 串行同一 Session 的 Run；结果未知时 Session 变为 INTERRUPTED 且 Home 被 QUARANTINED，不允许旧进程可能仍在写入时自动复用。

Worker 在启动 Harness 前从固定 MCP Profile 解析 command/cwd allowlist 与 SecretRef；官方 MCP Stdio proxy 校验 Profile 的完整目录摘要和当前 ToolVersion Schema 摘要，只向模型发布当前 Run 的 READ/PREVIEW 工具并在调用时二次拒绝未授权工具。Secret 明文不写入 overlay、临时 JSON、Trace 或日志，并在 Harness close 后清除进程配置副本。

验证命令与结果：

~~~bash
cd harness-engine
PYTHONPATH=src /tmp/agentdemo-harness-employee-sdk/bin/python -m pytest -q -rs
cd ../backend
PYTHONPATH=../harness-engine/src /tmp/agentdemo-harness-employee-sdk/bin/python -m pytest \
  tests/test_runtime_employee_context.py tests/test_runtime_adapter.py \
  tests/test_harness_runtime_adapter.py tests/test_control_plane_service.py \
  tests/test_claimed_run_execution.py -q
~~~

Harness Engine 全量：178 passed，3 skipped；Backend 类型化上下文、Session lease、RuntimeAdapter 和 app runtime factory 聚焦测试：39 passed。真实 DeepSeek SDK/runtime 与 Stdio proxy 使用本机假 OpenAI-compatible 模型端点和本机假 MCP 服务验证：连续 Run 共用 Session Home，但员工 system prompt 与 MCP tools/list 各自隔离；第二个 Run 不会看到第一个 Run 的 echo 工具。额外 MySQL V007 disposable 集成测试验证 claim/heartbeat/Session 串行、跨租户同名 Session、UNKNOWN 隔离和迁移 rollback。

该测试不覆盖真实 StaffDeck DolphinScheduler Profile、外部凭据、DolphinScheduler OFFLINE 创建、容器内子进程组关闭或非生产环境 UNKNOWN 对账；这些仍由 OpenSpec 1.1/5.3/7.2 验收。

## 当前 macOS 本地中转站复验（2026-11-14）

在临时 Python 3.13 虚拟环境中安装 `deepseek-harness-sdk==0.2.2rc2`、匹配的 `deepseek-harness-runtime-bin==0.2.2rc2` 和项目 `cryptography` 依赖，使用 `harness-engine/.artifacts/local-openai.json` 的本地中转站执行 startup-only 和真实 prompt。

结果：startup、model call、`final_response=sdk-0.2.2-ok`、`finish_reason=completed` 和 close 均通过；实时 delta/resume 仍未验证。将同一 SDK/runtime 安装到 Docker Linux 镜像后，arm64 与 amd64 两种容器均因 `node-addon-require-builtin` 原生 binding 缺失无法启动，真实容器 Harness/MCP 链路仍未通过。

## 未通过时的处理

若官方 SDK 的真实公开接口与设计不一致，先修改适配器设计和本记录，不实现自建 JSON-RPC。若本地运行时可用但不支持目标沙箱通信，则保持沙箱调度器拒绝状态，先完成 worker 协议验证。

## 真实验证命令模板

```powershell
py -3.11 -m venv .venv-sdk-check
.\.venv-sdk-check\Scripts\python.exe -m pip install --upgrade pip
.\.venv-sdk-check\Scripts\python.exe -m pip install deepseek-harness-sdk==<已验证版本>
```

上述命令只提供验证模板；版本号必须来自实际发布包和匹配 runtime，不能填入未经确认的版本。
