# 官方 Harness SDK 最小验证记录

状态：已完成本地最小运行时、一次真实模型 prompt 和一次本地 MCP 工具调用验证；会话恢复、审批和生产沙箱链路仍待验证。日期：2026-11-10。

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

## 当前容器环境检查

本机为 Windows + WSL2；Docker CLI 已安装，但 Docker Desktop Linux Engine 当前无法连接。因此本机没有完成 Docker 容器或 Kubernetes Pod 中的 Harness、MCP、恢复和产物链路验证。该项应在 Docker Engine 或 Kubernetes 节点上执行。microVM 后端不在当前阶段范围内。

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
