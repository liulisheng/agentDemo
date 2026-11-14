# Spec Delta

## Purpose

为平台提供可替换的 Agent 执行引擎边界，使 DeepSeek Harness、Codex app-server、Codex exec、其他 CLI 和 Fake 引擎能够由平台配置选择并共享 Session、Run、事件、取消和错误语义。

## ADDED Requirements

### Requirement: Engine exposes a common lifecycle
系统 SHALL 为每个执行引擎提供启动、创建 Session、恢复 Session、执行 Run、取消和关闭的统一语义。

#### Scenario: Start and run engine
- **WHEN** Worker 根据已解析的 ExecutionProfile 启动引擎并提交 Run
- **THEN** 平台创建可关联的 Session 和 Run，并接收统一结果或事件

### Requirement: Engine selection follows profile policy
系统 SHALL 根据平台、租户、用户范围的 ExecutionProfile 选择引擎，优先级为用户、租户、平台、系统默认。

#### Scenario: User override
- **WHEN** 用户级配置存在且处于 ACTIVE 状态
- **THEN** 新 Run 使用用户级引擎配置并记录解析来源

### Requirement: Unsupported capability is explicit
系统 SHALL 在引擎不支持恢复、取消、流式、审批、MCP 或工作区等被请求能力时返回明确能力错误，不静默降级。

#### Scenario: CLI lacks resume
- **WHEN** 用户请求恢复一个 Generic CLI 引擎无法恢复的 Session
- **THEN** 系统返回 unsupported capability，并保留原 Session 状态

### Requirement: Engine process is isolated from platform API
系统 SHALL 通过受控 Worker 和 Sandbox 启动引擎进程，平台 API 不直接执行未经策略授权的宿主命令。

#### Scenario: Malformed CLI arguments
- **WHEN** EngineProfile 包含非法命令参数或未授权工作目录
- **THEN** Worker 拒绝启动且不执行宿主进程

### Requirement: Run result is engine-neutral
系统 SHALL 将不同引擎的结果转换为统一的 RunResult、EngineEvent、ToolCall 和 Usage 语义，并保留原始供应商请求引用。

#### Scenario: Codex event conversion
- **WHEN** Codex app-server 发送流式 Agent message 或 tool event
- **THEN** 平台将其转换为统一事件并关联 session_id、run_id、trace_id

### Requirement: Runtime-owned state remains opaque
系统 SHALL 将 Runtime 的 Session、Thread、内部上下文、私有记忆、工作区和结果状态作为 opaque 引用管理，不要求不同引擎共享或迁移其内部格式。

#### Scenario: Switch engine for a new run
- **WHEN** 用户把部署从 DeepSeek Harness 切换到 Codex
- **THEN** 新 Run 创建新的 Runtime Session，原引擎引用仍保留在历史审计中

### Requirement: Adapter does not reimplement the runtime loop
系统 SHALL 通过 Runtime Adapter 调用供应商的 Agent Loop、上下文压缩、内部工具循环和环境操作，并只在平台边界处理业务策略、权限、事件和用量。

#### Scenario: Runtime compacts context
- **WHEN** Runtime 在内部执行上下文压缩
- **THEN** 平台接收标准化事件或 Usage，不重新执行另一套压缩流程
