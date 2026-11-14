# 目录版本与部署说明

更新时间：2026-11-14

本文件记录最小闭环涉及的 DigitalEmployee、Skill、Tool、ExecutionProfile 和 Deployment 状态。实现位于 backend/src/agent_platform/domain/catalog.py 与 backend/src/agent_platform/application/catalog_service.py，契约测试位于 backend/tests/test_catalog_domain.py 和 backend/tests/test_catalog_service.py。

## 架构图

~~~mermaid
flowchart TD
  Tool[ToolVersion] --> Skill[SkillVersion]
  Skill --> EmployeeVersion[DigitalEmployeeVersion]
  Tool --> EmployeeVersion
  Profile[ExecutionProfile] --> EmployeeVersion
  EmployeeVersion --> Deployment
  Deployment --> Session
~~~

## 生命周期

~~~mermaid
stateDiagram-v2
  [*] --> DRAFT
  DRAFT --> PUBLISHED: 依赖校验通过
  PUBLISHED --> DEPRECATED: 管理员废弃
  PUBLISHED --> ACTIVE: Deployment 固定
  ACTIVE --> RUNNING: Session 创建 Run
~~~

已发布版本是冻结数据类；编辑行为需要创建新版本。Deployment 只接受同一租户的 PUBLISHED DigitalEmployeeVersion，并在 Run 创建时复制 Skill/Tool/Profile 引用。

## 绑定规则

Skill 声明的 Tool、员工版本绑定的 Tool、ExecutionProfile 的 Tool policy 和当前租户授权共同取交集。任何未发布、跨租户或不在交集中的 Tool 都不能进入 RuntimeRequest。

## 验证

~~~bash
cd backend
python -m pytest tests/test_catalog_domain.py tests/test_catalog_service.py -q
~~~

已验证：草稿发布、发布版本不可变、Skill 三种类型、依赖缺失错误和跨租户 Deployment 查询。未验证：MySQL 持久化、回滚 API 和生产策略解析。

## CapabilityBinding 与回滚

~~~mermaid
sequenceDiagram
    participant Admin as 管理员
    participant Catalog as CatalogService
    participant Deployment as Deployment
    participant Run as 新 Run
    Admin->>Catalog: 发布员工版本 v2
    Admin->>Deployment: rollback 到已发布 v1
    Catalog->>Deployment: 替换 active employee_version
    Run->>Deployment: 解析 v1
    Note over Run: 已开始 Run 继续使用自己的 RunSnapshot
~~~

CapabilityBinding 使用 binding_id、employee_version_id、slot、module_id、module_version 和 resource_ref 记录 Skill/Tool 等能力来源。回滚只更新 Deployment 指针，不修改已发布版本或历史 RunSnapshot。
