# Tasks

## 1. Resource Center 数据模型与发布

- [x] 1.1 新增 `docs/migrations/V004__resource_center.sql`，创建 Hub 资源、不可变版本、依赖、Grant 和租户导入映射表，并为 COPY Tool 接收方凭据要求增加中文注释字段；验证平台/租户复合约束和所有新增/变更字段 COMMENT，执行 `information_schema` 正向/回滚集成测试。
- [x] 1.2 定义 Hub Package manifest 的规范化编码、依赖顺序和 SHA-256 计算；验证同一资源版本稳定产生同一摘要，Secret 明文和发布方 SecretRef 不进入 manifest。
- [x] 1.3 实现平台范围 Hub Repository 与资源可见性/Grant 校验；验证无权租户不能读取或导入资源、平台可见与租户 allowlist 规则生效，并同步中文 Hub 模型文档。
- [x] 1.4 实现租户管理员发布本地已发布 Employee/Skill/Tool 版本到 Hub 的服务和 API；验证只有已发布版本能打包、依赖版本准确、已发布 Hub 包不能原地修改。
- [x] 1.5 实现 Hub 资源目录和详情 API（按类型、关键词、可见范围、可用模式查询）；验证响应不会泄露发布租户内部 ID、密钥或未授权依赖。

## 2. COPY/REFERENCE 导入和运行解析

- [x] 2.1 实现 COPY 导入：递归校验依赖、生成接收租户本地资源 ID/版本、复制允许的内容并记录来源；验证源版本后续更新不改变副本，Tool SecretRef 要由接收租户重新配置。
- [x] 2.2 实现 REFERENCE 租户代理和授权映射；验证员工绑定仍使用租户本地资源 ID、数据库查询不直接跨租户读取、撤销 Grant 阻止新的导入。
- [x] 2.3 实现 Hub ResourceResolver，将精确 Hub 版本、manifest 摘要和来源模式固化进 RunSnapshot；验证 Run 读取引用只读、Hub 新版本不改变既有员工版本或 Run。
- [x] 2.4 增加 Hub 导入列表、显式升级和撤销 API；升级必须创建新的租户代理/资源版本而不覆盖原绑定，并同步中文 API/状态文档。

## 3. Skill 文件包模型与后端

- [x] 3.1 新增 `docs/migrations/V005__skill_file_packages.sql`，为 Skill 增加 INLINE/FILE_PACKAGE 模式、入口路径和清单摘要，并创建 `skill_file_entries`；所有新/变更字段带中文 COMMENT，外键保持 `platform_id + tenant_id`，验证 `information_schema` 和 disposable DB down migration。
- [x] 3.2 扩展 Skill Domain/Repository 支持 INLINE 与 FILE_PACKAGE；FILE_PACKAGE 必须有根目录唯一 `SKILL.md`，INLINE 继续兼容既有 `instruction`；为状态和入口规则添加领域测试并更新 Skill 数据模型文档。
- [x] 3.3 实现 ZIP staging 导入、清单预览和原子提交；验证压缩/展开大小、文件数、单文件大小、路径穿越、绝对路径、重复规范化路径、符号链接及缺少根 `SKILL.md` 都会拒绝整个包且不提交半套清单。
- [x] 3.4 实现 DRAFT 文件树查询、新建、读取、更新、重命名、删除和 ZIP replace/merge API；更新使用 `expected_sha256` 乐观并发校验，验证已发布版本和 Hub REFERENCE 全部只读。
- [x] 3.5 将 FILE_PACKAGE 入口和辅助文件摘要/只读引用接入 RuntimeRequest 与 RunSnapshot；验证 Fake Runtime 收到固定入口和文件 refs、不执行包内脚本，Trace 记录文件清单摘要。
- [x] 3.6 实现 Hub Skill 文件 COPY/REFERENCE；COPY 创建租户 MediaAsset 与文件清单，REFERENCE 通过 Grant 解析只读 Hub manifest，GC 保护仍被 Skill 版本引用的对象；添加 MySQL/LocalObjectStore 集成测试和运维说明。

## 4. 员工管理后端 API

- [x] 4.1 增加员工详情、版本列表/详情、搜索/状态筛选/游标分页 API；验证查询使用稳定排序、分页不跨租户且旧列表响应仍包含 `items`。
- [x] 4.2 增加员工基本信息 PATCH、草稿版本 PUT、归档 API 和租户管理员写权限；验证成员只读、跨租户按 404、PUBLISHED 版本返回 `IMMUTABLE_VERSION`。
- [x] 4.3 让员工 DRAFT 版本原子保存 Skill/Tool/Hub 绑定及执行配置；验证发布预检逐项返回未发布依赖、Grant 撤销、摘要不匹配和 SecretRef 缺失错误。
- [x] 4.4 扩展 Deployment 列表筛选、版本摘要和审计事件查询；验证部署/回滚只选 PUBLISHED 版本，回滚后既有 RunSnapshot 不变。
- [x] 4.5 更新中文 API/状态说明，包含请求/响应示例、错误码、权限矩阵、Hub COPY/REFERENCE 和文件上传时序；验证每个路径关联到路由测试。

## 5. 前端管理台与资源中心

- [x] 5.1 建立“数字员工 / 资源中心 / 部署记录”页面壳和租户上下文；资源目录负责 Skill/Tool 生命周期，员工详情负责精确版本绑定，验证路由和导航测试。
- [x] 5.2 实现员工列表、新建、详情、基本信息编辑和归档；覆盖搜索/状态、加载/空/错误/权限状态，并验证前端测试与 API client 契约。
- [x] 5.3 实现员工 DRAFT 版本向导：系统指令、Skill、Tool、ExecutionProfile/ModelRoute、Workspace/Sandbox、审批策略和有效 Tool 交集预览；验证保存/离页提醒/版本测试。
- [x] 5.4 实现 Skill 文件工作区：文件树、在线创建/编辑/删除、上传 ZIP 预览、replace/merge、大小/路径错误和只读来源提示；验证 FILE_PACKAGE 根 `SKILL.md` 唯一入口和前端交互测试。
- [x] 5.5 实现版本时间线、依赖预检、发布确认、部署版本选择和回滚确认；验证已发布版本不可编辑、引用资源展示固定版本/摘要、回滚结果更新。
- [x] 5.6 实现资源中心“发现资源 / 我发布的 / 我导入的”页面、详情、权限/依赖展示、发布设置、COPY/REFERENCE 导入和显式升级；验证未授权资源不可操作，REFERENCE 无编辑按钮。
- [x] 5.7 更新前端 API 类型、Query Cache key 和状态提示，保证租户切换时不复用其他租户缓存；验证组件测试、`npm test`、`npm run build`，并更新中文前端交互原型文档。
- [x] 5.8 补齐员工上下文 Tool 管理页：管理员创建/编辑 DRAFT、查看 Schema 并发布，成员只读；验证 Tool API 权限和前端创建/发布测试。
- [x] 5.9 在员工 Skills Tab 提供独立 Skill DRAFT 创建入口，创建后进入 Skill 文件工作区，发布与员工绑定分开完成；验证创建和租户缓存更新。
- [x] 5.10 将旧 Gallery 与根路径重定向到新管理台/资源中心，移除“开放广场平台”旧页面入口；验证旧 URL、新首页和 Nginx Docker 路由。
- [x] 5.11 将员工详情改为概览、Skills、Tools、版本与发布独立 Tab；扩展版本编辑器留白并收起高级配置；验证类型隔离、响应式布局和现有管理交互。
- [x] 5.12 将 FastAPI handler 按员工/Catalog、部署执行、资源中心、Skill 文件和 Conversation 分模块；保持 `/api/v1` URL、method、Schema 和权限不变，验证路径清单及 MySQL/离线测试。
- [x] 5.13 在资源中心集中管理本租户 Skill/Tool DRAFT 与已发布版本，按用途分组创建表单并从 Skill 目录进入精确版本文件工作区；为窄屏导航、键盘焦点和 API 状态提示补充中文交互文档，验证前端生产构建。

## 6. 端到端验收与交付记录

- [x] 6.1 增加 MySQL + LocalObjectStore API E2E：租户 A 发布 Skill ZIP/Tool/员工包，租户 B COPY 和 REFERENCE，创建员工版本、发布、部署、Run 并读取 Trace；验证 Run 固定来源模式/版本/摘要。
- [x] 6.2 增加安全与失败集成测试：Hub 无授权、Grant 撤销、ZIP 恶意路径、上传失败回滚、SecretRef 脱敏、租户隔离、引用只读和 GC 保护；验证所有 MySQL 表/字段中文 COMMENT。
- [x] 6.3 运行 backend MySQL/离线全量测试、Ruff、compileall、frontend test/build 和 OpenSpec strict validation；记录 MySQL 版本、migration checksum、命令、通过/跳过/未验证项和日志路径。

## 7. 类型定向编辑与不可变版本分叉

- [x] 7.1 增加员工版本 fork API，完整复制来源 PUBLISHED 版本的系统指令、精确 Skill/Tool 绑定、ExecutionProfile/ModelRoute、Workspace/Sandbox 和审批策略；覆盖租户隔离、成员拒绝、源版本不可编辑和并发版本号冲突。
- [x] 7.2 增加 Skill/Tool 类型专属绑定 API 和仓储事务，仅替换目标类型绑定表及同类型 capability slot；覆盖保留另一类型绑定、系统指令、运行配置、PUBLISHED 只读、成员 403、跨租户 404 和重复引用校验。
- [x] 7.3 增加本地 PUBLISHED Skill 版本 fork 与精确版本 publish API；新版本复制 Skill 元数据、Tool 依赖和不可变 MediaAsset 文件引用，Hub REFERENCE 必须拒绝直接 fork；覆盖 `SKILL.md`/manifest 摘要和旧版本不变。
- [x] 7.4 员工 Skills/Tools Tab 增加类型专属选择抽屉与移除交互；Skills 抽屉可独立创建 Skill DRAFT 并进入 Skill 文件工作区；PUBLISHED 员工选择资源时先 fork 完整 DRAFT，操作后留在原 Tab，前端只调用相应绑定 API，不跳进全量版本编辑器。
- [x] 7.5 Skill 文件工作区对 PUBLISHED 本地 Skill 显示创建草稿版本入口，对 DRAFT 显示精确版本发布入口；COPY/REFERENCE 只读边界、入口文件和发布失败信息可见。
- [x] 7.6 更新中文 API、数据流程和状态文档，运行后端/前端测试、Ruff、build、OpenSpec strict validation，并在 Docker 验证从资源添加到新员工草稿及 Skill 文件发布的闭环。

## Workflow follow-up

- 知识库实体、文件索引、检索和 Hub COPY/REFERENCE 作为后续 Change；本 Change 不创建知识库页面或查询 API。
- MinIO/S3/OSS/COS、真实 Runtime、生产身份提供方和生产密钥管理通过独立 Change 验收。
