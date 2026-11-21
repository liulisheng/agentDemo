# Spec Delta

## ADDED Requirements

### Requirement: Worker passes the resolved employee context to the engine
系统 SHALL 使用创建 Run 时固定的员工版本、系统指令、Skill 顺序及文件包摘要、模型路由和有效工具定义构造类型化 Engine Request，不得在执行时重新解析到较新的资源版本。

#### Scenario: Employee resources change after Run submission
- **WHEN** 员工或 Skill 在 Run 入队后发布了新版本
- **THEN** Worker 仍向 Harness 提交该 RunSnapshot 固定的资源版本和摘要

#### Scenario: A tool is outside the resolved allowlist
- **WHEN** Harness 请求当前 RunSnapshot 未允许的工具
- **THEN** Runtime Adapter 拒绝该调用并返回可关联的工具策略错误

### Requirement: Harness configuration and tools are isolated per Run
系统 SHALL 为每个 Run 创建独立 Harness 生命周期，并根据该 RunSnapshot 的固定员工、Skill、模型路由与 MCP Profile 生成独立配置；只向模型暴露 Run allowlist 中的 READ/PREVIEW 工具。

#### Scenario: Two Runs use different employees or tools
- **WHEN** 两个不同员工或不同 allowlist 的 Run 先后或并发执行
- **THEN** 每个 Run 使用独立 Harness 进程和配置 overlay，系统提示、Skill 文件及 MCP tools/list 只包含其固定快照，工具配置不会串到另一 Run

#### Scenario: A tool is removed from the effective allowlist
- **WHEN** 模型尝试调用未包含在该 Run 的员工/Skill/租户/Profile 交集中的工具
- **THEN** 该 Run 的 Stdio proxy 在转发至上游 MCP 前拒绝调用，并记录脱敏策略错误

#### Scenario: A Run finishes or loses its lease
- **WHEN** Run 成功结束、取消、超时或 Session lease 丢失
- **THEN** Worker 关闭该 Run 的 Harness/MCP 子进程并删除临时工具配置；其他 Run 的配置和 Session Home 不受修改

### Requirement: Worker owns the asynchronous engine lifecycle
系统 SHALL 由受控 Worker 领取持久化 Run 并负责 Runtime 生命周期；API 请求处理不得直接启动 Harness 或 MCP 进程。

#### Scenario: Worker claims a queued Run
- **WHEN** Worker 通过有效租约领取 QUEUED Run
- **THEN** Worker 使用 fencing token 调用所选 Runtime Adapter 并把标准事件关联到对应 Session、Run 和 Trace

#### Scenario: Worker loses its lease
- **WHEN** Worker 的租约失效或 fencing token 被更新
- **THEN** Worker 停止提交新的状态，并关闭由该 Worker 管理的 Runtime/MCP 子进程

#### Scenario: A queued Run is cancelled before claim
- **WHEN** Run 提交者在 Worker 领取前请求取消 QUEUED Run
- **THEN** 控制面将 Run 直接标记为 CANCELLED，Worker 不调用 Runtime

#### Scenario: Runtime cancellation cannot be confirmed
- **WHEN** Worker 收到运行中 Run 的取消请求，但 Runtime 不支持取消或不能证明执行已停止
- **THEN** Worker 将 Run 标记为 UNKNOWN，并且不自动重放该 Run
