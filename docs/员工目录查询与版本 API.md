# 员工目录查询与版本 API

## 架构

```mermaid
flowchart LR
    A[Fake identity / 租户用户] --> B[FastAPI 路由]
    B --> C[CatalogService]
    C --> D[(CatalogRepository)]
    D -->|platform_id + tenant_id| E[(MySQL digital_employees)]
    D -->|platform_id + tenant_id + employee_id| F[(MySQL digital_employee_versions)]
```

员工目录查询从认证上下文取租户，不接受查询参数覆盖租户。详情、版本清单和版本详情均通过 Repository 的平台/租户复合条件读取；列表分页基于确定的名称和 ID 排序键。

```mermaid
sequenceDiagram
    participant UI as 员工列表
    participant API as Digital Employee API
    participant Catalog as CatalogService
    participant DB as MySQL Catalog
    UI->>API: GET /digital-employees?keyword&status&limit
    API->>Catalog: list_employees_page(identity, filters, cursor)
    Catalog->>DB: 当前平台/租户范围读取员工
    DB-->>Catalog: 按名称和 ID 排序的筛选结果
    Catalog-->>API: items + next_cursor
    API-->>UI: 员工摘要和下一页游标
    UI->>API: GET /digital-employees/{id}/versions
    API->>Catalog: list_employee_versions_page(identity, id, status, cursor)
    Catalog->>DB: 验证员工租户并读取版本
    DB-->>API: 版本号倒序页和绑定摘要
```

## 数据模型

| 模型 | 作用 | 本次查询使用字段 |
|---|---|---|
| `DigitalEmployee` | 员工稳定身份和基本资料 | `employee_id`, `tenant_id`, `name`, `role_name`, `description`, `status` |
| `DigitalEmployeeVersion` | 不可变执行版本 | `version`, `status`, `system_prompt`, exact Skill/Tool version refs, ExecutionProfile version |
| `Deployment` | 固定一个已发布版本 | 员工详情只查询版本，不自动创建或切换 Deployment |

员工版本响应保留兼容的 `skill_ids`/`tool_ids` 数组，并增加 `skill_version_refs`/`tool_version_refs`，包含 ID 和绑定版本，避免客户端用“当前最新”猜依赖。

## API 与游标约定

以下路径均以 `/api/v1` 为前缀。身份通过 `X-Fake-User` 解析；请求租户范围始终来自身份上下文。

后端路由按功能拆分，`backend/src/agent_platform/api/routes.py` 只负责装配 `/api/v1` 聚合 Router：

| 路由模块 | 职责 |
|---|---|
| `api/route_modules/catalog.py` | 员工、Skill、Tool、ExecutionProfile、ModelRoute |
| `api/route_modules/deployments.py` | Deployment、审计、Session、Run、Trace、Usage |
| `api/route_modules/resource_center.py` | Hub 目录、发布、Grant、COPY/REFERENCE 导入 |
| `api/route_modules/skill_files.py` | Skill 文件树、在线编辑、ZIP Preview/Commit |
| `api/route_modules/conversations.py` | Conversation、消息、路由和投递 |
| `api/route_modules/common.py` / `serializers.py` | 身份/租户依赖与共享响应序列化 |

拆分只改变代码组织；路径、HTTP method、请求/响应和权限契约不变，应用入口仍使用 `from agent_platform.api.routes import router`。

后端路由按功能组织，`backend/src/agent_platform/api/routes.py` 只负责创建 `/api/v1` 聚合 Router：

| 模块 | 接口职责 |
|---|---|
| `api/route_modules/catalog.py` | 员工、Skill、Tool、ExecutionProfile、ModelRoute |
| `api/route_modules/deployments.py` | Deployment、审计、Session、Run、Trace、Usage |
| `api/route_modules/resource_center.py` | Hub 目录、发布、Grant、COPY/REFERENCE 导入 |
| `api/route_modules/skill_files.py` | Skill 文件树、在线编辑、ZIP Preview/Commit |
| `api/route_modules/conversations.py` | Conversation、消息、路由和投递 |
| `api/route_modules/common.py` / `serializers.py` | 身份/租户依赖与跨模块响应序列化 |

拆分只改变代码组织；`app.py` 仍从 `agent_platform.api.routes` 挂载聚合 Router，HTTP 路径、method 和响应契约保持稳定。

| 方法与路径 | 查询/请求 | 返回 |
|---|---|---|
| `GET /digital-employees` | `keyword`、`status`、`limit`（1–200，默认 50）、`cursor` | `{items, next_cursor}`，条目保留旧字段并含最新发布版本资源计数 |
| `GET /digital-employees/{employee_id}` | 无 | 员工基本信息、版本数、最新发布版本摘要 |
| `GET /digital-employees/{employee_id}/versions` | `status`、`limit`、`cursor` | `{items, next_cursor}`，版本号倒序 |
| `GET /digital-employees/{employee_id}/versions/{version}` | 精确版本号 | 系统指令、状态、策略、Skill/Tool 精确版本绑定 |
| `POST /digital-employees` | `name`、`role_name`、`description` | 创建当前租户员工，返回基础资料 |
| `POST /digital-employees/{employee_id}/versions` | 创建初始 DRAFT 版本的执行配置 | 返回版本号和 DRAFT 状态 |
| `PATCH /digital-employees/{employee_id}` | 至少包含 `name`、`role_name`、`description` 之一 | 部分更新稳定资料；省略字段保持不变 |
| `PUT /digital-employees/{employee_id}/versions/{version}` | 完整 DRAFT 指令、精确 Skill/Tool refs、执行配置 | 原子替换该 DRAFT 版本及绑定 |
| `POST /digital-employees/{employee_id}/versions/{version}/test` | `{ "prompt": "..." }` | 运行当前已保存版本的非持久化 Fake/Runtime 预览 |
| `POST /digital-employees/{employee_id}/versions/{version}/publish` | 无 | 预检通过后将 DRAFT 固定为 PUBLISHED |
| `POST /digital-employees/{employee_id}/archive` | 无 | 设置员工状态为 `ARCHIVED`，保留历史数据 |
| `GET /tools` | 无 | 当前租户 Tool 清单，含状态、输入 Schema 和凭据是否配置的布尔值 |
| `POST /tools` | `name`、`description`、`input_schema`、可选协议 | 管理员创建 DRAFT Tool |
| `PATCH /tools/{tool_id}` | DRAFT 的 `name`、`description`、`input_schema` 部分字段 | 管理员更新 DRAFT Tool；PUBLISHED 返回 `409 IMMUTABLE_VERSION` |
| `POST /tools/{tool_id}/publish` | 无 | 管理员发布 Tool；需要凭据的 Tool 必须先配置 SecretRef |
| `GET /skills` | 无 | 当前租户 Skill 清单和精确版本 |
| `GET /skills?include_versions=true` | 无 | 返回所有精确 Skill 版本，供绑定选择器保留历史 PUBLISHED 版本 |
| `POST /skills` | `name`、`skill_type`、`instruction`、`allowed_tool_ids` | 管理员创建 DRAFT Skill |
| `POST /skills/{skill_id}/publish` | 无 | 管理员发布 Skill；其声明的 Tool 必须已发布 |
| `POST /skills/{skill_id}/versions/{version}/fork` | 无 | 从本地 PUBLISHED Skill 创建下一 DRAFT，复制 Tool 声明和文件清单引用；Hub REFERENCE 返回 409 |
| `POST /skills/{skill_id}/versions/{version}/publish` | 无 | 校验并发布精确 Skill DRAFT 版本 |
| `POST /digital-employees/{employee_id}/versions/{source_version}/fork` | 无 | 从 PUBLISHED 员工版本克隆完整配置和绑定到下一 DRAFT |
| `PUT /digital-employees/{employee_id}/versions/{version}/skill-bindings` | `{ "skill_version_refs": [{"resource_id":"skill-1","version":2}] }` | 仅替换 DRAFT Skill 绑定与 Skill capability slot |
| `PUT /digital-employees/{employee_id}/versions/{version}/tool-bindings` | `{ "tool_version_refs": [{"resource_id":"tool-1","version":1}] }` | 仅替换 DRAFT Tool 绑定与 Tool capability slot |

### 类型定向编辑流程

```mermaid
sequenceDiagram
    participant UI as 员工 Skills/Tools Tab
    participant API as Catalog API
    participant App as CatalogService
    participant DB as MySQL Catalog
    UI->>API: PUBLISHED 源版本时 POST fork
    API->>App: 校验管理员、租户和源版本状态
    App->>DB: 锁员工行，克隆完整配置和绑定
    DB-->>UI: 新 DRAFT 精确快照
    UI->>API: PUT skill-bindings 或 tool-bindings
    API->>App: 校验 DRAFT 和精确已发布资源
    App->>DB: 单事务替换对应绑定表及同类型 capability slot
    DB-->>UI: 当前完整版本；前端仍显示原类型 Tab
```

员工 fork 复制 `system_prompt`、精确 Skill/Tool refs、ExecutionProfile/ModelRoute、Workspace/Sandbox 和审批策略。Skill/Tool 绑定 API 的请求体没有这些字段，只能替换对应类型的绑定行；PUBLISHED 目标版本 409、成员写入 403、跨租户资源 404。Skill 文件工作区的 fork/publish API 按精确版本操作；新版本复用同租户不可变 MediaAsset ID 与 manifest 摘要，REFERENCE 必须先 COPY。此变更复用 V003 的 `digital_employee_versions`、`employee_skill_bindings`、`employee_tool_bindings`、`capability_bindings` 和 V005 的 Skill 文件表，不新增 migration。

`GET /skills/{skill_id}/versions/{version}/files` 响应包含 `source_mode` 和 `fork_allowed`：本地 PUBLISHED 版本可 fork，Hub REFERENCE 显示只读及“先 COPY”说明；成员即使读取到 DRAFT 文件清单，界面也只显示只读视图。

DRAFT 版本 PUT 请求示例：

```json
{
  "system_prompt": "按租户政策处理工单。",
  "skill_version_refs": [{"resource_id": "01...", "version": 2}],
  "tool_version_refs": [{"resource_id": "01...", "version": 1}],
  "execution_profile_id": "01...",
  "execution_profile_version": 1,
  "model_route_id": "01...",
  "workspace_profile_id": null,
  "sandbox_profile_id": null,
  "approval_policy": "required-for-write"
}
```

PUT 将系统指令、Skill/Tool 精确版本引用和 ModelRoute/Workspace/Sandbox/审批设置作为一个版本替换，并在 MySQL 一个事务中保存版本正文、Skill 绑定、Tool 绑定和 Capability 绑定。Repository 行锁和状态检查防止写入 PUBLISHED 版本。

成功保存响应为该 DRAFT 的完整固定版本；客户端应使用响应的资源 refs 展示状态，不把旧数组 `skill_ids`/`tool_ids` 解释成“绑定最新版本”：

```json
{
  "employee_id": "emp-123",
  "version": 2,
  "system_prompt": "按租户政策处理工单。",
  "skill_version_refs": [{"skill_id": "skill-1", "version": 2}],
  "tool_version_refs": [{"tool_id": "tool-1", "version": 1}],
  "execution_profile_id": "profile-1",
  "execution_profile_version": 1,
  "model_route_id": "route-1",
  "workspace_profile_id": null,
  "workspace_profile_version": null,
  "sandbox_profile_id": null,
  "sandbox_profile_version": null,
  "approval_policy": "required-for-write",
  "status": "DRAFT"
}
```

PATCH 请求示例 `{"tenant_id":"tenant-a","description":"处理售后工单"}` 返回更新后的员工基本信息；没有提供的字段不变。归档响应返回员工状态 `ARCHIVED`。以上 API 不返回其他租户的 ID、SecretRef 明文或对象存储地址。

版本测试执行前重用 AgentResolver 校验员工 Skill/Tool、Hub Grant/摘要和 FILE_PACKAGE 摘要，创建仅存在于本次调用中的 Deployment 视图并直接调用 Runtime。它不写入 `deployments`、`sessions`、`runs`、`Trace` 或 Usage Ledger；请求只能由租户管理员执行。

```mermaid
sequenceDiagram
    participant UI as 员工版本编辑器
    participant API as Employee API
    participant Catalog as CatalogService
    participant DB as MySQL Catalog
    UI->>API: PUT DRAFT 内容和精确资源 refs
    API->>Catalog: 检查管理员/员工状态/版本 DRAFT
    Catalog->>DB: 锁版本并事务替换版本及绑定行
    DB-->>UI: 更新后的 DRAFT 版本
    UI->>API: POST 版本 publish
    API->>Catalog: 预检资源状态、Hub Grant/摘要和 SecretRef
    Catalog-->>UI: 发布成功，或逐项返回 missing 依赖
```

员工列表按 `(name.casefold(), employee_id)` 升序排序。游标是 URL-safe Base64 JSON，含最后一条 `name`/`employee_id` 和当前 keyword/status；不透明字段与另一组筛选条件混用时返回 `422 INVALID_ARGUMENT`。员工版本按 `version DESC` 稳定排序，游标包含员工 ID、状态筛选和最后版本号。

列表响应示例：

```json
{
  "items": [
    {
      "id": "01...",
      "tenant_id": "01...",
      "name": "工单助手",
      "role_name": "客服",
      "description": "处理客户工单",
      "status": "ACTIVE",
      "published_to_gallery": false,
      "resource_counts": {"knowledge_base": 0, "skill": 2, "sop": 0, "tool": 1}
    }
  ],
  "next_cursor": "eyJlbXBsb3llZV9pZCI6Li4ufQ"
}
```

## 状态、权限与错误

- 同平台同租户可读取员工资料和版本；跨租户 ID 返回 `404 RESOURCE_NOT_FOUND`，避免资源存在性探测。
- 租户成员可读；创建、PATCH、草稿版本 PUT、发布、归档、部署和回滚要求租户管理员。PATCH/PUT 使用租户身份角色授权，客户端传入的 `tenant_id` 不可扩大权限。
- `DRAFT` 版本可继续配置；`PUBLISHED`/`DEPRECATED` 版本只读。版本详情始终按请求版本号返回，不退化为最新版本。
- 员工状态 `ARCHIVED` 阻止资料修改、新版本创建、发布和新 Deployment；历史版本、已有 Deployment 与 Run 保留供审计。
- PATCH 必须至少包含一个字段；空值字符串被 schema 拒绝。归档为软状态变更，不物理删除版本或来源资源。
- 发布预检逐项验证 Skill/Tool 已发布、ExecutionProfile/ModelRoute/Workspace/Sandbox 存在，REFERENCE Grant 仍有效且 Hub Manifest 摘要匹配，FILE_PACKAGE 清单摘要一致，Tool 所需接收方 SecretRef 已配置。失败保留 DRAFT，并返回 `DEPENDENCY_VALIDATION_ERROR` 与 `details.missing[]`，每项包含 `kind`、资源 `id` 和稳定 `reason`。
- `limit` 越界、游标损坏或游标过滤条件改变返回 `422 INVALID_ARGUMENT`。
- PUBLISHED 版本 PUT 返回 `409 IMMUTABLE_VERSION`；管理员角色缺失返回 `403 AUTHORIZATION_DENIED`；跨租户资源 ID 返回 `404 RESOURCE_NOT_FOUND`。
- DRAFT 版本 `/test` 失败时返回依赖校验或 Runtime 错误；测试结果只返回状态、文本、Usage 和脱敏事件摘要，不产生正式运行记录。
- 旧 `GET /digital-employees` 仍包含 `items`，并增加可空 `next_cursor`；旧客户端继续可读取列表。

| 页面/接口操作 | 成员 | 管理员 | 验证边界 |
|---|---|---|---|
| 列表、详情、版本清单和精确版本读取 | 允许 | 允许 | 服务端由 IdentityContext 取平台/租户；跨租户 ID 隐藏为 404 |
| 创建、PATCH、DRAFT 版本 PUT、版本发布、归档 | 拒绝 | 允许 | 每次写入检查管理员角色；版本 PUT 只能写 DRAFT |
| Skill 文件清单/内容读取 | 允许 | 允许 | 精确 Skill 版本与租户作用域查询 |
| Skill 文件在线编辑、ZIP 预览/提交 | 拒绝 | 允许 | DRAFT、预期 SHA/staging ID、入口和文件大小限制 |
| Tool/Skill 创建、DRAFT Tool 编辑和发布 | 拒绝 | 允许 | Tool/Skill 资源与员工版本均按当前租户隔离 |
| Skill/Tool 定向绑定 | 拒绝 | 仅 DRAFT 可写 | 两个 API 各自只更新本类型绑定表和 Capability slot |
| PUBLISHED 员工版本 fork | 拒绝 | 允许 | 复制完整执行快照到新 DRAFT；原版本不可变 |
| PUBLISHED Skill fork / DRAFT Skill publish | 拒绝 | 允许 | 精确版本操作；Hub REFERENCE 不允许直接 fork |
| 版本 Fake Runtime 测试 | 拒绝 | 允许 | 仅执行已保存 DRAFT，不写 Deployment/Session/Run/Trace |

接口错误结构为 `{ "code": "...", "message": "...", "request_id": "...", "details": {} }`。常见状态码：`404 RESOURCE_NOT_FOUND`（跨租户或隐藏资源）、`403 AUTHORIZATION_DENIED`（成员写入/租户身份不匹配）、`409 IMMUTABLE_VERSION`（写已发布版本）、`409 DEPENDENCY_VALIDATION_ERROR`（发布预检失败并含 `details.missing[]`）、`409 IDEMPOTENCY_CONFLICT`（文件 SHA 或 ZIP staging 修订冲突）、`422 INVALID_ARGUMENT`（筛选、游标、路径或请求体不合法）。Hub 权限与 COPY/REFERENCE 的细分矩阵见[资源中心模型与授权](资源中心模型与授权.md)。

## 验证

```bash
cd backend
set -a
. ../deploy/control-plane/.env
set +a
export MYSQL_HOST=127.0.0.1 MYSQL_PORT=3309 MYSQL_USER=root MYSQL_PASSWORD="$MYSQL_ROOT_PASSWORD"
export AGENT_PLATFORM_MYSQL_TEST_ALLOW_CREATE_DATABASE=1
python -m pytest -q
python -m ruff check src tests
python -m compileall -q src tests

cd ../frontend
npm test
npm run build

cd ..
openspec validate digital-employee-resource-center --strict
```

本轮已验证：后端全量 221 passed、0 skipped（MySQL 8.4.10）；额外覆盖员工 fork 并发版本分配、类型绑定的 MySQL 事务、Skill FILE_PACKAGE fork 的不可变对象引用。Ruff、compileall、OpenSpec strict 通过。前端 11 个测试文件、35 个用例通过，TypeScript/Vite 构建通过。Docker 栈健康；浏览器中确认 Skills 抽屉完成绑定后留在当前 Tab，并能从已发布 Skill 文件页创建新版本；资源中心抽屉显示授权依赖、COPY 后只绑定本地代理；Docker API 将样例 Skill v2 发布为 PUBLISHED。

未验证：生产身份中心、云对象存储和真实 Runtime；它们不在本 Change 范围内。
