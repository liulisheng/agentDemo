# Spec Delta

## ADDED Requirements

### Requirement: MCP Stdio server profiles are versioned and constrained
系统 SHALL 以租户范围不可变版本保存 MCP Stdio 启动配置、启用工具目录、超时和 schema 摘要；秘密仅以 SecretRef 引用，进程使用参数数组和授权工作目录启动。

#### Scenario: Worker starts a published Stdio profile
- **WHEN** Worker 为 Run 加载 ACTIVE MCP Profile 版本
- **THEN** Worker 使用固定 command、args、working directory 和 SecretRef 启动本机 Stdio 子进程，且不创建网络监听端点

#### Scenario: Discovered tool catalog differs from the profile
- **WHEN** MCP tools/list 返回的工具名称或输入 schema 摘要与已发布 Profile 不一致
- **THEN** 系统拒绝工具调用并要求发布经校验的新 Profile/ToolVersion

### Requirement: MCP tool exposure is limited by the complete authorization set
系统 SHALL 只向模型暴露员工绑定、Skill 精确版本声明、租户策略和 MCP Profile 启用目录共同允许的 READ/PREVIEW 工具；WRITE 工具只能由一次性批准动作执行。

#### Scenario: Model requests a disallowed MCP tool
- **WHEN** 模型请求的工具不在当前 Run 的有效 allowlist 中
- **THEN** 工具网关在发送 MCP 请求前拒绝调用并记录稳定错误类别

#### Scenario: Model requests a write operation
- **WHEN** 当前任务需要创建、发布、启动或调度等 WRITE 操作
- **THEN** 模型只能获得该操作的预览结果，不能直接调用对应 WRITE 工具

### Requirement: Approved MCP writes are bound to one exact preview
系统 SHALL 将一次审批绑定到单个 Run、ToolInvocation、ToolVersion、MCP Profile 版本、参数摘要和预览摘要；仅提交该 Run 的同租户用户可以批准，审批拒绝、过期、已消费或摘要不一致时不得执行写工具。

#### Scenario: User approves the displayed preview
- **WHEN** 提交该 Run 的同租户用户确认仍有效的预览摘要
- **THEN** 系统创建可由 Worker 消费的一次性批准动作，且只允许执行该审批绑定的工具和参数

#### Scenario: A different user attempts to approve
- **WHEN** 不是该 Run 提交者的用户尝试确认审批
- **THEN** 系统拒绝该操作且不排队写工具调用

#### Scenario: Write outcome is unknown
- **WHEN** 写请求可能已到达远端但 Worker 未收到可信结果
- **THEN** 工具调用标记为 UNKNOWN 且不自动重试
