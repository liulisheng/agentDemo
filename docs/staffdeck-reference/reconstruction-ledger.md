# StaffDeck 黑盒重建台账

更新时间：2026-11-15

| 观察 | 项目映射 | 证据/限制 |
|---|---|---|
| 员工卡片和能力统计 | DigitalEmployee / CapabilityBinding | evidence.md Gallery 记录 |
| CompositionSnapshot | CompositionSnapshot | domain-model.md；字段存在推断 |
| Session/Turn/Trace | SessionRecord / Turn / TraceSpan | runtime-api-flow.md；最终状态未完整捕获 |
| 渠道重复消息 | FakeChannel external_message_id 幂等 | 项目自有契约，非 StaffDeck 代码 |

项目只保留观察、推断和未知项，不复制 StaffDeck 源码、静态资源、样式或私有模块。
