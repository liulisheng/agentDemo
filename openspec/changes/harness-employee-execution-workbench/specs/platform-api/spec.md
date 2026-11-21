# Spec Delta

## ADDED Requirements

### Requirement: Conversation workbench APIs are tenant scoped and paginated
系统 SHALL 提供 Conversation 列表和消息读取 API，按认证上下文、员工绑定及游标分页范围返回工作台所需的会话摘要。

#### Scenario: List employee conversations
- **WHEN** 已认证用户按 employee_id 查询 Conversation 列表
- **THEN** API 只返回当前租户中用户有权访问且绑定该员工的项目，并提供稳定 next_cursor

#### Scenario: Read another tenant conversation
- **WHEN** 用户查询其他租户或未授权的 Conversation
- **THEN** API 返回统一 not found 结果且不泄露资源存在性

### Requirement: Conversation turns submit asynchronously
系统 SHALL 提供 turn 提交 API，将用户消息、Conversation/Session、幂等键和 Run 创建关联，并对客户端立即返回可查询的异步状态。

#### Scenario: Submit a valid turn
- **WHEN** 已认证用户提交有效文本和幂等键
- **THEN** API 保存消息并返回 HTTP 202、message_id、run_id 和 QUEUED 状态，不等待 Runtime 完成

#### Scenario: Retry a turn with changed content
- **WHEN** 客户端复用幂等键但消息摘要不同
- **THEN** API 返回稳定的 IDEMPOTENCY_CONFLICT 且不创建第二个 Run

### Requirement: Approval APIs authorize one exact preview action
系统 SHALL 提供审批查询、确认和拒绝 API，并在服务端校验租户范围、Run 提交者身份、有效期、当前预览摘要和幂等键。

#### Scenario: Approve a current preview
- **WHEN** 同租户获批用户确认未过期且摘要匹配的审批
- **THEN** API 原子记录批准人和时间并使唯一绑定动作可由 Worker 领取

#### Scenario: Approve a stale or mismatched preview
- **WHEN** 预览已过期或客户端提交的摘要不匹配
- **THEN** API 拒绝批准且不排队任何写操作

#### Scenario: Another tenant user tries to approve
- **WHEN** 同租户中非 Run 提交者调用确认 API
- **THEN** API 返回稳定的授权拒绝且不排队任何写操作

### Requirement: Run cancellation is authorized by the Run submitter
系统 SHALL 仅允许同租户 Run 提交者请求取消工作台 Run；授权身份必须来自认证上下文。

#### Scenario: Submitter cancels a queued or running Run
- **WHEN** Run 提交者请求取消属于自己的 Run
- **THEN** QUEUED Run 直接返回 CANCELLED，已领取 Run 返回 CANCELLING 并由 Worker 处理 Runtime 取消

#### Scenario: Another user cancels a Run
- **WHEN** 非 Run 提交者请求取消该 Run
- **THEN** API 返回稳定的授权拒绝且 Run 状态保持不变
