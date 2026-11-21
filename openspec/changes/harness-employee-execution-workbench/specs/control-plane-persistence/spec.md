# Spec Delta

## ADDED Requirements

### Requirement: New workbench runs persist their authenticated submitter
系统 SHALL 在新建工作台 Run 时持久化认证上下文中的提交者用户标识，并以该标识核验关联写操作审批；不得从请求体读取授权身份。

#### Scenario: Run submitter returns to approve a preview
- **WHEN** 创建该 Run 的同租户用户确认绑定的预览
- **THEN** Approval 记录的 requested_by 与 Run 持久化的提交者一致并允许继续审批状态迁移

#### Scenario: A different user tries to approve the Run
- **WHEN** 同租户其他用户确认该 Run 的预览
- **THEN** 系统拒绝确认且不排队或执行写操作

### Requirement: Runs persist the approval waiting state
系统 SHALL 持久化 AWAITING_APPROVAL Run 状态，并仅在预览绑定的写操作获批且结果确定后完成该 Run。

#### Scenario: Run waits for user approval
- **WHEN** Agent 生成受策略要求的写操作预览
- **THEN** 系统持久化审批引用并将 Run 转为 AWAITING_APPROVAL，且不发送写请求

#### Scenario: Approval is rejected or expires
- **WHEN** 用户拒绝审批或审批超过有效期
- **THEN** 审批记录保存 REJECTED 或 EXPIRED，Run 以明确的取消结果关闭，且不调用写工具

### Requirement: Approval actions are durable and single-use
系统 SHALL 将一次审批绑定到一个 Run、ToolInvocation、精确 Tool/Profile 版本、参数摘要和预览摘要，并记录 Run 提交者及批准人；仅同一租户的 Run 提交者可以批准，批准动作以原子状态迁移最多消费一次。

#### Scenario: Two workers claim the same approval
- **WHEN** 多个 Worker 并发领取同一已批准动作
- **THEN** 只有一个有效租约持有者能消费授权并向 MCP 发送该写请求

#### Scenario: Approval confirmation is retried
- **WHEN** 用户对同一审批重复提交相同的幂等确认
- **THEN** 系统返回现有审批/动作状态且不创建第二次远端写操作

#### Scenario: Approval is requested by a different user
- **WHEN** 与 Run 提交者不同的同租户用户确认该审批
- **THEN** 系统拒绝批准且保持审批未执行

### Requirement: Approval workers are protected by leases and fencing
系统 SHALL 为批准动作记录 Worker 租约和单调递增 fencing token，并在状态提交时校验当前 token。

#### Scenario: Stale approval worker reports a result
- **WHEN** Worker 使用过期 token 报告批准动作结果
- **THEN** 系统拒绝其更新且保留当前动作状态

#### Scenario: Remote write outcome cannot be established
- **WHEN** 远端可能已执行批准写请求但 Worker 未取得可信响应
- **THEN** 系统持久化 UNKNOWN 并阻止自动重放该动作

### Requirement: Run cancellation is persisted and fenced
系统 SHALL 持久化 QUEUED/STARTING/RUNNING Run 的取消状态；只有当前 Worker 的有效 fencing token 可以完成取消或写入 UNKNOWN。

#### Scenario: A queued Run is cancelled before claim
- **WHEN** Run 提交者取消 QUEUED Run
- **THEN** Run 直接转为 CANCELLED，后续 Worker claim 不会领取它

#### Scenario: A running Run cancellation is uncertain
- **WHEN** Runtime 取消后无法证明执行已停止或租约已失效
- **THEN** Run 转为 UNKNOWN、fencing token 递增且不再自动执行

### Requirement: A Session Home has at most one active Harness Run
系统 SHALL 以平台和租户范围为每个 Session Home 保存一个持久运行租约；Run Worker 必须在同一事务获得 Run claim 和 Session lease，且只有当前 Worker/token 可续租或释放该 lease。

#### Scenario: Different workers claim Runs for the same Session
- **WHEN** 多个 Worker 并发领取绑定同一 Session 的 QUEUED Run
- **THEN** 最多一个 Run 持有 ACTIVE Session lease，其余 Run 保持 QUEUED，不能打开同一个 Harness Home

#### Scenario: A known Run result releases the Session Home
- **WHEN** 当前 lease 持有者证明 Harness 已关闭并持久化 SUCCEEDED、FAILED 或 CANCELLED
- **THEN** 完成 Run 的同一事务释放 Session lease，允许后续 Run 使用该 Session Home

#### Scenario: A Run lease expires with an uncertain Harness process
- **WHEN** Run Worker lease 到期，无法证明旧 Harness/MCP 子进程已停止
- **THEN** Run 转 UNKNOWN，Session lease 变为 QUARANTINED，Session 转 INTERRUPTED；新 Run 不能复用该 Home 或自动重试
