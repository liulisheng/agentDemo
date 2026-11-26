# 会话、渠道与路由

更新时间：2026-11-25
状态：单员工/团队 Conversation、异步 Router/成员 Run Worker 队列、MySQL 团队/编排/产物及 Worker 心跳、Web 交付已接入；外部 IM 未接入，跨节点负载与故障规模未做生产验证

## 架构图

~~~mermaid
flowchart LR
  Channel[Fake/Webhook Channel] --> Normalize[Message 规范化]
  Normalize --> Conversation[Conversation]
  Conversation --> Mode{ConversationMode}
  Mode --> Single[SINGLE_AGENT]
  Mode --> Team[TEAM]
  Single --> Binding[AgentBinding]
  Team --> TeamBinding[ConversationTeamBinding]
  TeamBinding --> Orchestration[PLANNING OrchestrationRun]
  Binding --> Session[独立 Session]
  Orchestration --> RouterRun[QUEUED Router Run]
  RouterRun --> RunWorker[引擎过滤 Run Worker]
  RunWorker --> Plan[校验后的 Task DAG]
  Plan --> ChildRuns[成员 QUEUED 子 Run]
  ChildRuns --> RunWorker
  Conversation --> Delivery[OutboundDelivery]
~~~

Conversation 与 Session 分离；同一个群聊可以绑定多个员工，每个绑定有自己的 Session。

数字员工对话模式的员工会话目录只返回 `SINGLE_AGENT` Conversation；同一数字员工作为 TeamMember 参与的 `TEAM` Conversation 只出现在团队协作工作台，避免模式切换后最近对话串栏。

团队 Agent 消息通过对应的 `AgentBinding`/TeamMember Session 回显员工名称和成员标识。当前 `messages.sender_id` 保留现有用户外键约束，Agent 消息落库时使用当前提交者身份作为存储归属；对外读取仍按绑定 Session 展示 Agent 名称，不把该存储键当作 Agent 身份。

## 触发状态

~~~mermaid
stateDiagram-v2
  [*] --> MessageSaved
  MessageSaved --> SingleRun: 单员工消息
  MessageSaved --> NoRun: 团队普通消息
  MessageSaved --> Triggered: @提及/命令/显式规则
  MessageSaved --> LoopPrevented: Agent 来源或 routing_depth > 0
  Triggered --> RouterQueued: TEAM 消息
  RouterQueued --> RouterRunning: Worker lease
  RouterRunning --> PlanValidated
  DeliveryPending --> DeliveryRetrying: 渠道暂时失败
  DeliveryRetrying --> DeliverySent
  SingleRun --> RunCreated
  PlanValidated --> PlanApproval
  PlanApproval --> TaskReady
  TaskReady --> ChildRunQueued
  ChildRunQueued --> ChildRunRunning: Worker lease
~~~

FakeChannelAdapter 以 tenant_id + channel + external_message_id 幂等，重复 Webhook 只返回同一内部 Message。

## API

- POST /api/v1/conversations
- POST /api/v1/conversations/team
- POST /api/v1/conversations/{id}/continue-as-team
- POST /api/v1/conversations/{id}/bindings
- POST /api/v1/conversations/{id}/messages
- POST /api/v1/conversations/{id}/messages/{message_id}/route
- POST /api/v1/conversations/{id}/deliveries
- POST /api/v1/conversations/{id}/orchestrations
- POST /api/v1/conversations/{id}/team-turns
- GET /api/v1/conversations/{id}/team-activity
- GET /api/v1/orchestrations/{id}/tasks
- GET /api/v1/orchestrations/{id}/events
- GET /api/v1/orchestrations/{id}/artifacts

`SINGLE_AGENT` Conversation 继续使用单 Session/单 Run；`TEAM` Conversation 使用固定 TeamVersion、多个 AgentBinding、Task DAG 和独立 Engine Worker Pool。两种模式共享消息、Trace、用量和 Artifact 权限边界，切换团队配置不会修改已有 Conversation 的运行快照。

团队用户通过 `POST /api/v1/conversations/{id}/team-turns` 提交需求后，服务端先持久化 PLANNING Orchestration 和 QUEUED Router Run。Router 原文作为用户消息保存，执行用的完整内部指令作为隐藏 SYSTEM 输入对象绑定到 Run；Run Worker 在自己的凭据边界内调用固定 EngineProvider/ModelRoute。Activity 查询读取 Worker 终态，验证 DAG/成员/能力后恢复任务计划。计划确认后，每个 READY Task 由 Scheduler 创建 Attempt 和 QUEUED 子 Run，Run Worker 领取执行；轮询将结果收敛到 Task、Artifact、Trace 和群聊消息。

团队成员的当前认领任务、执行阶段、EngineProvider、Worker Pool、Attempt 和 Trace 通过成员活动和任务/事件弹框展示；原始思考链、凭据和隐藏 Prompt 不进入用户可见消息。

团队消息、Router 回复和成功任务的 Agent 回复均以稳定消息/Run 标识幂等保存。服务端以 TeamVersion 的固定成员绑定解析路由目标，不能由前端请求覆盖租户、成员部署或运行引擎。

Worker 执行需要的 Prompt 以 `source=system` 的内部消息与 QUEUED Run 原子保存，`messages` 用户可见 API 会过滤这类内部输入；团队用户只看到原始需求、Router 的计划摘要和成员最终结果。

Worker Pool 队列深度和活跃 Run 来自 MySQL；Run Worker 以 `AGENT_PLATFORM_WORKER_ENGINE_NAMES` 限定可领取的引擎，并每 10 秒更新 V014 Worker 心跳，30 秒内有心跳才显示健康。Compose 默认启动 3 个当前 Runtime Engine Worker 副本，实际并发还受 Team 与成员限额控制。

验证命令：
~~~bash
cd backend
python -m pytest tests/test_conversations.py tests/test_extended_api.py tests/test_team_turns.py tests/test_team_task_execution.py -q
~~~

MySQL Run Worker 集成（需 disposable MySQL）：

~~~bash
cd backend
AGENT_PLATFORM_MYSQL_TEST_ALLOW_CREATE_DATABASE=1 python -m pytest tests/integration/test_mysql_team_orchestration_repository.py -q
~~~
