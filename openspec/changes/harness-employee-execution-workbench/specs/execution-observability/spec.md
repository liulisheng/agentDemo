# Spec Delta

## ADDED Requirements

### Requirement: MCP previews and approvals are correlated in the Run timeline
系统 SHALL 将 MCP 发现/调用、预览摘要、审批状态和批准动作结果关联到同一 Session、Run、Trace 与 ToolInvocation，并记录可审计的状态、耗时、错误类别和内容引用。

#### Scenario: Run pauses for approval
- **WHEN** Agent 生成受审批策略约束的写操作预览
- **THEN** Trace 显示预览 Tool Span、Approval 引用和 AWAITING_APPROVAL Run 状态，且普通索引不包含完整参数或预览正文

#### Scenario: Approved action completes
- **WHEN** Worker 消费一次性批准动作
- **THEN** Trace 追加带 Tool/Profile 版本、审批引用和结果状态的 Tool Span，并更新关联 Run

#### Scenario: MCP write result is uncertain
- **WHEN** 远端写操作可能已执行但没有可信响应
- **THEN** Trace 和 Run 显示 UNKNOWN，保存只读对账所需的受控引用，不暴露密钥或原始 payload
