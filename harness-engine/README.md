# Harness Engine Python 子项目

Python 3.12+ 的 engine-neutral Harness 包装层，提供 Fake/DeepSeek 引擎适配、受信工具插件，以及 MySQL 持久化控制面接口。

## 安装

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev,mysql,mcp]"
```

只使用 Fake 引擎时不需要安装 MySQL 或 MCP 依赖。DeepSeek SDK/runtime 的验证安装见仓库根目录的 `docs/sdk-validation.md`。

## 本地 MySQL 8.4

在仓库根目录：

```powershell
Copy-Item deploy/mysql/.env.example deploy/mysql/.env
# 编辑 deploy/mysql/.env，设置随机 MYSQL_PASSWORD 和 MYSQL_ROOT_PASSWORD
docker compose -f deploy/mysql/docker-compose.yml up -d
docker compose -f deploy/mysql/docker-compose.yml ps
```

首次启动时，MySQL 官方镜像在空数据目录自动执行 `docs/migrations/V001__core_schema.sql`。部署还须独立执行 `V002__conversation_messages.sql`；消息/上传 intent 仓储已实现，但必须显式注入到 `PersistentSessionService` 才会保存对话正文。现有 `persistence-demo` 仍只装配控制面仓储。应用不会自动迁移，也不会在 SQLite 上降级运行。

可通过以下环境变量配置连接池：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `MYSQL_HOST` | `127.0.0.1` | 数据库主机 |
| `MYSQL_PORT` | `3306` | TCP 端口 |
| `MYSQL_DATABASE` | `harness` | 数据库名 |
| `MYSQL_USER` | `harness` | 应用账号 |
| `MYSQL_PASSWORD` | 空字符串 | 应用账号密码；部署时必须设置 |
| `MYSQL_POOL_SIZE` | `5` | 连接池容量 |
| `MYSQL_CONNECT_TIMEOUT` | `5` | 建连超时秒数 |

## doctor 和验证

doctor 默认只检查包导入和 migration 文件，不连接数据库、不写数据、不发模型请求：

```powershell
.\.venv\Scripts\python.exe -m harness_engine.cli doctor
.\.venv\Scripts\python.exe -m harness_engine.cli doctor --json
```

显式要求 MySQL 时：

```powershell
.\.venv\Scripts\python.exe -m harness_engine.cli doctor --require-mysql --json
# wheel 未附带 canonical migration 时，可显式指定工作区中的 V001：
.\.venv\Scripts\python.exe -m harness_engine.cli doctor --migration ..\docs\migrations\V001__core_schema.sql
```

结果中的 `PASSED` 表示检查实际通过，`SKIPPED` 表示用户未请求该项，`UNAVAILABLE` 表示请求的依赖或服务不可用。exit code `2` 表示至少一项 `UNAVAILABLE`。
canonical SQL 由仓库中的 V001 和 V002 单独分发，不重复打包进 wheel。若在仓库外安装 wheel 并希望 doctor 检查 migration 文件，使用 `--migration` 指向部署使用的 V001，且 V002 必须位于同一目录；未提供时只跳过本地文件检查，数据库检查仍可独立执行。

运行离线测试和真实 MySQL 集成测试：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
$env:HARNESS_MYSQL_INTEGRATION = "1"
.\.venv\Scripts\python.exe -m pytest tests/test_mysql_integration.py -q
Remove-Item Env:HARNESS_MYSQL_INTEGRATION
```

真实集成测试使用 `MYSQL_*` 环境变量，必须连接一次性 MySQL 8.4 数据库且已依序执行 V001 和 V002；测试会使用随机平台/租户/用户/会话/run ID，并在 `finally` 中清理记录。它覆盖租户范围、联合外键、状态 CHECK、幂等冲突、`SKIP LOCKED`、审计事务、租约过期和旧 fencing token 拒绝，以及消息、chunk、manifest/上传 intent、会话删除和 GC 的事务、隔离和回收恢复。Task 1–5 已在一次性 MySQL 8.4.13 上验证；当前离线套件为 162 passed、4 skipped，Task 5 相关 MySQL 集成为 14 passed，相关改动文件 Ruff 检查通过。

## 使用持久会话服务

### 运行 Fake + MySQL 持久化演示

先启动 Docker Desktop，再在仓库根目录准备 MySQL 本地配置并启动数据库：

```powershell
Copy-Item deploy/mysql/.env.example deploy/mysql/.env
# 编辑 deploy/mysql/.env，填入 MYSQL_PASSWORD 和 MYSQL_ROOT_PASSWORD
docker compose -f deploy/mysql/docker-compose.yml up -d
```

安装 MySQL 可选依赖后运行演示：

```powershell
cd harness-engine
.\.venv\Scripts\python.exe -m pip install -e ".[mysql]"
Get-Content ..\deploy\mysql\.env | ForEach-Object { if ($_ -match '^([^=]+)=(.*)$') { [Environment]::SetEnvironmentVariable($matches[1], $matches[2], 'Process') } }
.\.venv\Scripts\python.exe -m harness_engine.cli persistence-demo
```

也可以直接运行模块：`python -m harness_engine.persistence_demo`。命令会创建随机的演示平台、租户和用户，然后通过现有 MySQL 仓储完成创建会话、提交同一幂等请求两次、worker 执行 Fake 引擎、重新查询并输出终态。输出里的 `idempotent_submission: true` 和 `persisted_run_state: "SUCCEEDED"` 是本次演示的验收点。Fake 返回的文本只在本次命令输出；控制面持久化状态与 `result_ref`，不会把完整 prompt 或模型结果文本写入数据库。演示的随机目录记录和会话/run 记录会保留在本地数据库，方便检查。

同一演示也支持真实 DeepSeek Harness 引擎。默认会使用当前工作目录下的 `.artifacts/local-openai.json`（可用 `--local-config` 指定），并将 Harness home 放在 `.artifacts/dsh-home`：

```powershell
.\.venv-sdk-check\Scripts\python.exe -m pip install -e ".[mysql]"
.\.venv-sdk-check\Scripts\python.exe -m harness_engine.cli persistence-demo --engine deepseek --prompt "用一句话介绍你自己" --max-tokens 256
```

该虚拟环境还需安装已验证匹配的 `deepseek-harness-sdk==0.2.1rc2` 和 `deepseek-harness-runtime-bin==0.2.1rc2`。这条命令会发起真实模型请求，可能产生模型使用费用。完整响应显示在终端；当前 MySQL 保存会话、请求摘要和 run 终态，不保存 prompt 或完整响应正文，也不建立应用可读取的 transcript。Harness home 的引擎原生状态不等于统一消息历史。

演示使用 `MYSQL_HOST`、`MYSQL_PORT`、`MYSQL_DATABASE`、`MYSQL_USER`、`MYSQL_PASSWORD` 等环境变量连接数据库；数据库必须已依序成功执行 V001 和 V002。该命令仅用于本地演示，演示身份种子数据不能替代生产认证或租户创建流程。此演示没有启用下文的正文存储依赖，所以演示请求和结果正文仍不会生成应用 transcript。

```python
from harness_engine import PersistentSessionService
from harness_engine.core import TenantContext
from harness_engine.engines.fake import FakeEngine
from harness_engine.persistence.mysql import (
    MySqlConfig,
    MySqlDatabase,
    MySqlRunRepository,
    MySqlSessionRepository,
)

database = MySqlDatabase.from_config(MySqlConfig.from_env())
service = PersistentSessionService(
    MySqlSessionRepository(database),
    MySqlRunRepository(database),
)
tenant = TenantContext(
    platform_id="从认证上下文取得",
    tenant_id="从认证上下文取得",
    user_id="从认证上下文取得",
    roles=("member",),
    auth_source="oidc",
)
engine = FakeEngine()
session = service.create_session(tenant, engine)
print(session.control_session_id)  # 控制面 ID
print(session.engine_session_id)  # 引擎原生 ID
```

仓储方法都接收显式 `TenantContext`，跨租户 ID 按资源不存在处理。会话列表使用更新时间和 ID 组成的游标。run 请求按 tenant 范围的幂等键和 canonical JSON SHA-256 摘要去重；默认控制面装配不写 prompt 和完整模型结果。启用下文的可选消息依赖后，非流式用户输入与助手可见最终文本会先写入加密 ContentStore，再由 MySQL 原子发布消息与 run 引用。

### 启用非流式对话正文持久化

`LocalContentStore` 是本地开发/验证用的加密文件适配器；生产环境仍需接入有租户隔离、备份和 KMS/密钥轮换策略的对象存储。以下示例从受保护的环境变量读取 32 字节主密钥（64 个十六进制字符），不要把密钥写进源码或配置文件：

```python
import os
from pathlib import Path

from harness_engine import PersistentSessionService
from harness_engine.persistence import (
    LocalContentStore,
    MySqlConfig,
    MySqlContentUploadRepository,
    MySqlDatabase,
    MySqlMessageRepository,
    MySqlRunRepository,
    MySqlSessionRepository,
)

database = MySqlDatabase.from_config(MySqlConfig.from_env())
content_store = LocalContentStore(
    Path(".artifacts/conversation-content"),
    bytes.fromhex(os.environ["HARNESS_CONTENT_MASTER_KEY"]),
)
service = PersistentSessionService(
    MySqlSessionRepository(database),
    MySqlRunRepository(database),
    messages=MySqlMessageRepository(database),
    uploads=MySqlContentUploadRepository(database),
    content_store=content_store,
)
```

启用后，`submit_run()` 返回的 `RunRecord.message_ids` 含已持久化的用户消息 ID；运行前会创建助手 `PENDING` 消息。`MySqlMessageRepository.get/list()` 返回不含正文的消息元数据。正文通过 `ContentStore.get()` 按可信租户上下文和 `content_ref` 读取。MySQL 仅保存引用、SHA-256、字节数、类型和状态。支持可见文本 delta 的引擎会按最多 32 KiB 或 2 秒持久化 checkpoint，使用 `tail_stream(tenant, message_id, last_chunk_sequence)` 从 MySQL durable log 续读；完成时消息引用 manifest，worker 过期时恢复已提交前缀并标为 `PARTIAL`/`UNKNOWN`。引擎只有真正提供运行中 delta 时才应声明 `streaming=True`；DeepSeek 固定 SDK 当前不支持，适配器声明 `streaming=False`。owner/admin 可调用 `delete_session_history()`，删除事务会立即撤销历史读取并将消息、分段、manifest、运行结果、Harness home、workspace 和上传 intent 引用排入 `content_gc_queue`；`MySqlContentGarbageCollector` 按租户和默认 24 小时宽限期扫描孤儿对象，失败可重试，晚到 PUT 会重新入队。不要把引擎私有历史或推理事件直接传入正文存储。

owner/admin 可以修改会话状态或请求取消；取消服务会先持久化 `CANCELLING`，再调用当前活动引擎的 `cancel(native_session_id)`，返回值仅表示引擎确认状态；worker 回报最终结果，或系统回收器在租约过期后将 run 标为 `UNKNOWN`。member 可以新建会话、提交 run 和读取租户会话；只有 `ACTIVE` 会话可以接收或执行新 run；viewer 只有列表读取权；worker 通过显式 `TenantContext(auth_source="worker")` 及 fencing token 更新 run；系统回收器通过 `auth_source="system"` 并将对应会话标为 `INTERRUPTED`。P4 不含跨进程 worker 通知，部署调度层需负责该部分。

`SessionRef.session_id` 保持为原生引擎 session ID，`SessionRecord.control_session_id` 是数据库中的控制面 ID。DeepSeek SDK 当前未验证的 resume/cancel 能力仍明确报 `UnsupportedCapabilityError`；控制面记录持久化不代表底层运行时恢复已经通过。

## 对话正文、流式分段与执行轨迹

MySQL 保存租户范围的消息索引、流式 chunk 序号和对象引用；ContentStore 保存加密的 user prompt、assistant 正文、checkpoint 与 manifest。非流式回复与引擎发出的可见文本 delta 均可持久化，client 使用 durable cursor 续读。系统回收器将已提交的部分回答保留为 `PARTIAL`，run 标为 `UNKNOWN`，不自动重发模型请求。当前固定 DeepSeek SDK 不暴露运行中的文本 delta，适配器因此声明 `streaming=False`；通用流式路径通过 Fake Engine 和 MySQL 8.4 集成验证。未保存独立推理过程，也不能据此推断 Harness SDK 原生历史不包含推理内容。V002、实现边界和验证范围见[MySQL schema](../docs/mysql-schema.md)、[持久化设计](../docs/superpowers/specs/2026-11-13-conversation-content-persistence-design.md)及[真实验证矩阵](../docs/verification-matrix.md)。

## 自定义引擎

自定义引擎实现 `HarnessEngine` 协议；生命周期状态守卫可继承 `LifecycleEngine`：

```python
from datetime import UTC, datetime
from uuid import uuid4

from harness_engine.core import (
    EngineCapabilities,
    EngineRequest,
    EngineResult,
    LifecycleEngine,
    RunStatus,
    SessionRef,
    UnsupportedCapabilityError,
)


class CustomEngine(LifecycleEngine):
    capabilities = EngineCapabilities(resume=True)

    def create_session(self) -> SessionRef:
        self._require_started()
        return SessionRef(f"custom-{uuid4().hex}", "custom")

    def resume_session(self, ref: SessionRef) -> SessionRef:
        self._require_started()
        if ref.engine_name != "custom":
            raise ValueError("session belongs to another engine")
        # 真实引擎必须确认该原生 session 仍存在；不能悄悄创建新 session。
        return ref

    def run(self, request: EngineRequest, on_event=None) -> EngineResult:
        self._require_started()
        session = request.session or SessionRef(
            request.session_id or f"custom-{uuid4().hex}", "custom"
        )
        return EngineResult(
            text=f"custom: {request.prompt}",
            finish_reason="completed",
            completed_at=datetime.now(UTC),
            capabilities=self.capabilities,
            run_id=request.run_id,
            session=session,
            status=RunStatus.SUCCEEDED,
        )

    def cancel(self, session_id: str):
        raise UnsupportedCapabilityError("custom engine cancellation is unavailable")
```

以上仅演示接口形状；正式自定义引擎应在 `tests/` 中复用统一生命周期契约测试，并为真实恢复和取消行为提供自己的证据。

## 工具插件

工具必须通过 `agent_harness.tools` entry point 显式注册并由 allowlist 启用：

```python
# my_tools.py
from harness_engine.tools import ToolDefinition


def calculator_definition():
    def calculate(arguments):
        # 生产实现应使用安全解析器，不要对模型输入调用 eval。
        return {"result": sum(arguments["values"])}

    return ToolDefinition(
        name="sum_values",
        description="Add a list of numeric values.",
        input_schema={
            "type": "object",
            "properties": {"values": {"type": "array", "items": {"type": "number"}}},
            "required": ["values"],
            "additionalProperties": False,
        },
        handler=calculate,
        plugin_name="sample-tools",
    )
```

```toml
# 插件自己的 pyproject.toml
[project.entry-points."agent_harness.tools"]
sum_values = "my_tools:calculator_definition"
```

安装插件后显式检查：

```powershell
python -m harness_engine.cli tools list --enable sum_values
```

MCP 服务使用官方 Python SDK：`python -m pip install -e ".[mcp]"`。DeepSeek 连接本地 MCP 的 profile 安装与调用方法见仓库根目录 `docs/deepseek-harness-demo.md`。工具插件属于受信 Python 代码，不是操作系统沙箱。
