# DeepSeek Harness 最小验证 Demo

这个 Demo 用独立的临时 Harness home 验证官方 Python SDK 的最小链路：

1. 导入 `deepseek_harness.DeepSeekHarness`。
2. 使用显式 `DSH_HOME` 和工作目录启动 runtime。
3. 发送一个 prompt，读取 `final_response` 和 `finish_reason`。
4. 关闭 runtime，并输出 JSON 验证报告。

它直接使用官方 SDK，不接入 `harness_engine.core`，也不会把 SDK 类型暴露给业务层。

## 安装验证依赖

在 `harness-engine/` 下创建独立环境，并安装当前项目记录的 SDK/runtime 版本：

```powershell
py -3.12 -m venv .venv-sdk-check
.\.venv-sdk-check\Scripts\python.exe -m pip install --upgrade pip
.\.venv-sdk-check\Scripts\python.exe -m pip install -r requirements-deepseek-check.txt
```

如果官方发布了新的 SDK/runtime 配对版本，先更新该文件和 `docs/sdk-validation.md`，再重新执行验证。

## 只验证启动和关闭

不需要 API Key：

```powershell
$env:PYTHONPATH = "src"
.\.venv-sdk-check\Scripts\python.exe -m harness_engine.deepseek_demo --startup-only
```

默认会创建并清理临时 Harness home。若要保留 session、profile 和 runtime 日志，传入明确目录：

```powershell
$env:PYTHONPATH = "src"
.\.venv-sdk-check\Scripts\python.exe -m harness_engine.deepseek_demo `
  --startup-only `
  --dsh-home "$pwd\.artifacts\dsh-home" `
  --report "$pwd\.artifacts\startup-report.json"
```

## 验证一次真实 prompt

先设置凭据，再发送一个短 prompt。脚本不会把 API Key 写入报告：

```powershell
$env:DEEPSEEK_API_KEY = "sk-..."
$env:PYTHONPATH = "src"
.\.venv-sdk-check\Scripts\python.exe -m harness_engine.deepseek_demo `
  --dsh-home "$pwd\.artifacts\dsh-home" `
  --report "$pwd\.artifacts\prompt-report.json" `
  "Reply with exactly: deepseek-harness-ok"
```

通过条件是报告中的 `status` 为 `passed`，并同时看到：

- `startup: passed`
- `model_call: passed`
- `finish_reason: completed`
- `close: passed`

缺少 SDK 时脚本返回退出码 `2` 并给出安装命令；runtime 启动或模型调用失败时返回退出码 `1`，报告会保留失败阶段和错误类型。

## 使用本地 OpenAI 兼容代理

Demo 会自动读取 `harness-engine/.artifacts/local-openai.json`。该文件已配置为：

- provider：`openai`
- model：`gpt-6-luna`
- base URL：`http://localhost:50811/v1`
- protocol：`openai-completions`

这个文件已被 `.gitignore` 忽略，适合放本机代理凭据；当前模型为 `gpt-6-luna`。启动配置中保持工作目录为
`harness-engine/`，然后直接运行：

```powershell
$env:PYTHONPATH = "src"
.\.venv-sdk-check\Scripts\python.exe -m harness_engine.deepseek_demo `
  "Reply with exactly: local-openai-ok"
```

脚本会临时生成一个 `llm-pi-ai` profile patch，并把本地代理的凭据通过子进程环境传给 Harness；API Key 不会写入 JSON 报告。

## 通过引擎协议运行

P1 已将同一 SDK 调用接入 `DeepSeekHarnessEngine`，CLI 可以直接选择
`--engine deepseek`。它会把 SDK 的 `final_response`、`finish_reason` 和完成时间转换为
`EngineResult`，并在结束时关闭 runtime：

```powershell
.\.venv-sdk-check\Scripts\harness.exe `
  --engine deepseek `
  --max-tokens 64 `
  "Reply with exactly: cli-deepseek-ok"
```

CLI 的工作目录保持为 `harness-engine/` 时，会自动读取同一个
`.artifacts/local-openai.json` 配置。

## 连接本地 Python MCP 工具

先在运行 Harness 的同一个虚拟环境中安装官方 MCP Python 依赖：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[mcp]"
```

DeepSeek profile 还需要安装与 `dsh --version` 对齐的官方 MCP client 插件，
并让它写入选定 profile：

```powershell
$env:DSH_HOME = "$pwd\.artifacts\dsh-home"
& .\.venv-sdk-check\Scripts\dsh.exe --profile sdk-minimal --dump-default-config | Out-Null
& .\.venv-sdk-check\Scripts\dsh.exe plugin --profile sdk-minimal add `
  "@deepseek-ai/dsh-mcp-client@<与 dsh --version 一致的版本>"
```

启动引擎时重复传入 `--mcp-tool` 即可启用白名单工具。适配器会在
`DSH_HOME/mcp-tools.patch.yml` 中生成官方 stdio 配置，命令使用当前 Python
解释器的绝对路径：

```powershell
$env:PYTHONPATH = "src"
.\.venv-sdk-check\Scripts\python.exe -m harness_engine.cli `
  --engine deepseek `
  --dsh-home "$pwd\.artifacts\dsh-home" `
  --mcp-tool echo `
  "Use the echo tool with text 'mcp-ok', then report its result."
```

验证 `mcp-tools.patch.yml` 中包含 `@deepseek-ai/dsh-mcp-client`、
`transport: stdio` 和 `harness_engine.mcp_server`，并在 Harness 日志中看到
`mcp__python-tools__echo` 被发现。若 profile 未安装该插件，SDK 会在启动时明确报
插件加载错误，不会回退到自定义协议。
