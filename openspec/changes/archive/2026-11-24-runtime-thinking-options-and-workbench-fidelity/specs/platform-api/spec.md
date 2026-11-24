# Spec Delta

## ADDED Requirements

### Requirement: Runtime configuration APIs expose typed answer strategy
系统 SHALL 通过默认运行环境和 Deployment 运行配置 API 读写受白名单约束的回答策略字段，并按当前认证租户和作用域返回来源。

#### Scenario: Save a valid strategy revision
- **WHEN** 管理员提交有效的思考模式、推理强度、预算、答复详细程度、输出/工具/超时限制和期望配置版本
- **THEN** API 创建下一不可变修订并返回策略摘要、配置版本和来源，不返回凭据明文

#### Scenario: Reject unknown strategy fields
- **WHEN** 请求包含未在 RuntimeOptions 白名单中的字段或非法范围
- **THEN** API 返回稳定参数错误，不修改已有修订

#### Scenario: Concurrent strategy update
- **WHEN** 管理员使用过期 `expected_config_version` 保存策略
- **THEN** API 返回配置修订冲突，保留当前修订和当前 Deployment 指针

### Requirement: Session and Run APIs return fixed strategy snapshots
系统 SHALL 在 Session 和 RunSnapshot 响应中返回实际生效的回答策略、来源和配置修订，且后续默认值变化不改变历史快照。

#### Scenario: Create a Session with deep thinking
- **WHEN** Session 创建请求解析出兼容的深度思考策略
- **THEN** API 返回固定策略和来源；后续 Run 使用同一 Session 策略

#### Scenario: Submit a per-run strategy override
- **WHEN** 工作台提交允许范围内的本轮回答策略覆盖
- **THEN** API 将覆盖写入该 RunSnapshot，不修改 Session 或 Deployment 默认

#### Scenario: Strategy is incompatible
- **WHEN** Session 或 Run 的策略与 EngineProvider/ModelRoute 不兼容
- **THEN** API 在 Runtime 出站前返回能力错误，不创建伪成功 Run
