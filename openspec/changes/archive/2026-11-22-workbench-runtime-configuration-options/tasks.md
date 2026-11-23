# Tasks

## 1. Deployment 运行配置持久化与 API

- [x] 1.1 新增 `docs/migrations/V009__deployment_runtime_config.sql` 和配套 down 文件：建 `model_route_bindings` 与 `deployment_runtime_config_versions` 两张表；分别列明 scope/route 和 Deployment/修订/config_mode/可空 EngineProfile、ModelRoute 覆盖、Workspace/Sandbox、审批/白名单参数/actor/time 字段的含义与租户归属；主键、唯一作用域键、同租户复合外键、INHERIT/OVERRIDE 与 ID/version-pair CHECK、索引逐项写入设计及中文 COMMENT。迁移顺序先 preflight，再从旧 Profile/EmployeeVersion 回填 Route scope bindings 和 Deployment OVERRIDE 修订 1，随后加 Deployment/Session 指针与来源字段，最后使 EmployeeVersion 运行字段可空并将 `execution_profiles.model_alias` 改为可空，字段 COMMENT 改为 `历史兼容模型别名；新 Session 模型由独立 ModelRoute 或运行时默认解析`。生产回滚保留 V009 数据和列，down 只在 disposable DB 验证；通过 MySQL `information_schema.TABLES`/`COLUMNS` 测试逐表逐列检查中文注释、租户键和回填。
- [x] 1.2 为现有 `execution_profile_bindings` 实现 EngineProfile 默认 Repository/API，并为新 `model_route_bindings` 实现 ModelRoute 默认 Repository/API；两个值独立写入、独立审计。Profile 默认只解析引擎和策略，Profile `model_alias` 在领域/API 中可空。验证各自的 User > Tenant > Platform/System、跨租户拒绝、成员权限和服务重启持久化，并更新中文接口文档；Session 使用 Route `model_name` 的验收归入 3.1。
- [x] 1.3 实现 Deployment `INHERIT/OVERRIDE` 配置 API 与并发修订校验；新 Deployment 在配置保存前保持 `PENDING_CONFIGURATION`；覆盖修订可只固定 EngineProfile、只固定 ModelRoute 或同时固定。Session 的双默认解析、来源快照和缺配置拒绝由 3.1 验收，V009 现有 ACTIVE/DISABLED 部署回填由 1.1 验收。未接入 Worker 的 Deployment 级 Workspace/Sandbox/附加审批非空写入必须返回 `ENGINE_CAPABILITY_UNSUPPORTED`，迁移历史配置若与精确 Profile 不一致也必须显式拒绝 Session；验证 MySQL 串行写入与不增加修订。
- [x] 1.4 补齐 ExecutionProfile 版本 API：从 PUBLISHED 版本创建同 ID 的下一 DRAFT，允许更新 DRAFT EngineProvider 并发布；禁止修改 PUBLISHED，更新时清除旧模型绑定以保持 ModelRoute 独立。当前 Worker 未接通的 Workspace/Sandbox/附加审批字段在创建、编辑或发布时拒绝新非空值；历史版本必须先清除。验证成员拒绝、跨租户隔离、DRAFT 可修改、发布后不可变和默认绑定继续固定旧版本，并覆盖 MySQL 重启读取。

## 2. 员工版本与资源中心可移植性

- [x] 2.1 修改员工包发布和 COPY/REFERENCE 导入：新包排除发布方 ExecutionProfile、ModelRoute、Workspace/Sandbox、审批配置和 SecretRef；导入旧版含 `execution_profile` 的包时忽略环境绑定并提示接收方配置。验证没有同模型/沙箱的接收租户仍可导入、跨租户资源无引用、Hub 摘要和授权行为保持有效，并更新资源中心中文说明。
- [x] 2.2 修改 EmployeeVersion 领域、接口和编辑器，允许版本只包含员工指令与 Skill/Tool 精确绑定；发布不要求本地 Profile/Route/Sandbox，版本 fork 不复制运行配置。验证发布、编辑、只读版本、REFERENCE 与 tenant isolation 测试，并同步员工字段说明和 API 文档。

## 3. Session、RunSnapshot 与 Worker

- [x] 3.1 新建 Session 时分别解析 EngineProfile 与 ModelRoute 的 Deployment override/用户/租户默认，优先使用独立 Route 的 `model_name` 填充运行上下文，并固定两个来源；旧 Session/Profile 通过既有固定列或 alias 兼容回退。验证只覆盖 Engine、只覆盖 ModelRoute、换引擎不改变已选模型、不同作用域组合、不兼容组合拒绝 Session、Run 幂等摘要和旧快照测试，并更新 Harness 执行设计。
- [x] 3.2 建立 EngineProvider 注册表与 capability/ModelRoute compatibility 校验；ModelRoute 选择保持独立，不因引擎变化而锁定或自动替换。EngineProvider 连字符别名在 Profile API、Session/RunSnapshot 与 Worker 选择时规范化为注册稳定 ID。DeepSeek Harness 和通过公共生命周期/隔离验证的 Codex adapter 可按 Profile 选择，Claude Code 保持未注册直到适配器通过验证。验证不支持模型/能力时拒绝 Session，不回退其他引擎，并记录每类 adapter 的验证边界。
- [x] 3.3 Worker 仅使用 RunSnapshot 的接收方 Profile/ModelRoute/引擎来源；缺失/撤销时明确失败，不回退发布方设置、latest Profile、其他租户凭据或 Fake。验证 SecretRef 脱敏、Worker 配置失败和 UNKNOWN 边界测试，并更新 Trace/错误说明。

## 4. 工作台、原型与中文文档

- [x] 4.1 在现有 `/enterprise/workbench` 当前员工旁增加紧凑“运行配置”入口，可分别继承或覆盖 EngineProfile/ModelRoute，并在 Run 快照显示两个 resolved source；更换 EngineProfile 不得自动改 ModelRoute；增加通往“模型管理 > 默认运行环境”的入口。验证角色、只覆盖引擎/只覆盖模型、A/B/C 配置、未启用 Claude adapter、缺配置、旧覆盖切换继承及不支持的 Workspace/Sandbox/审批字段提示和拒绝，并通过窄屏组件测试。
- [x] 4.2 更新 `docs/工作台运行配置方案.md` 并维护独立 `docs/原型/员工Deployment运行配置.html`，模拟截图中的连接卡片、模型目录与路由表，并在同一页签栏新增“默认运行环境”；展示独立 EngineProfile/ModelRoute 默认、Profile 不绑定新模型、同 ID 创建新版本流程和工作台跳转。验证静态演示、优先级、A/B/C 引擎/模型独立选择、不自动切换模型、旧 Session 快照、无真实请求/凭据、状态可操作且键盘和窄屏可读。
- [x] 4.3 在模型管理“默认运行环境”页展示同租户 Profile 版本目录；管理员可新建只配置 EngineProvider 的 Profile、从发布版本创建同 ID 下一版、编辑草稿并发布，个人/租户默认绑定可明确选择精确版本。Workspace/Sandbox/附加审批以不可用提示呈现。验证 profile 版本切换不改已有 Session 和未显式调整的默认值，普通成员不能写租户配置或 Profile。

## 5. 跨组件验收

- [x] 5.1 运行 `openspec validate workbench-runtime-configuration-options --strict`、后端全量测试、MySQL V009 migration/`information_schema` 集成测试、前端 `npm test -- --run` 与 `npm run build`；将真实验证结果和未验证项写入中文验证文档。
- [x] 5.2 在两个独立租户完成资源中心 COPY/REFERENCE 闭环：发布方模型/沙箱不传递；接收方分别配置 EngineProfile 和 ModelRoute 的租户/用户默认或 Deployment 覆盖；验证 A/Codex CLI + OpenAI ModelRoute、B/DeepSeek Harness + DeepSeek Route、引擎与路由不兼容拒绝、未启用 Claude Code 的明确拒绝；更新默认只影响新 Session，旧 Run 仍用旧快照。记录失败、拒绝、恢复和回滚结果。

## Workflow follow-up

- 完成实现、MySQL/前端验收并通过代码审查后，再同步主规格并归档该 OpenSpec Change。
