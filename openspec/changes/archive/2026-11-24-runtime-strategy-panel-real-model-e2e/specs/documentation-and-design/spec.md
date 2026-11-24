# Spec Delta

## ADDED Requirements

### Requirement: Independent strategy panel and real model evidence are documented
系统 SHALL 提供独立 HTML 策略面板原型、中文架构/流程/数据/API/权限/验收文档，并明确区分真实模型、Fake Runtime、确定性 Mock 和未验证的 Provider 能力。

#### Scenario: Review panel fidelity
- **WHEN** 评审运行配置界面
- **THEN** 原型、React 组件和交互文档使用相同的双栏布局、字段文案、状态颜色、只读/错误/空状态和秒级时间

#### Scenario: Review real model acceptance
- **WHEN** 评审真实模型验收记录
- **THEN** 文档包含 Endpoint 约定、SecretRef 边界、Run/Trace/Usage 证据、验证命令和未验证项，不包含密钥值
