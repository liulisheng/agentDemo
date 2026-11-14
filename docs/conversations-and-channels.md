# 会话、渠道与路由

更新时间：2026-11-15
状态：Conversation/Message/FakeChannel/触发路由/投递重试已验证

## 架构图

~~~mermaid
flowchart LR
  Channel[Fake/Webhook Channel] --> Normalize[Message 规范化]
  Normalize --> Conversation[Conversation]
  Conversation --> Binding[AgentBinding]
  Binding --> Session[独立 Session]
  Conversation --> Delivery[OutboundDelivery]
~~~

Conversation 与 Session 分离；同一个群聊可以绑定多个员工，每个绑定有自己的 Session。

## 触发状态

~~~mermaid
stateDiagram-v2
  [*] --> MessageSaved
  MessageSaved --> NoRun: 普通群消息
  MessageSaved --> Triggered: @提及/命令/显式规则
  MessageSaved --> LoopPrevented: Agent 来源或 routing_depth > 0
  Triggered --> RunCreated
  DeliveryPending --> DeliveryRetrying: 渠道暂时失败
  DeliveryRetrying --> DeliverySent
~~~

FakeChannelAdapter 以 tenant_id + channel + external_message_id 幂等，重复 Webhook 只返回同一内部 Message。

## API

- POST /api/v1/conversations
- POST /api/v1/conversations/{id}/bindings
- POST /api/v1/conversations/{id}/messages
- POST /api/v1/conversations/{id}/messages/{message_id}/route
- POST /api/v1/conversations/{id}/deliveries

验证命令：
~~~bash
cd backend
python -m pytest tests/test_conversations.py tests/test_extended_api.py -q
~~~
