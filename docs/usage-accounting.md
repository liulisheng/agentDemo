# 用量台账

更新时间：2026-11-15
状态：Fake 用量、精确/估算状态、定价版本和租户聚合已实现

ModelCallRecord 保存 input/output/total Token、cached/reasoning Token（可选）、provider request id、cost、currency、pricing_version 和 usage_status。完成记录不可覆盖；供应商修正通过 UsageAdjustment 追加。

预算阻断和月度报表仍是后续 MySQL/计费阶段能力。当前 API 可通过 GET /api/v1/runs/{run_id}/usage 查看一次 Run 用量。

验证命令：cd backend && python -m pytest tests/test_usage_observability.py -q。

~~~mermaid
flowchart LR
  ModelCall --> UsageLedger
  UsageLedger --> TenantAggregate
~~~
