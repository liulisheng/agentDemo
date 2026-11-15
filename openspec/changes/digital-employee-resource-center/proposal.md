# 提案

## 为什么

平台已能通过持久化 API 创建并运行数字员工，但管理入口仍停留在只读 Gallery 和占位页面；Skill 文件包、员工下能力绑定及跨租户资源分发也缺少完整产品流程。现在需要建立以员工为入口的管理台和“资源中心”，让租户能够创建、发布、部署员工，并安全地复用其他租户发布的员工、Skill 和 Tool。

## 变更内容

- 建立数字员工管理台，提供员工基本信息、技能管理、工具管理、版本与发布、部署记录页面；技能和工具管理位于员工详情内。
- 员工详情的 Skill/Tool 添加和移除保持在对应类型 Tab，通过定向绑定接口仅修改该类型绑定；从已发布员工版本操作时先克隆完整配置到新 DRAFT。
- 支持从本地 PUBLISHED Skill 创建下一 DRAFT 版本，复制文件清单引用；Skill 文件工作区可发布当前精确 DRAFT Skill 版本。
- 扩展员工、Skill、Tool 的详情、版本查询和草稿编辑接口；已发布版本保持不可变，通过创建新版本进行修改。
- 为 Skill 增加 `INLINE` 和 `FILE_PACKAGE` 两种正文模式。文件包 ZIP 必须在根目录包含唯一 `SKILL.md` 入口；草稿支持文件树查看、在线创建/编辑/删除和 ZIP 导入预览。
- 建立平台级资源中心，允许租户发布不可变员工/Skill/Tool 资源包，并通过显式 Grant 允许其他租户 COPY 或 REFERENCE。
- COPY 导入形成接收租户自己的版本；REFERENCE 创建租户侧代理并固定 Hub 版本与摘要，Run 中只读。修改通过创建租户副本或新版本，不自动追随上游更新。
- 增加资源包/Grant/导入映射和 Skill 文件清单的 MySQL migration，SQL 统一放在 `docs/migrations/`；每个新增表和字段都添加中文 MySQL COMMENT，并用 `information_schema` 验证。
- 知识库页面、知识库导入和检索不纳入本 Change，后续单独规划。

## 能力

### 新增能力

- `digital-employee-management`: 管理租户员工基本信息、不可变版本、Skill/Tool 固定绑定、发布、部署和回滚。
- `tenant-resource-center`: 发布 Hub 资源包、配置租户授权，并以 COPY/REFERENCE 模式导入员工、Skill 和 Tool。
- `skill-file-packages`: 管理 Skill 的 Inline/File Package 正文、文件树、入口文件、上传校验和版本固定。

### 修改能力

无。当前没有正式的主规格目录；本 Change 通过新增能力规格定义管理台、资源中心和 Skill 文件包行为。

## 影响范围

- 前端：扩展 React 管理台与资源中心页面、员工详情下的技能/工具和文件包编辑交互。
- 后端：扩展 FastAPI Catalog API，增加 Hub 发布/授权/导入服务、Skill 文件服务和运行时只读资源解析。
- 数据库：新增资源中心版本/授权/导入记录，以及 Skill 文件清单；保持 `platform_id + tenant_id` 租户隔离和现有 V001/V002/V003 历史 migration 不变。
- 对象存储：继续使用 MySQL-backed LocalObjectStore 保存 ZIP/文件对象；本 Change 不增加 MinIO/S3/OSS/COS，也不在 Hub 中发布密钥明文。
