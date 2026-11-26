# Agent 团队端到端可用性实施计划

> **For agentic workers:** 本计划按仓库现有 OpenSpec `agent-team-orchestration` 变更和既有确认的方案二实施；每项按测试先行、独立验收后再继续。

**目标：** 让管理员可以配置并发布团队，用户可以创建真实团队对话、提交任务、查看执行状态并交付产物，服务重启后数据仍可用。

**架构：** FastAPI 负责租户范围的控制面 API；MySQL 保存团队、编排、任务、事件、产物和交付状态；对象存储保存产物正文；React 工作台只呈现服务端状态并执行原型定义的操作。每个团队成员固定使用其部署版本、EngineProvider、ModelRoute 和能力快照。

**Runtime 边界：** MySQL 模式下 API 只创建 QUEUED Run 与受控输入对象；独立 Run Worker 按持久化 Session/Run 快照执行并解析 SecretRef。Router 规划 Run 和成员任务 Run 都走此队列。API 轮询恢复 Run 终态并幂等推进 Orchestration/Task/Attempt/Artifact，不在 API 进程内同步调用持有凭据的 Runtime。

**技术栈：** Python/FastAPI、MySQL、现有 ObjectStore/RuntimeAdapter/EngineProvider、React/TypeScript、Vitest、pytest。

**规格依据：** `openspec/changes/agent-team-orchestration/design.md`、`openspec/changes/agent-team-orchestration/specs/`、`docs/原型/agent团队群聊.html`。

## 全局约束

- 查询和写入必须始终带 `platform_id` 与 `tenant_id`。
- 页面文案、状态、按钮和弹框以独立 HTML 原型为验收基线。
- 产物正文只能通过租户授权的 ObjectStore `read` 读取，数据库只保存受控对象引用和来源元数据。
- Worker/Runtime 未实际执行时不得伪报任务已领取、运行或完成。
- API 容器不注入 Engine Secret；所有模型调用必须经过受信 Worker 的 Run lease 与 fencing token。
- Router 计划生成与成员任务执行均为持久化异步阶段；HTTP 请求结束或 API 重启不能丢失待处理状态。
- schema 变更只能增加新的向前迁移；表与字段注释必须为中文。
- OpenSpec task 只在实现和验证均满足其描述后勾选。

## 风险验收输入

- 数据库重启或 API 进程重启：团队、对话绑定、计划、任务事件、产物元数据和交付状态必须可恢复。
- 跨租户猜测团队、任务、事件或 Artifact ID：响应按不存在处理，不能暴露数据。
- 一个团队含不同 EngineProvider：成员 Session 与任务执行必须使用配置对应引擎，不能静默改道。
- 团队缺 Router、Finalizer、成员部署失效或池不可用：发布/执行给出可操作错误并保留一致状态。
- Viewer 或只读身份：能查看原型允许的只读信息，不能保存配置、确认计划、取消任务或交付。

## 文件边界

- 修改 `backend/src/agent_platform/infrastructure/mysql_teams.py`：修正 TeamVersion 与成员的外键插入顺序和持久化读取。
- 新增 MySQL orchestration/artifact repositories，并在 `backend/src/agent_platform/api/app.py` 的 MySQL 模式注入；Fake 模式继续使用内存适配器。
- 修改 `backend/src/agent_platform/application/artifacts.py`：预览和打包时从 ObjectStore 受控读取正文。
- 修改 `backend/src/agent_platform/application/orchestration.py`、`backend/src/agent_platform/api/route_modules/teams.py`：计划、状态、活动和事件返回真实服务端数据；执行由可用 Worker/Runtime 报告，不由 UI 伪造。
- 修改 `frontend/src/features/platform/TeamWorkbench.tsx` 与 `frontend/src/api/client.ts`：实现原型中的新建、成员配置、发布、对话、提交、暂停/取消、计划确认、任务/事件弹框及产物交付。
- 修改 `docs/原型/agent团队群聊.html`、`docs/agent团队群聊设计.md` 和 OpenSpec tasks：只在实际发现原型本身缺少真实页面字段时同步修订，并记录实现映射。

## 任务

### 任务 1：团队配置到发布闭环

先为管理员创建草稿、添加员工部署成员、指定 Router/Finalizer、保存并发布编写失败测试；确认失败后实现并验证 API 和 MySQL 状态。前端按原型补齐配置窗口和发布按钮，验证只读身份无法修改。

### 任务 2：MySQL 状态仓储

为 Orchestration/Task/Dependency/Attempt/Event 与 Artifact/Delivery 写真实 MySQL 读写测试，验证分页、幂等、事务和租户条件；修复 Team Version 的成员外键写入顺序，接入 MySQL app factory。

### 任务 3：Run Worker 执行与计划恢复

Router 消息先创建 PLANNING Orchestration 和 QUEUED Run；输入通过仅成员可见的 SYSTEM Message 关联 Run，用户原文单独保存为 USER Message。Run Worker 使用 Session 固定的 EngineProvider/ModelRoute 和自身 SecretRef 执行，API 轮询 Run 并幂等恢复计划。计划确认后每个 Task 创建 QUEUED 子 Run 与 Attempt；Worker 完成后 API 将 Run 终态收敛到 Task、Artifact、群聊消息和事件。任何没有 Worker lease 的任务保持排队，不能显示为运行中。

为每个状态添加测试，覆盖重复提交、Worker 先后终态、API/Worker 重启、Attempt 关联、租户隔离及 Runtime 配置失败；不同 EngineProvider 使用同一 Worker 注册表路由到固定适配器，不能静默降级。

### 任务 4：产物保存与交付

产物字节由 ObjectStore 持久保存；MySQL 保存 Artifact/Delivery 索引。完成预览、打包、下载授权和幂等交付 API，页面操作带成功/错误反馈。

### 任务 5：React 工作台严格对原型

删去静态演示任务、成员、活动、事件、产物及虚假的运行状态；替换为团队、对话、编排、任务、事件、Worker Pool 与产物查询。逐项核对布局、颜色、间距、断点、字段、按钮、弹框、权限和状态。

### 任务 6：文档、测试与发布验证

更新中文设计与验收记录；执行后端相关及全量测试、前端相关及全量测试、前端构建、MySQL 集成验证、HTML parser、内嵌脚本语法检查、`openspec validate --strict` 与 `git diff --check`。在 compose 环境完成迁移、重建、重启和 API/UI 冒烟验证。
