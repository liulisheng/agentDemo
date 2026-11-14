# 运行租约、fencing token 与幂等规则

状态：run 控制面协议、V002 上传 intent 与内容清理队列 schema、消息仓储和非流式用户/助手消息发布已实现；在一次性 MySQL 8.4.13 上通过验证。流式 checkpoint、GC worker、分布式通知、沙箱终止和生产运维行为仍待后续阶段交付。日期：2026-11-13。

## 运行状态

```text
queued -> starting -> running -> succeeded
                         ├----> failed
                         ├----> cancelling -> cancelled
                         └----> unknown
```

`unknown` 表示控制面无法证明 worker 是否已经产生副作用。它不能自动回到 queued，也不能自动重复提交模型请求。用户或安全策略可以显式创建新的重试运行，并保留原运行引用。

## 幂等

客户端为一次用户意图提供 `idempotency_key`。唯一范围是 `(platform_id, tenant_id, idempotency_key)`。第一次请求创建 queued 运行并返回 `run_id`；重复请求返回原运行及当前状态；请求参数与原记录摘要不一致时返回幂等冲突，不复用旧运行。

幂等键保留时间至少覆盖运行保留期。成功、失败、取消和 unknown 都保留原记录，防止网络重试产生第二个任务。

## 对话消息与输出 checkpoint

实现消息持久化后，首次提交还会创建一条 `USER/COMPLETE` 消息，并与 run 在 MySQL 中建立关联。相同幂等键和相同请求摘要返回原 user message/run，不再写第二条消息；同键不同摘要仍冲突。助手结果只在对象存储中保存正文，MySQL 的 `messages.content_ref` 与 `runs.result_ref` 指向同一最终对象或 manifest。

流式 assistant message 的 delta 按序写不可变对象分段，MySQL 以 message ID 和 `chunk_sequence` 记录引用。`messages.last_committed_chunk_sequence` 与每条 chunk 在一个事务中推进，只有前一序号加一的 chunk 可以提交，所以已提交数据始终是连续前缀。客户端断线重连后持续 tail 同一个 MySQL chunk log；通知只用于唤醒查询，不能在一次 replay 后切换到有丢失窗口的 volatile subscription。worker 消失后恢复到最后 checkpoint，消息显示 `PARTIAL`、run 显示 `UNKNOWN`；不自动重跑原请求。

每次对象上传前先创建 `content_upload_intents` 行并分配 object ref。上传发布事务和过期清理事务锁定同一 intent：发布必须证明 lease 仍有效，清理必须先把过期 intent 标为 `EXPIRED` 再排入 `content_gc_queue`。旧 run 的 `runs.result_ref` 也是有效对象根；孤儿扫描不得回收仍由该字段引用的结果。只有 `QUEUED`、`DELETING`、`RETRY` 清理任务和仍有效的 `UPLOADING` intent 暂时保护对象；过期 intent 与 `DELETED` 任务不永久屏蔽晚到 PUT，扫描发现对象重新出现后会重新排队。显式 session 删除会排入所有关联 intent 的对象引用，包括仍在上传的对象。

同一会话的 message `sequence_no` 由服务端在事务中锁定 session 行后分配；客户端时间不用于决定顺序。session 显式删除时，先把 message/chunk/manifest/Harness home/workspace 及所有关联 upload intent（含 `UPLOADING`）的对象引用写入 `content_gc_queue`，并将关联 intent 标为 `EXPIRED`，再删除会话消息、run 与会话级审计；对象清理由可重试 worker 完成，晚到 PUT 由孤儿扫描再次入队。

## 租约

调度器领取 queued 运行时在一个 InnoDB 事务内锁定记录，生成单调递增 `fencing_token`，写入 `worker_id` 和 `lease_expires_at`，再提交。推荐租约有效期 30 秒，worker 每 10 秒续租；最终值必须由部署容量和网络延迟压测确认。

所有 worker 写操作都带 `platform_id`、`tenant_id`、`run_id` 和 `fencing_token`。SQL 更新条件必须匹配当前 token；影响行数为零即视为旧 worker，被拒绝并停止。新 worker 先写入更高 token，再允许旧 worker 失效。

租约过期后，回收器先把运行标记为 unknown 并使旧 token 失效，再检查 VM 和 worker。只有确认旧执行域已销毁，或经过人工确认，才可以创建新的重试运行。

## 取消

取消请求先校验 TenantContext、角色、会话与引擎能力，再在事务内把 run 更新为 `CANCELLING`，随后调用当前活动的引擎实例 `cancel(native_session_id)`。该调用返回的是取消确认状态；worker 报告最终结果或 system 在租约过期后将运行收敛为 `UNKNOWN`，不能因为请求已发送就报告 `CANCELLED`。如果引擎不支持取消，接口显式返回 `UnsupportedCapabilityError`，不伪报成功。分布式 worker 通知和超时终止由后续调度/沙箱层提供；本地 P4 服务不实现跨进程消息传递。

## 状态更新规则

- 终态不可被普通更新覆盖。
- 旧 fencing token 的 heartbeat、finish、artifact commit 全部拒绝。
- 数据库提交成功后才向调用方返回 run_id。
- 控制面未知结果时只记录 unknown，不自动重放。
- 所有状态转换写审计事件，审计事件带 actor、platform、tenant、run 和策略版本。

## 必测场景

- 两个 worker 同时领取同一运行，只有一个获得有效 token。
- 旧 worker 在新 worker 领取后发送 finish，数据库拒绝该更新。
- 客户端重复提交相同幂等键，只产生一个 run。
- 相同幂等键但不同参数返回冲突。
- 取消期间 worker 断连，运行进入 unknown 并触发回收。
- 数据库提交成功但响应丢失，客户端重试仍得到原 run。
- 同幂等键重试不重复创建 user message、assistant message 或 stream chunk。
- fencing 旧 worker 后，其任何 stream checkpoint、manifest 或 message terminal update 都被拒绝。
- 客户端从 checkpoint 序号重连时只收到后续段且顺序稳定；worker crash 后能读取最后已提交 partial。
- session 删除后内容引用不再可读，`content_gc_queue` 仍可在重启后完成对象删除。
