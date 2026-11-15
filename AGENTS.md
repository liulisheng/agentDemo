# 项目协作约定

## 语言要求

- Commit message、commit body、Pull Request 标题和描述统一使用中文。
- 代码注释、公共类/函数文档字符串和复杂逻辑说明使用中文。
- API 字段、类名、函数名和目录名保持清晰稳定的工程命名；解释性文字使用中文。
- 用户可见的开发说明、设计文档、流程图和验收说明使用中文。

## 提交要求

Commit message 使用简短的类型前缀和中文内容，例如：

```text
feat: 搭建 Agent 平台首批控制面骨架
fix: 修复租户员工列表过滤错误
docs: 补充后端与 Harness 引擎关系设计
refactor: 拆分 RuntimeAdapter 和业务控制面
```

Commit body 必须说明：

```text
变更内容：
- 做了什么

影响范围：
- 哪些模块或接口发生变化

验证结果：
- 执行了哪些测试、构建或校验

风险和后续：
- 已知限制、未完成项或下一步
```

提交前必须确认：

1. 不包含本地密钥、虚拟环境、缓存和 REA/OpenSpec 本机配置。
2. 代码变更有对应测试或明确的只读文档说明。
3. 设计、流程、接口和状态变化已经同步到中文文档。
4. OpenSpec 任务和规格已经反映本次变更。

## 代码说明要求

- 公共模块、类、端口、适配器、Worker 和 API 路由必须有中文用途说明。
- 复杂的状态转换、租户隔离、Runtime 边界、重试和错误处理必须有中文注释。
- 注释说明“为什么这样做”和“边界是什么”，不重复代码本身的字面含义。
- 不复制 StaffDeck 源代码、静态资源、样式或私有模块；只参考经过记录的行为和领域关系。

## 文档要求

项目级文档放在 `docs/`，使用中文文件名。每个功能变更至少需要：

- 架构图
- 流程图或时序图
- 数据模型说明
- API 或状态说明
- 已验证项和未验证项
- 验证命令

OpenSpec CLI 固定的 `proposal.md`、`design.md`、`tasks.md` 和 `spec.md` 文件名保持不变。

## SQL Schema 约定

- 所有可执行 MySQL schema 和 migration SQL 统一放在 `docs/migrations/`，不得另建重复的 SQL 来源目录。
- 新建或变更的表必须有中文表级 `COMMENT`；新建或变更的每个字段必须有准确的中文字段 `COMMENT`，说明该字段存储什么以及必要的边界。
- 已应用的历史 migration 不得为补注释而改写；需要通过新的向前 migration 更新 schema 元数据，并保持原 migration 校验和。
- MySQL 集成测试必须通过 `information_schema.TABLES` 和 `information_schema.COLUMNS` 验证本次新增或变更对象的中文注释。
- 表名、字段名、索引名和 SQL 标识符使用稳定的工程命名；中文说明写入 COMMENT，不用中文标识符替代。
