# agent-platform-foundation 清单完成说明

更新时间：2026-11-15
状态：OpenSpec 71/71 任务已标记完成，严格校验通过

## 交付范围

- 后端基础：身份、租户、错误、Runtime/Model/Workspace/Storage/Memory/Trace 端口。
- 目录与版本：数字员工、Skill、Tool、ExecutionProfile、ModelRoute、Deployment、回滚和 RunSnapshot。
- Runtime：Fake、Harness 生命周期桥、Generic CLI、Codex/Hermes 契约和能力注册表。
- 渠道：Conversation、Participant、AgentBinding、Message、FakeChannel、@提及/命令路由、loop prevention、Delivery retry。
- 上下文与记忆：预算策略、ContextSnapshot、企业记忆作用域、显式写入和检索 Trace。
- 观测与用量：Span/Timeline、ModelCall、ToolInvocation、UsageLedger、Adjustment、RawPayload policy。
- 持久化：V003 MySQL 迁移设计、租约/fencing bridge、Run 快照和 Runtime 引用。
- 前端：Gallery、Dashboard、Skill List、Chat Workspace、Run Trace 路由。
- 文档：每个功能域包含中文职责、架构/流程/状态/数据说明、验证命令和未验证边界。

## 验证证据

~~~bash
cd backend
python -m pytest -q                         # 86 passed
python -m ruff check src tests               # 通过
python -m compileall -q src tests            # 通过
cd ../frontend
npm test -- --run                          # 3 files, 3 passed
npm run build                               # 通过，保留 chunk size warning
cd ../harness-engine
PYTHONPATH=src python -m pytest -q           # 164 passed, 4 skipped
cd ..
openspec validate agent-platform-foundation --type change --strict
~~~

## 仍需真实环境验证的外部能力

DeepSeek/Codex 真实 SDK、new-api/Dadi Gateway、Dadi User Center、IM、S3/MinIO/OSS/COS、MySQL 实例、Kubernetes Sandbox 和生产计费尚未在本地离线测试中宣称通过。对应适配器已提供端口、Fake/契约实现和独立开关，缺失能力会明确报错，不静默回退。
