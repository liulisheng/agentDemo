# Spec Delta

## Purpose

为代码开发、日志查询和运维 Agent 提供按任务选择的执行环境，使 Agent 可以使用临时或持久工作区，同时保护租户、宿主机、凭据和业务系统。

## ADDED Requirements

### Requirement: Execution environment is selected by profile
系统 SHALL 通过 WorkspaceProfile 和 SandboxProfile 声明 Agent 是否需要文件、代码仓库、远程数据工具、网络、资源限额和持久化工作区。

#### Scenario: Log agent without files
- **WHEN** 日志 Agent 使用 no-files 环境运行
- **THEN** Agent 只能通过授权的日志 API 或 MCP 工具读取数据

### Requirement: Code workspace is isolated
系统 SHALL 为代码 Run 提供租户和项目范围隔离的工作区，并支持指定仓库、提交、分支、读写权限和产物引用。

#### Scenario: Temporary checkout
- **WHEN** 代码 Agent 运行在临时工作区
- **THEN** 系统从指定提交创建隔离目录，完成后返回 diff 或产物引用并按策略清理

### Requirement: Persistent workspace is controlled
系统 SHALL 对持久工作区记录所有者、项目、配额、生命周期、访问策略和租约，Session 不能访问未授权工作区。

#### Scenario: Cross-project workspace access
- **WHEN** Run 请求挂载其他项目的工作区
- **THEN** SandboxProvider 拒绝挂载并记录授权事件

### Requirement: Host execution is policy constrained
系统 SHALL 通过受控 SandboxProvider、资源限额、网络策略和凭据代理执行不可信任务，不允许任务自行修改宿主执行策略。

#### Scenario: Privileged request
- **WHEN** Agent 请求特权容器、宿主路径或长期凭据
- **THEN** 系统拒绝请求并记录安全事件

### Requirement: Operations actions require capability boundaries
系统 SHALL 将运维操作暴露为范围受限的工具能力，并对重启、扩容、删除和配置变更等动作执行审批或策略检查。

#### Scenario: Read-only operations
- **WHEN** 运维 Agent 查询服务状态和日志
- **THEN** 系统允许读取授权资源但不授予写操作权限

### Requirement: Platform owns environment lifecycle
系统 SHALL 负责工作区和沙箱的创建、授权、租约、配额、凭据注入、停止和回收，并向 Runtime Adapter 提供 opaque environment references。

#### Scenario: Start runtime in a workspace
- **WHEN** Run 需要代码工作区
- **THEN** 平台先创建并授权 Workspace/Sandbox，再把 opaque reference 交给 Runtime Adapter

### Requirement: Runtime owns internal environment operations
系统 SHALL 允许目标 Runtime 在已授权环境内执行其支持的文件、命令和工具操作，但平台不复制 Runtime 的内部 shell、编辑器或文件操作实现。

#### Scenario: Runtime edits code
- **WHEN** Codex 或其他 Runtime 在已授权 workspace 中修改文件
- **THEN** 平台记录事件和产物引用，不重新执行同一文件操作

### Requirement: Environment state is not cross-engine portable
系统 SHALL 将 Runtime 私有 workspace/session state 视为引擎相关资源；切换引擎时必须创建新的 Runtime environment 或明确兼容的适配器迁移。

#### Scenario: Engine switch
- **WHEN** 已有 DeepSeek Session 被切换到 Codex
- **THEN** 平台创建新的 Runtime environment，并保留旧 environment_ref 的历史关联
