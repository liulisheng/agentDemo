# 可插拔 Python Harness 工程设计

状态：基础 SDK、P2 生命周期、P3 工具插件、P4 控制面持久化和可选的非流式对话正文持久化已实现；流式恢复、清理 worker、生产对象存储/KMS 与服务器沙箱仍处于实现/验证计划阶段。更新日期：2026-11-13。

## 1. 目标与范围

后续服务器目标已明确为多租户不可信任务，见 [沙箱设计](sandbox-design.md)。下文描述本地基础版，不能直接用于该生产场景；服务器阶段的隔离、凭据和元数据要求以沙箱设计为准。

构建面向通用工具型 Agent 的 Python SDK 与 CLI，以官方 DeepSeek Harness 为默认底座。业务依赖统一引擎接口，替换整个 Harness 不要求修改业务逻辑；模型提供方与引擎分别配置。

第一版支持本地托管运行时、事件回调、Python 工具插件、持久会话恢复，以及用于离线验证的 Fake 引擎。暂不包含 HTTP 服务、Web UI、多租户、分布式调度、插件热加载或跨引擎会话迁移。

## 2. 技术选型与目录职责

采用 Python 3.11+、src 布局、uv 依赖管理、Pydantic 配置校验、pytest 测试、Ruff 检查与 mypy 类型检查。具体兼容版本在实施验证后锁定，不使用浮动版本作为交付依据。

| 模块 | 职责 | 依赖约束 |
| --- | --- | --- |
| core | 引擎协议、请求、结果、事件、能力及异常 | 不导入具体引擎 SDK |
| application | 执行编排、会话恢复、取消和生命周期 | 面向核心协议 |
| engines | DeepSeek/Fake 适配器及注册工厂 | 将原生对象转换为核心类型 |
| tools | 工具注册、校验、本地 MCP 服务 | 不包含业务专用逻辑 |
| storage | MySQL/InnoDB 持久化与仓储 | 保存控制面状态；不复制引擎私有历史 |
| config | 配置读取与校验 | 凭据不落盘 |
| cli | 参数解析、交互审批与结果展示 | 调用应用服务，不直接调用底座 |

依赖方向：CLI → 应用服务 → 核心协议；具体引擎实现核心协议，由组合入口装配。保持小模块、明确命名和类型注解，不引入复杂依赖注入框架。

## 3. 引擎抽象

公共协议 HarnessEngine 的设计接口如下，具体签名在实施时以这些语义为准：

| 接口 | 语义 |
| --- | --- |
| start() / close() | 启动及释放本实例拥有的运行时资源；两者幂等 |
| create_session() | 创建独立会话并返回 `SessionRef` |
| resume_session(ref) | 恢复既有会话；失败不得偷偷新建 |
| run(request, on_event=None) | 同步执行并返回 `RunResult`，可通过引擎无关事件回调报告事件 |
| cancel(session_id) | 请求取消活动任务；结果区分已确认、进行中和未知 |
| capabilities | 声明事件、恢复、取消和工具接入支持情况 |

第一版只公开同步接口，内部按需用线程处理事件与审批，避免同时维护同步、异步两套 API。

公共类型：RunRequest 携带会话和输入；RunResult 携带最终文本、终止原因与运行标识；SessionRef 携带业务及引擎会话标识；HarnessEvent 携带事件类型、会话及运行标识和载荷；EngineCapabilities 描述能力。原生扩展数据应明确隔离，不能成为业务必需字段。

统一异常至少区分配置错误、能力不支持、会话恢复失败、运行时故障、协议错误与执行超时。保留原始异常链，但用户可见错误与日志必须脱敏。不支持的能力明确拒绝，不能静默降级。

引擎通过 Python entry points 分组 agent_harness.engines 注册。配置选择引擎，启动时检查重复名称、加载错误和配置兼容性。第一版只交付 DeepSeek 生产适配器和 Fake 测试适配器。

## 4. DeepSeek 接入

封装官方 deepseek-harness-sdk，不自行实现 Agent 循环、上下文压缩或 JSON-RPC 通信。官方文档说明 SDK 使用匹配版本的打包运行时，普通 SDK 执行不需要系统 Node.js；外部插件管理的依赖另行检查。

- 显式分配独立 Harness home 和工作目录，避免读取或修改用户全局配置。
- engine、provider、model 分开配置。DeepSeek SDK 类型仅允许出现在适配器内部。
- 使用官方运行、通知及恢复接口，并通过上下文管理器释放进程。
- 区分协议响应超时和整个任务执行期限；等待超时不会自动代表底座已经停止。
- 执行超时应请求取消并等待有界清理，无法确认停止时报告状态不确定。
- 崩溃、断连和结果不确定时不得自动重放任务，以免重复产生副作用。
- 固定 SDK 与 runtime 的兼容版本；发布前验证目标 Windows x64 环境。

## 5. Python 工具插件

采用本地 stdio MCP 服务桥接 Python 业务工具。底座作为 MCP 客户端调用工具服务，工具服务负责发现、参数校验和执行。使用官方 MCP Python 包实现协议，不手写协议解析器。

工具定义包含名称、说明、输入 JSON Schema 和执行函数。通过 agent_harness.tools entry points 发现，仅加载配置白名单中的插件。重复名称启动失败；非法参数不进入工具函数；执行异常转换为不泄露敏感信息的工具错误。

工具服务是独立进程，stdio 的 stdout 仅用于协议，日志写 stderr。服务命令使用当前 Python 环境的绝对可执行路径，不拼接 shell 命令。DeepSeek 适配器在启用 `mcp_tools` 时生成 profile patch，插入官方 `@deepseek-ai/dsh-mcp-client` 的 stdio 配置；该 npm 插件仍需按锁定的 Harness/runtime 版本安装到目标 profile。

当前实现将工具定义与 MCP 传输分层：`harness_engine.tools` 负责显式白名单、entry point、输入校验和 handler 错误边界；`harness_engine.mcp_server` 通过官方 MCP Python SDK 的低层 `Server` 暴露精确 JSON Schema。未安装 MCP SDK 时，服务启动明确失败，不回退到自定义协议。

第一版提供无副作用示例工具。工具注册不是沙箱：安装及启用的 Python 插件属于受信代码。未来引擎不支持 MCP 时必须实现等价桥接，否则拒绝工具功能。

## 6. 持久化、会话与执行状态

MySQL/InnoDB 是当前暂定的关系型持久化数据库，开发、测试和服务器部署使用同一数据库类型。用户中心参考 `dadi` 的平台、租户、用户、组织、岗位、用户组和服务端会话模型。数据库保存用户身份映射、授权关系、业务会话索引、引擎及原生会话标识、运行状态、租约、审计元数据和幂等键。租户数据表使用 platform_id + tenant_id，并以复合唯一约束和外键保护数据关联，以索引支持租户过滤查询；这些约束不会自动阻止漏写 tenant_id 条件造成的读取越权。

Harness 原生历史和工作区文件由运行时及隔离存储负责，不把完整事件流或大文件塞进业务数据库。生产环境将这些数据放入按租户/会话隔离、加密且有备份策略的对象存储或持久卷；MySQL 只保存经过授权的对象引用和版本标识。恢复时在新的隔离执行环境中挂载对应数据并调用原生恢复接口，不重放历史 prompt。缺失、损坏或不兼容的历史必须明确报错。

应用可读取的对话 transcript 与 Harness 原生恢复历史是两份不同数据。新增的对话持久化方案以 MySQL 保存 `messages`/`message_chunks` 元数据和不透明引用，以加密对象存储保存用户输入、助手可见回复和流式内容分段。非流式回复存为一份完整对象；流式输出按 32 KiB 或 2 秒（先到者）写不可变 checkpoint，客户端断线后按 `last_chunk_sequence` 持续读取同一 MySQL durable log。worker/进程退出时只恢复最后已提交的部分输出并标记 `PARTIAL`/`UNKNOWN`，不自动重发模型请求。会话级内容和审计元数据默认保留至显式删除。

执行审计记录工具名称、调用状态、审批结果、错误码、时长及资源引用等可核验元数据。当前消息服务只保存 `EngineResult.text` 作为助手可见最终文本，不保存独立的原始内部推理/隐藏 reasoning；工具参数、凭据、完整工具响应及 prompt 不写入审计 payload。给用户看的简短解释若产品需要，应作为可见 assistant 消息保存，不冒充内部推理轨迹。V002、只读 doctor、Python 消息仓储和非流式服务接入已实现并通过本地/一次性 MySQL 验证；Harness 原生历史的推理保留行为仍需单独核实，详见[对话正文持久化设计](superpowers/specs/2026-11-13-conversation-content-persistence-design.md)。

租户隔离由应用层负责：认证中间件从已验证身份取得 platform_id、tenant_id、user_id 和角色，仓储方法必须接收完整 TenantContext，所有读写都显式带 platform_id + tenant_id 条件。数据库用包含平台和租户的复合唯一约束、复合外键和索引减少关联错误并支持租户过滤查询。MySQL 方案不依赖数据库行级安全策略，因此跨租户越权测试是必需验收项；请求体自报的租户或用户身份不能作为授权依据。

每个会话只允许一个活动任务。使用 InnoDB 事务、行锁或租约/fencing token协调多实例并发，不使用进程内锁承担分布式正确性。队列式领取可使用 SKIP LOCKED，但不用于普通一致性查询。运行中断后标记为不确定/中断状态，由用户或策略决定是否恢复，不能自动重放有副作用任务。

不恢复 shell、浏览器等外部进程，也不承诺跨引擎迁移。API Key 等凭据不写入数据库、会话元数据或日志。

## 7. 配置、CLI 与安全

普通配置采用 TOML，优先级：CLI > 环境变量 > 配置文件 > 默认值。API Key 只从环境变量或 SDK 参数注入，不写入配置示例、MySQL、命令参数或日志。

计划命令：

- harness doctor：检查依赖、运行时、配置和目录访问能力。
- harness run "任务"：新建会话并执行，显示会话 ID。
- harness run "继续" --session <id>：恢复指定会话。
- harness sessions list：查询本地索引。
- harness engines list / harness tools list：列出可用插件及启用状态。

默认不暴露任意 shell 或文件写入业务工具。底座原生高风险能力必须配置审批，交互 CLI 可确认；非交互调用没有审批处理器则拒绝。默认 profile 的工具与权限不能仅凭名称假定安全，必须核验有效配置并测试拒绝路径。

本地进程及审批策略不等于操作系统沙箱。日志默认只包含运行标识、状态、耗时和错误类别，不记录完整输入、工具参数或凭据。

## 8. 参考与关联文档

以下为本次方案核实所使用的上游文档，master 可能变化，实施时应绑定具体版本：

- [官方 Python SDK](https://github.com/deepseek-ai/deepseek-harness/blob/master/python/sdk/README.md)
- [官方打包运行时](https://github.com/deepseek-ai/deepseek-harness/blob/master/python/sdk-runtime/README.md)
- [官方 MCP 模块](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/mcp/README.md)
- [实施计划](implementation-plan.md)
- [技术决策](decisions.md)
