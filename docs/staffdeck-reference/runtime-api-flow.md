# 运行时 API 和状态流

## 认证后初始化

```text
POST /api/auth/login
    |
    v
GET /api/auth/me
GET /api/enterprise/agents
GET /api/enterprise/teams
GET /api/enterprise/model-configs
GET /api/chat/agents
GET /api/chat/sessions
GET /api/chat/handoffs
GET /api/chat/ui-config
```

请求普遍带有 `tenant_id`。独立实现中租户应来自已验证 IdentityContext，而不是信任请求体。

## 员工能力加载

```text
GET /api/enterprise/agents/{agent_id}/skills
GET /api/enterprise/general-skills
GET /api/enterprise/knowledge-bases
GET /api/enterprise/tools
GET /api/enterprise/sessions
GET /api/enterprise/feedback/summary
GET /api/enterprise/scheduled-tasks
GET /api/enterprise/agents/{agent_id}/evolution/proposals
GET /api/enterprise/agents/{agent_id}/work-record
```

## 对话运行

```text
POST /api/chat/stream
    |
    +-- 创建或恢复 Session
    +-- 保存 User Message
    +-- 创建 Turn
    +-- 生成 CompositionSnapshot
    +-- 运行 Runtime
    +-- 返回流式结果
    |
    v
GET /api/chat/sessions/{session_id}/messages
GET /api/chat/sessions/{session_id}/events
GET /api/chat/sessions/{session_id}/trace
GET /api/chat/sessions/{session_id}
```

黑盒测试观察到的状态流：

```text
No Session
    |
    v
Send first message
    |
    v
Session created
    |
    v
Session status = running
    |
    +-- user_message_received
    +-- composition_snapshot_compiled
    +-- model/runtime processing
    +-- trace and messages query
    |
    v
completed / failed / unknown
```

本次捕获在 3 秒时仍处于 Runtime 执行中，`/api/chat/stream` 尚未完成，因此最终结果状态保持未知。

## 独立平台的 API 边界

平台 API 可以保留类似的资源关系，但使用自己的路径和模型：

```text
/api/v1/digital-employees
/api/v1/digital-employees/{id}/versions
/api/v1/digital-employees/{id}/bindings
/api/v1/sessions
/api/v1/runs
/api/v1/runs/{id}/messages
/api/v1/runs/{id}/events
/api/v1/runs/{id}/trace
```
