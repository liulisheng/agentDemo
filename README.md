# Harness Engine 工程

根目录只保存架构文档、部署材料和项目说明。Python 工程位于独立子项目 [harness-engine](harness-engine/)，以后所有 Python 源码、测试、依赖和构建配置都放在该目录内。

## 目录约定

```text
agentDemo/
├── docs/                 # 架构、协议、迁移和验收文档
├── deploy/               # Docker、Kubernetes 部署材料
├── harness-engine/       # Python 子项目
│   ├── pyproject.toml
│   ├── src/harness_engine/
│   └── tests/
└── README.md
```

## 运行 Python 子项目

```powershell
cd harness-engine
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m harness_engine.cli "hello"
```

MySQL 持久化、会话服务、doctor 和扩展教程见
[Python 子项目说明](harness-engine/README.md)。

## 启动本地 MySQL

```powershell
Copy-Item deploy/mysql/.env.example deploy/mysql/.env
# 编辑 deploy/mysql/.env，设置随机密码
docker compose -f deploy/mysql/docker-compose.yml up -d
```

连接与 migration 验收步骤见 [MySQL 开发说明](deploy/mysql/README.md)。

对话消息正文、流式分段、执行轨迹、删除和 GC 的持久化设计及待验证项见
[对话正文持久化设计](docs/superpowers/specs/2026-11-13-conversation-content-persistence-design.md)、
[实施计划](docs/superpowers/plans/2026-11-13-conversation-content-persistence.md)
和[真实验证矩阵](docs/verification-matrix.md)。V002 已在一次性 MySQL 8.4.13 中执行，
只读 doctor 和 V002 代表性检查通过；本地加密 ContentStore、MySQL 上传 intent/消息仓储、
引擎无关的流式 checkpoint、durable tail、worker 过期恢复、会话级删除和 GC 已实现并验证。
离线套件为 162 passed、4 skipped；一次性 MySQL 8.4.13 集成为 14 passed。
DeepSeek 固定 SDK 不提供公开的运行中 delta 回调，适配器声明 `streaming=False`，
因此 DeepSeek 实时流仍未验证。生产对象存储/KMS、跨进程 transcript 读取和 Docker/Kubernetes 沙箱仍需目标环境验收。

## 验证 DeepSeek Harness

最小验证 Demo 和 SDK/runtime 安装说明见
[docs/deepseek-harness-demo.md](docs/deepseek-harness-demo.md)。在 `harness-engine/`
下设置 `PYTHONPATH=src` 后，可以先执行无密钥的启动检查：

```powershell
.\.venv-sdk-check\Scripts\python.exe -m harness_engine.deepseek_demo --startup-only
```

验证已经接入引擎协议后，可以运行：

```powershell
.\.venv-sdk-check\Scripts\harness.exe --engine deepseek "Reply with exactly: cli-deepseek-ok"
```

## 验证 Python MCP 工具

```powershell
cd harness-engine
python -m pip install -e ".[mcp]"
$env:PYTHONPATH = "src"
python -m harness_engine.cli tools list --enable echo
```

DeepSeek 连接方式和 profile 插件安装步骤见
[DeepSeek Harness 最小验证 Demo](docs/deepseek-harness-demo.md)。
