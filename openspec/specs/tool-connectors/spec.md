# tool-connectors Specification

## Purpose

为平台统一管理 API、MCP 和 A2A 外部能力，并在不同协议之间提供一致的发现、授权、调用、审批和审计行为。

## Requirements

### Requirement: Tool connector declares a protocol and contract
系统 SHALL 为工具保存稳定名称、版本、协议类型、说明、输入输出 Schema、认证引用、超时、重试和风险策略。

#### Scenario: Register API tool
- **WHEN** 管理员注册带有效 Schema 和认证引用的 API 工具
- **THEN** 系统创建可供 Skill 和数字员工版本引用的工具版本

### Requirement: Tool access is explicitly allowlisted
系统 SHALL 只加载和调用当前部署策略、Skill 版本及租户授权共同允许的工具。

#### Scenario: Tool is not allowlisted
- **WHEN** 模型请求一个未被部署策略允许的工具
- **THEN** 调用被拒绝且不向外部服务发送请求

### Requirement: Tool invocation has a normalized lifecycle
系统 SHALL 为 API、MCP 和 A2A 调用提供统一的请求、运行、成功、失败、取消和未知状态。

#### Scenario: A2A asynchronous task
- **WHEN** A2A 工具返回异步任务引用
- **THEN** 系统记录远程任务状态并允许通过事件或轮询完成对应 Tool Call

### Requirement: Tool input and output are controlled
系统 SHALL 在调用前校验输入 Schema，并在调用后记录结果状态、耗时、错误类别和受控内容引用。

#### Scenario: Invalid tool input
- **WHEN** 输入不满足工具 Schema
- **THEN** 工具 handler 或远端服务不会被调用，Run 收到可追踪的 validation error

### Requirement: High risk tools require approval
系统 SHALL 根据工具风险策略在执行前请求审批，并在没有有效审批时拒绝高风险调用。

#### Scenario: Approval rejected
- **WHEN** 用户拒绝重启服务或修改生产数据等高风险调用
- **THEN** 工具不执行，Run 记录拒绝结果并继续或结束于明确状态
