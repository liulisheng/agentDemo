# 执行观测与 Trace

更新时间：2026-11-15
状态：Trace/Usage/RawPayload 契约已验证

## 数据分层

~~~mermaid
flowchart TD
  Session --> Run
  Run --> Span[TraceSpan]
  Run --> Model[ModelCallRecord]
  Run --> Tool[ToolInvocationRecord]
  Run --> Usage[UsageLedger]
  Run --> Payload[RawPayloadRef]
~~~

普通 Trace 只保存状态、父子关系、摘要和引用；RawPayloadPolicy 关闭时不保存正文，打开时只返回加密对象引用、哈希、大小和保留信息。

## Span 类型

Session、Run、Context、Skill、Tool、Runtime、Model、Memory、Approval、Sandbox、Channel、Result。Span 通过 trace_id、parent_span_id、run_id 和 sequence 关联。

## 租户隔离

TraceRepository、UsageLedger 和 RawPayloadStore 的查询都以 platform_id + tenant_id 为索引；跨租户查询返回空或资源不存在，不泄露资源存在性。

验证命令：
~~~bash
cd backend
python -m pytest tests/test_usage_observability.py tests/test_raw_payload_policy.py tests/test_control_plane_service.py -q
~~~
