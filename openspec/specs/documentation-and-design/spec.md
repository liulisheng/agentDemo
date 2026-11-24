# documentation-and-design Specification

## Purpose

为平台代码和运行流程建立持续维护的中文设计文档体系，使模块边界、数据模型、接口、状态机、流程、部署和故障处理都能被独立阅读、评审和验证。

## Requirements

### Requirement: Project design documents use Chinese names and descriptions
系统 SHALL 将项目级设计文档放在 `docs/` 下，并使用中文文件名和中文正文；OpenSpec CLI 保留的固定 artifact 文件名不受此要求影响。

#### Scenario: Add a new platform module
- **WHEN** 实现新的后端模块或前端业务域
- **THEN** 变更同时新增或更新对应的中文设计文档，并说明模块职责和依赖边界

### Requirement: Architecture and process diagrams are maintained
系统 SHALL 为每个跨模块能力维护至少一张架构图和一张流程图；涉及状态变化时还 SHALL 维护状态图或时序图。

#### Scenario: Change Run lifecycle
- **WHEN** Run 状态、Worker、Runtime 或持久化流程发生变化
- **THEN** 对应架构图、状态图、时序图和流程说明一并更新

### Requirement: Public code has documentation traceability
系统 SHALL 让公开的 API、领域对象、端口、适配器和 Worker 能够追溯到中文设计文档、OpenSpec 能力或任务。

#### Scenario: Review a RuntimeAdapter
- **WHEN** 评审 RuntimeAdapter 的接口或实现
- **THEN** 代码、接口契约、运行流程和故障处理文档之间存在明确链接

### Requirement: Documentation includes evidence and status
系统 SHALL 在设计文档中标明状态、更新时间、适用范围、已验证行为、未验证项和外部依赖，不把推断写成已验证事实。

#### Scenario: External runtime capability is unverified
- **WHEN** DeepSeek、Codex 或其他 Runtime 的能力尚未完成真实验证
- **THEN** 文档标记为未验证，并记录验证入口和限制

### Requirement: Implementation tasks include documentation work
系统 SHALL 将文档更新作为实现任务的一部分，不把架构、流程、接口和验收文档集中推迟到所有代码完成之后。

#### Scenario: Complete a feature task
- **WHEN** 一个功能任务被标记完成
- **THEN** 其对应代码测试和中文文档验证都已完成

### Requirement: Commit and code explanations use Chinese
系统 SHALL 要求提交信息、提交说明、公共代码注释和文档字符串使用中文，并说明变更目的、影响范围和验证结果。

#### Scenario: Submit a feature change
- **WHEN** 开发者提交一个功能或修复
- **THEN** Commit message 和 body 使用中文，包含变更内容、验证结果、风险和后续项

#### Scenario: Add a public adapter
- **WHEN** 新增 RuntimeAdapter、API 路由、Worker 或公共领域类
- **THEN** 代码包含中文用途说明，并链接或对应到中文设计文档和 OpenSpec 任务

### Requirement: Local configuration is excluded from commits
系统 SHALL 在提交前排除虚拟环境、缓存、密钥、本机 REA 配置和临时分析文件。

#### Scenario: Review staged files
- **WHEN** 提交前检查暂存区
- **THEN** 暂存区只包含项目代码、中文文档、OpenSpec 变更和必要的锁文件

### Requirement: Prototypes are the user-visible fidelity baseline
系统变更 SHALL 以 `docs/原型/` 中对应 HTML 的布局、视觉令牌、交互文案、状态和示例数据为验收基线；实现不得另起一套未记录的视觉或状态语义。

#### Scenario: Implement a workbench change
- **WHEN** 工作台增加回答策略、思考阶段或审批状态
- **THEN** 对应原型、中文需求、流程图和实现测试同时描述同一用户路径

#### Scenario: Prototype and implementation differ
- **WHEN** 当前 React 页面或 API 与原型的字段、布局、状态或文案不一致
- **THEN** 任务不得标记完成，必须先更新提案/原型/设计或按已批准的差异记录原因

### Requirement: Design documents distinguish implemented and planned behavior
系统 SHALL 在中文设计文档和原型中明确区分已实现、已验证、方案设计、待接入和未验证，不把静态演示按钮或供应商推断写成已实现能力。

#### Scenario: Runtime capability is not verified
- **WHEN** DeepSeek、Codex 或其他引擎的思考参数尚未完成契约验证
- **THEN** 文档和原型标记为方案/未接入，记录验证命令和限制

#### Scenario: Complete the change
- **WHEN** 实现任务准备标记完成
- **THEN** 原型、流程/状态图、API/数据说明、测试结果和未验证项均已同步

### Requirement: Independent strategy panel and real model evidence are documented
系统 SHALL 提供独立 HTML 策略面板原型、中文架构/流程/数据/API/权限/验收文档，并明确区分真实模型、Fake Runtime、确定性 Mock 和未验证的 Provider 能力。

#### Scenario: Review panel fidelity
- **WHEN** 评审运行配置界面
- **THEN** 原型、React 组件和交互文档使用相同的双栏布局、字段文案、状态颜色、只读/错误/空状态和秒级时间

#### Scenario: Review real model acceptance
- **WHEN** 评审真实模型验收记录
- **THEN** 文档包含 Endpoint 约定、SecretRef 边界、Run/Trace/Usage 证据、验证命令和未验证项，不包含密钥值
