# P3 技能与工具插件设计

## 意图

为 Harness 增加受信 Python 工具插件边界，使工具可以通过统一定义注册、按显式白名单发现、在执行前校验 JSON 输入，并由独立的本地 MCP 服务向 Harness 暴露。P2 的生命周期协议继续负责引擎和运行状态；工具插件不把 SDK 类型带进核心模块。

## 目标

- 定义 `ToolDefinition`、工具注册表和统一工具异常。
- 使用 `agent_harness.tools` entry point 发现插件，并强制显式 allowlist。
- 在调用 handler 前校验 JSON Schema；非法参数不得进入业务函数。
- 将工具异常转换为不泄露原始敏感信息的错误。
- 提供无副作用 `echo` 示例工具。
- 提供独立 MCP 服务适配器：安装官方 `mcp` Python 包后使用 stdio；未安装时返回明确的依赖错误。
- 让 MCP 服务具有 start/close 幂等生命周期，并能列出和调用已启用工具。
- 为后续 Skill 和子智能体保留工具元数据、运行元数据和事件扩展空间。

## 非目标

- 不把工具插件当作操作系统沙箱；插件代码属于受信安装代码。
- 不实现 HTTP 服务、热加载、任意目录扫描或隐式启用插件。
- 不自定义 MCP JSON-RPC 协议；协议传输交给官方 MCP SDK。
- DeepSeek profile patch 只使用已核实的 `@deepseek-ai/dsh-mcp-client` stdio 配置；目标 profile 仍需安装与 runtime 对齐版本的 npm 插件。

## 约束

- Python 3.12+，核心工具注册和校验不依赖第三方包。
- MCP stdio 适配锁定官方 `mcp==2.3.0`；离线 registry 测试不要求安装它。
- 工具名称在一个 registry 内唯一且不能为空。
- allowlist 为空时不发现任何 entry point；未授权工具不可调用。
- schema 至少支持 object、array、string、integer、number、boolean、null、required、properties、items、enum 和 additionalProperties。
- stdio stdout 只交给官方 MCP SDK；应用日志使用 stderr。
- 工具进程使用当前 Python 解释器的绝对路径，不拼接 shell 命令。

## 方案

1. **分层 registry + 官方 MCP 适配（选定）**：registry 负责定义、发现、allowlist、schema 和 handler 执行；MCP 适配器只负责把 registry 接到官方 SDK。优点是离线测试不需要 MCP 包，协议边界清楚；代价是需要额外的可选依赖安装步骤。
2. **直接为每个工具手写 MCP 服务**：实现快，但重复协议和生命周期代码，违反官方 SDK 优先原则，因此不选。
3. **把工具直接塞进引擎适配器**：与 DeepSeek 耦合，无法给 Fake 或未来引擎复用，也无法独立测试，因此不选。

## 选定设计

`ToolDefinition` 携带名称、说明、输入 schema、handler 和可选插件标识。`ToolRegistry.discover(allowlist)` 读取 `agent_harness.tools` entry points，只加载名称在 allowlist 内的定义；重复名称、加载错误和定义类型错误都失败。`invoke` 先校验参数，再执行 handler，原始异常保留为 cause 但对外只暴露安全错误。

`McpToolServer` 持有 registry，提供 `start()`、`close()`、`list_tools()` 和 `call_tool()`。`start()` 检查官方 `mcp` 包并建立低层 `Server` stdio 服务；没有依赖时抛出 `McpUnavailableError`。服务本身不扫描插件、不修改 allowlist，也不接收宿主路径或命令参数。

启用 DeepSeek 工具时，适配器把当前 Python 解释器、`-m harness_engine.mcp_server` 和显式工具白名单写入 profile patch，并通过官方 `@deepseek-ai/dsh-mcp-client` 连接。插件安装由目标 profile 管理，不在 Python 包中隐式下载 npm 依赖。

## 验收

- 未授权 entry point 不会加载；重复名称和加载失败有明确异常。
- 缺少 required、类型错误、额外字段和 enum 不匹配均在 handler 前失败。
- 成功调用返回结果；handler 异常不泄露原始错误文本。
- `McpToolServer` 的列表、调用、启动和关闭生命周期有离线测试；真实 MCP stdio 和一次 DeepSeek Harness 工具调用在显式环境中验证。
