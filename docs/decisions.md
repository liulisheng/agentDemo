# Harness 工程技术决策

状态：基础 SDK、生命周期、工具插件、P4 控制面持久化，以及可选的非流式对话正文持久化已实现；流式恢复、清理 worker、生产对象存储/KMS 和服务器沙箱链路仍待实现或验证。更新日期：2026-11-13。下文保留 2026-11-09 的历史决策背景。

## 已由用户确认的方向

| 决策 | 结果 |
| --- | --- |
| Agent 底座 | 官方 DeepSeek Harness，而非仅调用 DeepSeek 模型 API |
| 使用入口 | Python SDK + CLI |
| 目标场景 | 通用工具型 Agent |
| 运行方式 | 本地托管引擎运行时 |
| 插拔范围 | 整体引擎与 Python 业务工具 |
| 会话 | 持久化并支持退出后恢复 |
| 关系型持久化 | 暂定 MySQL/InnoDB；不选择 SQLite |
| 对话内容 | MySQL 保存 tenant-scoped 消息/分段索引；加密对象存储保存正文；默认保留至显式删除 |
| 流式记录 | 按 32 KiB 或 2 秒（先到者）保存可见文本分段；断线按 checkpoint 续播；进程退出恢复已保存部分，不自动重发 run |
| 模型推理文本 | 不保存原始内部 reasoning/chain-of-thought；可审计工具动作、审批决定和状态元数据 |
| 当前交付范围 | 按用户批准的计划实现 V002/doctor、加密 ContentStore 和消息仓储；流式恢复与删除 worker 留在后续阶段 |
| 用户中心参考 | `D:\GitHub\dadi\dadi-user-center` 的平台/租户/用户/组织/RBAC/OIDC 模式 |

## 设计默认值

1. **官方 SDK 优先**：封装现有 Python SDK，不维护自研 JSON-RPC 桥。经文档核实，普通 SDK 使用的是打包运行时，不要求系统 Node.js；此前关于必须安装系统 Node.js 的判断已修正。
2. **同步公共接口**：与上游调用方式对齐，内部用工作线程处理交互。异步公共 API 后续按实际需求增加。
3. **协议隔离**：业务只依赖 core；引擎特有对象留在适配层。引擎选择和模型选择分离。
4. **entry points 插件**：使用标准 Python 包发现方式，配合显式启用白名单；不做任意目录扫描与热加载。
5. **MCP 工具桥接**：Python 工具通过本地 stdio 服务供官方 Harness 调用，避免业务插件依赖 TypeScript。
6. **MySQL/InnoDB 持久化（暂定）**：作为所有环境统一的关系型数据库，保存多租户控制面状态、授权关联、会话/消息索引、运行租约和审计元数据。租户数据通过 tenant_id、复合约束、应用授权及越权测试保护。用户消息、助手回复、Harness 原生历史与工作区文件放在租户隔离的加密持久存储中；数据库只保存消息元数据和不透明引用。
7. **同引擎恢复**：不把不同 Harness 的会话格式视为可互换；替换引擎后的新任务使用新会话。
8. **安全失败**：高风险动作无审批处理器则拒绝；不自动重放不确定任务；本地进程不是强隔离沙箱。
9. **离线优先验证**：Fake 和契约测试覆盖框架行为，真实集成另行显式运行。
10. **轻量工程化**：Python 3.11+、uv、Pydantic、pytest、Ruff、mypy；不引入复杂 DI 框架和不必要的抽象层。
11. **开发环境也使用 MySQL**：使用本地容器或专用开发实例，避免生产和开发数据库语义分叉；不增加 SQLite 备用实现。
12. **身份中心职责外置**：Harness 工程不自行实现密码、OIDC Provider 或 token 签发；参考 dadi 的服务端会话和平台/租户绑定模式，通过 OIDC 或受信内部身份解析接入。

以上是实施默认选择，不表示相应功能已经通过测试。

## 为什么最初提议 SQLite，以及为什么现在不选

最初的方案按本地单机 SDK 做会话索引，因此 SQLite 看起来合适：

- 无需独立数据库服务，安装和启动简单。
- 单文件便于本机开发、备份和测试。
- 对单用户、单机、低并发的元数据索引足够。
- 早期只保存少量会话定位信息，曾预期完整历史继续由 Harness 管理。

这些理由只适用于本地原型，不适用于用户后来明确的服务器多租户、不可信任务场景。SQLite 不适合作为该多实例服务的中心化持久化数据库，单文件也增加共享部署、权限隔离和高可用管理负担。因此撤回 SQLite 方案，开发、测试和服务器统一使用 MySQL/InnoDB。

## 当前数据库选择：MySQL/InnoDB（暂定）

MySQL/InnoDB 满足本项目当前的事务、行级锁、会话元数据和多实例调度需求，也与用户当前选择一致。所有租户数据查询必须由应用从认证上下文取得 tenant_id 并显式过滤；表使用 tenant_id 复合唯一约束、外键和索引。MySQL 方案当前不依赖数据库自动行级策略，因此仓储层接口和跨租户越权测试要作为重点。

开发、测试和部署统一用 MySQL；在开发环境使用 MySQL 容器或专用实例，不用 SQLite 模拟。租户隔离仍需要应用层正确实现，使用 MySQL 并不自动消除漏写 tenant_id 条件的风险。

### 曾评估但当前未采用：PostgreSQL RLS

为说明此前选 PostgreSQL 的考虑，下面保留 RLS 的概念示例。它只描述 PostgreSQL 的行级策略，不是当前 MySQL 方案的实现。

### 大白话解释：RLS 怎么防止租户数据串在一起

假设数据库有一张 `sessions` 表，里面同时存着甲公司和乙公司的会话：

| session_id | tenant_id | title |
| --- | --- | --- |
| s-101 | tenant-a | 甲公司的项目 |
| s-202 | tenant-b | 乙公司的项目 |

应用正常应该这样查甲公司的会话：

```sql
SELECT * FROM sessions WHERE tenant_id = 'tenant-a';
```

如果开发人员写查询时忘了 `WHERE tenant_id = ...`，普通数据库会把两家公司的记录都返回。RLS 可以在 `sessions` 表上再设一道规则：“当前请求的租户只能看见 tenant_id 等于自己的行”。这样即使应用漏写过滤条件，数据库也会自动把乙公司的行挡掉；如果某张表启用了 RLS 却没有允许当前角色访问的策略，默认就是一行也不给看。

写入和修改也要设规则：读规则控制哪些旧记录可见，写入检查控制新记录或修改后的记录必须属于当前租户。这样应用不能把甲公司的会话改成乙公司所有，也不能给甲公司的请求插入一条归属乙公司的记录。

大致可理解为数据库为表加了自动门禁：应用先通过登录信息认出“当前用户属于甲公司”，在数据库事务内设置本次请求的租户上下文；数据库每次读写都用这个上下文核对行的 `tenant_id`。租户 ID 必须由服务端认证结果设置，不能直接相信调用者在请求内容里自报的值。

以下是 PostgreSQL 伪 SQL 示意；它不适用于当前选择的 MySQL：

```sql
ALTER TABLE sessions ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_sessions ON sessions
  USING (tenant_id = current_setting('app.tenant_id')::text)
  WITH CHECK (tenant_id = current_setting('app.tenant_id')::text);
```

PostgreSQL 应用通常在一个事务开始时设置 `app.tenant_id`，事务结束即清理。连接池若留下旧上下文，后一个请求可能继承前一个请求的身份。RLS 还要避免用可以绕过策略的数据库角色。它只能减少漏过滤错误的影响，不能代替认证、应用授权和越权测试。这些是未采用方案的注意事项。

### 大白话解释：JSONB 用来放什么

不同引擎的能力或配置字段可能不一样。固定、常查、必须校验的身份和状态仍放在普通列中，例如 `tenant_id`、`session_id`、`engine_name`、`status`；少量随引擎变化的附加信息可以放在 MySQL JSON 列中，例如：

```json
{
  "supports_resume": true,
  "tool_protocol": "mcp",
  "provider_options": {"reasoning_effort": "high"}
}
```

这样新增一种引擎能力时，不一定每次都要给所有表加新列。MySQL 可以查询 JSON 字段；如果某个 JSON 子字段后来需要高频过滤，可按需要建立生成列和索引。PostgreSQL JSONB 在 JSON 查询和索引方面有不同能力，但本项目当前元数据用量不需要因此更换数据库。JSON 列不是“什么都塞进去”的理由：租户归属、权限、会话状态、外键关系和需要强约束的字段必须保留为明确列及数据库约束；JSON 内容还要由应用按引擎配置模型校验，不能因为格式灵活就跳过校验。

## 实施前必须验证的事项

- 官方已发布版本的可安装性、精确版本组合及目标平台支持。
- 官方取消 API 和运行结束事件的对应关系；不能只依靠杀进程模拟正常取消。
- 默认 profile 的原生工具范围、审批配置和 MCP 的具体配置格式。
- MCP 服务所需环境变量最小集合，防止无关凭据传给工具进程。
- 恢复失败和历史格式变更的行为，以及版本升级时的兼容性边界。
- Windows 下 Ctrl+C、子进程退出和跨进程锁的实际行为。

发现上游能力缺失时应记录证据并调整设计，不虚构 API、不改用单纯模型调用冒充 Harness 接入。

## 明确推迟

补充决策（2026-11-09）：用户确认目标部署包含服务器上多个用户的不可信任务。当前优先建设 Docker/Kubernetes 沙箱后端，要求受限运行时、资源限制、默认拒绝网络和节点隔离；microVM 后端暂不纳入本阶段。可先交付本地 SDK/CLI 基础阶段，之后建设服务器执行面；MySQL 和 tenant_id 数据边界从第一版 schema 起保持一致。P4 控制面实现状态见[实施计划](implementation-plan.md)和[真实验证矩阵](verification-matrix.md)。

HTTP 服务、Web UI、服务器分布式执行面、多租户调度实现、插件热加载、跨引擎历史迁移、第二款生产引擎和 microVM 后端实现不纳入本地 SDK/CLI 基础阶段；当前服务器执行面优先验证 Docker/Kubernetes。

数据库依据：[MySQL InnoDB 事务模型](https://dev.mysql.com/doc/refman/8.4/en/innodb-transaction-model.html)、[MySQL 锁定读与 SKIP LOCKED](https://dev.mysql.com/doc/refman/8.4/en/innodb-locking-reads.html)、[MySQL JSON 列索引](https://dev.mysql.com/doc/refman/8.4/en/create-table-secondary-indexes.html)。此前评估的 RLS 资料：[PostgreSQL Row Security Policies](https://www.postgresql.org/docs/17/ddl-rowsecurity.html)。

## 变更规则

替换公共接口、放宽权限、改变会话所有权或增加部署依赖时，先更新此决策记录及架构文档，再实施；普通内部重构不必新增决策。

关联：[架构设计](design.md)、[实施计划](implementation-plan.md)。
